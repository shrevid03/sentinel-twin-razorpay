"""Replayable risk decisions: evidence timing, as-of graph, two honest decision points, version checks."""
import pytest

from app.agents import CaptureRefused, Team
from app.razorpay import Razorpay, payer_links
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(60)
    for _ in range(5):
        t.tick()
    return t


def _team(t):
    team = Team(t.fork(), Razorpay())
    team.no_live = True
    team.twin.policy = {**team.twin.policy, "review_threshold": 0.5, "block_threshold": 0.8}
    return team


def test_fraud_link_added_after_checkout_does_not_change_the_replay(trained_twin):
    team = _team(trained_twin)
    t = team.twin
    out = team.guarded_checkout(CART)
    did = team.receipts[out["receipt_id"]]["risk"]["decision_id"]
    before = t.decisions[did]
    ring = next(a for a in t.accounts.values() if a.segment == "ring-1")
    with t.provenance("investigator note after checkout"):
        t.accounts[team.buyer].pay_ids.add(min(ring.pay_ids))
    t.freeze(ring.id, "confirmed after checkout")
    for _ in range(3):
        t.tick()
    r = t.replay(did)
    assert r["identical"] and r["replayed"]["outcome"] == before["outcome"]
    assert any("after checkout" in e["provenance"] for e in r["evidence_added_since"])


def test_current_payment_identifiers_cannot_change_the_pre_checkout_decision(trained_twin):
    team = _team(trained_twin)
    t = team.twin
    mule = "mule.pool.99@okicici"
    for k, m in enumerate([a for a in t.accounts.values() if a.id in t.known_fraud][:3]):
        t.link_payer(m.id, payer_links({"vpa": mule}), source=f"prior payment pay_R{k}")
    out = team.guarded_checkout(CART)
    pre = team.receipts[out["receipt_id"]]["risk"]["decision_id"]
    team._demo_payer = {"vpa": mule}
    try:
        team.capture(out["pay_order_id"])
    except CaptureRefused:
        pass
    cap = team.checkouts[out["pay_order_id"]]["pre_capture_decision"]
    assert t.decisions[cap]["point"] == "pre_capture" and t.decisions[pre]["point"] == "pre_checkout"
    upi_pre = [e for e in t.decisions[pre]["evidence"] if e["kind"] == "upi" and "authorized" in e["provenance"]]
    upi_cap = [e for e in t.decisions[cap]["evidence"] if e["kind"] == "upi" and "authorized" in e["provenance"]]
    assert not upi_pre and upi_cap  # the payment's UPI ID only exists for the pre-capture decision
    assert t.replay(pre)["identical"]
    assert t.decisions[cap]["score"] > t.decisions[pre]["score"]


def test_model_version_change_and_loss_are_detected(trained_twin):
    team = _team(trained_twin)
    t = team.twin
    out = team.guarded_checkout(CART)
    did = team.receipts[out["receipt_id"]]["risk"]["decision_id"]
    old = t.decisions[did]["model_version"]
    t.train(20)  # a new model goes live
    r = t.replay(did)
    assert r["model_version"] == old != r["current_model_version"] and r["identical"]  # replayed with the stored model
    t.model_versions.pop(old)
    r = t.replay(did)
    assert not r["replayable"] and "not available" in r["reason"]


def test_every_decision_is_in_the_ledger_with_versions(trained_twin):
    team = _team(trained_twin)
    team.guarded_checkout(CART)
    recs = team.ledger.records(kind="risk_decision")
    assert recs and recs[-1]["model_version"] and recs[-1]["policy_version"] and recs[-1]["point"] == "pre_checkout"
    assert team.ledger.verify()["ok"]


def test_replay_api(monkeypatch):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    sc = c.post("/api/scenario/new-account").json()
    assert sc["pre_checkout"]["replay"]["identical"] and sc["pre_capture"]["pre_checkout_replay"]["identical"]
    assert c.post("/api/decisions/DEC-99999/replay").status_code == 404


def test_forest_engine_scores_and_replays(monkeypatch):
    monkeypatch.setenv("TWIN_ENGINE", "rf_graph")
    t = Twin(seed=3)
    t.train(30)
    assert t.metrics["engine"] == "rf_graph" and "+rf-" in t.model_version
    team = Team(t, Razorpay())
    team.no_live = True
    out = team.guarded_checkout(CART)
    did = team.receipts[out["receipt_id"]]["risk"]["decision_id"]
    assert t.decisions[did]["features"] and t.replay(did)["identical"]
    ring = next(a for a in t.accounts.values() if a.segment == "ring-1")
    s = t.score_order(ring.id, "M-07", 30000, min(ring.devices), min(ring.ips))
    assert s["engine"] == "rf_graph" and s["risk"] > 0.5
