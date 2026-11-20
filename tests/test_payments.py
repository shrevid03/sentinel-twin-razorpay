"""Durable, crash-safe payments: atomic reservations, forward-only states, reconciler, idempotent webhooks.

Runs on SQLite by default; set TEST_DATABASE_URL=postgresql://... to run the same tests against Postgres."""
import json
import os
import tempfile
import threading

import pytest

from app.agents import Team, WorkerCrash
from app.razorpay import Razorpay, RazorpayError
from app.store import Store
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]  # ₹2,699
PG = os.getenv("TEST_DATABASE_URL", "")


def _url(tmp):
    return PG or "sqlite:///" + os.path.join(tmp, "pay.db")


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(30)
    return t


@pytest.fixture
def url():
    s = Store(_url(tempfile.mkdtemp()))
    s.reset()
    return s.url


def _team(twin, url, rp=None):
    team = Team(twin.fork(), rp or Razorpay(), store=Store(url))
    team.no_live = True
    team.twin.policy = {**team.twin.policy, "review_threshold": 1.01, "block_threshold": 1.01}  # approve everything
    return team


def test_concurrent_reservations_only_one_fits(url):
    s = Store(url)
    s.ensure_mandate("B1", 12500, 2000)
    results = []
    barrier = threading.Barrier(2)

    def go():
        st = Store(url)  # separate connections: real concurrency, not a shared lock
        barrier.wait()
        results.append(st.reserve("B1", 1500))
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(r is not None for r in results) == 1
    m = s.mandate("B1")
    assert m["reserved"] == 1500 and m["remaining"] == 500


def test_many_concurrent_reservations_never_overspend(url):
    s = Store(url)
    s.ensure_mandate("B2", 12500, 10000)
    got = []

    def go():
        got.append(Store(url).reserve("B2", 1500))
    ts = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(r is not None for r in got) == 6 and s.mandate("B2")["remaining"] == 1000


def test_states_only_move_forward(url):
    s = Store(url)
    s.ensure_mandate("B3", 12500, 35000)
    rid = s.reserve("B3", 1000)
    s.create_payment("order_X", "B3", 1000, "approved", rid)
    assert s.transition("order_X", "captured", "early webhook")["applied"]  # captured before authorized
    assert not s.transition("order_X", "authorized", "late webhook")["applied"]  # cannot move back
    assert not s.transition("order_X", "captured", "duplicate")["applied"]
    assert s.mandate("B3")["spent"] == 1000  # debited exactly once
    assert s.transition("order_X", "refunded", "refund")["applied"]
    assert not s.transition("order_X", "refunded", "refund again")["applied"]
    assert s.mandate("B3")["spent"] == 0  # credited exactly once


def test_refund_before_capture_nets_to_zero(url):
    s = Store(url)
    s.ensure_mandate("B4", 12500, 35000)
    rid = s.reserve("B4", 1000)
    s.create_payment("order_Y", "B4", 1000, "approved", rid)
    s.transition("order_Y", "refunded", "refund webhook arrived first")
    s.transition("order_Y", "captured", "capture webhook arrived late")
    p, m = s.payment("order_Y"), s.mandate("B4")
    assert p["pay_state"] == "refunded" and m["spent"] == 0 and m["reserved"] == 0


def test_crash_after_razorpay_captured_is_recovered_once(trained_twin, url):
    rp = Razorpay()  # stands in for Razorpay's servers: survives the worker crash
    team = _team(trained_twin, url, rp)
    oid = team.guarded_checkout(CART)["pay_order_id"]
    team._fault = "crash_after_capture"
    with pytest.raises(WorkerCrash):
        team.capture(oid)
    s = Store(url)
    assert s.payment(oid)["pay_state"] == "capture_requested"  # the trail written before calling Razorpay
    buyer = s.payment(oid)["buyer"]
    assert s.mandate(buyer)["spent"] == 0 and s.mandate(buyer)["reserved"] == 2699
    # restart: a new worker with the same database and the same Razorpay
    team2 = _team(trained_twin, url, rp)
    out = team2.reconcile()
    assert out["resolved"][0]["status"] == "COMPLETED"
    assert s.payment(oid)["pay_state"] == "captured"
    assert s.mandate(buyer)["spent"] == 2699 and s.mandate(buyer)["reserved"] == 0
    team2.reconcile()  # running it again changes nothing
    assert s.mandate(buyer)["spent"] == 2699
    assert len([c for c in rp.calls if c["path"].endswith("/capture")]) == 1  # Razorpay asked to capture once
    again = team2.capture(oid)  # the customer retries: told it is complete, never "nothing was charged"
    assert again["status"] == "COMPLETED" and again.get("duplicate")


