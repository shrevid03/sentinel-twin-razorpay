"""Parallel requests cannot double-capture, double-release or double-count spend."""
import os
import threading

from app.agents import Team
from app.razorpay import Razorpay
from app.twin import Twin


def _team():
    t = Twin(seed=3)
    t.train(20)
    pg = os.getenv("TEST_DATABASE_URL")
    if pg:  # the same races against Postgres: separate pooled connections, row locks, conditional UPDATEs
        from app.store import Store
        s = Store(pg)
        s.reset()
        return Team(t, Razorpay(), store=s)
    return Team(t, Razorpay())


def _parallel(fn, n=12):
    out, errs = [], []

    def run():
        try:
            out.append(fn())
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out, errs


def test_parallel_captures_charge_once():
    team = _team()
    team.twin.policy = {**team.twin.policy, "review_threshold": 1.01, "block_threshold": 1.01}
    oid = team.guarded_checkout([{"sku": "SKU-COF-02", "qty": 1}])["pay_order_id"]
    pid = team.pp.demo_pay(oid)["razorpay_payment_id"]
    out, errs = _parallel(lambda: team.capture(oid, pid, "demo"))
    assert not errs and sum(not r.get("duplicate") for r in out) == 1
    assert team.mandate["spent"] == 2699.0


def test_parallel_hold_decisions_happen_once():
    team = _team()
    team.twin.policy = {**team.twin.policy, "review_threshold": 0.0, "block_threshold": 1.01}
    hid = team.guarded_checkout([{"sku": "SKU-COF-02", "qty": 1}])["hold_id"]
    out, errs = _parallel(lambda: team.decide_hold(hid, True, "asha", "known customer"))
    assert not errs and sum(not r.get("duplicate") for r in out) == 1
    assert len([c for c in team.checkouts.values()]) == 1


def test_parallel_scoring_and_traffic_do_not_corrupt_the_twin():
    team = _team()
    out, errs = _parallel(lambda: (team.twin.tick(3), team.twin.stats(), team.twin.flagged(5)), n=8)
    assert not errs and team.twin.stats()["orders"] > 0