def test_lost_capture_response_says_confirming_then_reconciles(trained_twin, url, monkeypatch):
    rp = Razorpay()
    team = _team(trained_twin, url, rp)
    oid = team.guarded_checkout(CART)["pay_order_id"]
    real = rp.capture_payment

    def accepted_but_lost(*a, **k):
        real(*a, **k)  # Razorpay captures...
        raise RazorpayError("Razorpay is unreachable (ReadTimeout)")  # ...but the response never arrives
    monkeypatch.setattr(rp, "capture_payment", accepted_but_lost)
    r = team.capture(oid)
    assert r["status"] == "CONFIRMING" and "nothing was charged" not in json.dumps(r).lower()
    monkeypatch.setattr(rp, "capture_payment", real)
    team.reconcile()
    s = Store(url)
    assert s.payment(oid)["pay_state"] == "captured" and s.mandate(s.payment(oid)["buyer"])["spent"] == 2699


def test_crash_before_razorpay_call_is_finished_by_the_reconciler(trained_twin, url, monkeypatch):
    rp = Razorpay()
    team = _team(trained_twin, url, rp)
    oid = team.guarded_checkout(CART)["pay_order_id"]

    def dies(*a, **k):
        raise WorkerCrash("died before the capture call left the process")
    monkeypatch.setattr(rp, "capture_payment", dies)
    with pytest.raises(WorkerCrash):
        team.capture(oid)
    monkeypatch.undo()
    team2 = _team(trained_twin, url, rp)
    team2.reconcile()
    s = Store(url)
    assert s.payment(oid)["pay_state"] == "captured" and s.mandate(s.payment(oid)["buyer"])["spent"] == 2699


def test_abandoned_checkout_releases_its_budget(url):
    s = Store(url)
    s.ensure_mandate("B5", 12500, 3000)
    rid = s.reserve("B5", 2500, ttl=-1)  # already expired
    s.create_payment("order_Z", "B5", 2500, "approved", rid)
    assert s.mandate("B5")["remaining"] == 500
    assert s.expire_reservations() == 1
    assert s.mandate("B5")["remaining"] == 3000 and s.payment("order_Z")["pay_state"] == "cancelled"


def _webhook(c, etype, entity, eid):
    key = "refund" if etype.startswith("refund") else "payment"
    return c.post("/api/razorpay/webhook", content=json.dumps({"event": etype, "payload": {key: {"entity": entity}}}),
                  headers={"content-type": "application/json", "x-razorpay-event-id": eid}).json()


@pytest.mark.parametrize("order", [["authorized", "captured"], ["captured", "authorized"], ["captured", "captured"]])
def test_webhooks_duplicate_and_out_of_order_converge(monkeypatch, order):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    monkeypatch.setenv("ALLOW_UNVERIFIED_WEBHOOKS", "1")
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    main.team.twin.policy = {**main.team.twin.policy, "review_threshold": 1.01, "block_threshold": 1.01}
    oid = main.team.guarded_checkout(CART)["pay_order_id"]
    pid = "pay_WH" + oid[-8:]
    for i, ev in enumerate(order):
        _webhook(c, f"payment.{ev}", {"id": pid, "order_id": oid, "vpa": "x@okaxis"}, f"evt_{oid}_{ev}_{i}")
    _webhook(c, "payment.captured", {"id": pid, "order_id": oid}, f"evt_{oid}_captured_0")  # exact replay
    p = main.store.payment(oid)
    assert p["pay_state"] == "captured"
    caps = [e for e in main.store.events(oid) if e["to_state"] == "captured"]
    assert len(caps) == 1  # one capture recorded, whatever the delivery order
    _webhook(c, "refund.processed", {"id": "rfnd_1", "payment_id": pid}, f"evt_{oid}_refund")
    _webhook(c, "refund.processed", {"id": "rfnd_1", "payment_id": pid}, f"evt_{oid}_refund_again")  # new id, same refund
    assert main.store.payment(oid)["pay_state"] == "refunded"
    assert len([e for e in main.store.events(oid) if e["to_state"] == "refunded"]) == 1


def test_webhook_dedupe_survives_a_restart(url):
    s1, s2 = Store(url), Store(url)
    assert s1.first_delivery("evt_1", "payment.captured")
    assert not s2.first_delivery("evt_1", "payment.captured")  # a new process still knows it
