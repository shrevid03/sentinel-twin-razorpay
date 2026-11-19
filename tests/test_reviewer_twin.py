"""Reviewer twin: learns from the human review queue, predicts before each decision, autopilot off and limited."""
import json

import pytest

from app.agents import CaptureRefused, Team
from app.razorpay import Razorpay
from app.reviewer_twin import NAMES, ReviewerTwin, synthetic_case
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]  # ₹2,699


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(40)
    return t


def _team(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    team.twin.policy = {**team.twin.policy, "review_threshold": 0.0, "block_threshold": 1.01}  # every checkout held
    return team


def test_human_decisions_are_recorded_without_pii(trained_twin):
    team = _team(trained_twin)
    out = team.guarded_checkout(CART)
    assert out["status"] == "held_for_review"
    team.decide_hold(out["hold_id"], False, "asha", "looks odd")
    rt = team.reviewer_twin
    real = [r for r in rt.records if r["source"] == "real"]
    assert len(real) == 1 and real[0]["human_approved"] is False and real[0]["prediction"]
    assert all(isinstance(v, (int, float)) for v in real[0]["features"].values())
    blob = json.dumps(rt.status()) + json.dumps(rt.records)
    acc = team.twin.accounts[team.buyer]
    for raw in (acc.name, acc.email, *acc.pay_ids):
        assert raw not in blob


def test_agreement_uses_predictions_made_before_each_decision(trained_twin):
    rt = _team(trained_twin).reviewer_twin
    f = synthetic_case(__import__("random").Random(5))
    before = rt.predict_features(f)
    rec = rt.observe("hold", {"id": "X-1"}, not (before["decision"] == "approve"), "asha", features=f)
    assert rec["prediction"] == before  # stored prediction is the one made before learning from this decision
    ag = rt.agreement(sources=("real",))
    assert ag["n"] == 1 and ag["rate"] == 0.0  # the human disagreed, and it counts as a miss


def test_prediction_is_deterministic(trained_twin):
    f = synthetic_case(__import__("random").Random(8))
    a, b = ReviewerTwin(_team(trained_twin)), ReviewerTwin(_team(trained_twin))
    assert a.predict_features(f) == b.predict_features(f) == a.predict_features(f)
    assert len(a.w) == len(NAMES)


def test_replay_learns_and_reports_agreement(trained_twin):
    rt = _team(trained_twin).reviewer_twin
    r = rt.replay(30)
    assert r["agreement"]["n"] == 30 and r["agreement"]["rate"] >= 0.6
    assert r["status"]["trained_on"] == {"real": 0, "replay": 30, "seeded": 40}


def test_autopilot_is_off_by_default_and_never_touches_irreversible_actions(trained_twin):
    team = _team(trained_twin)
    rt = team.reviewer_twin
    assert rt.autopilot["enabled"] is False
    out = team.guarded_checkout(CART)
    assert out["status"] == "held_for_review"  # shadow mode: a human decides
    rt.set_autopilot(enabled=True, min_confidence=0.5, max_inr=10 ** 9)
    assert rt.autopilot["min_confidence"] == 0.95 and rt.autopilot["max_inr"] == 12500.0  # clamped
    rt.predict = lambda kind, item: {"p_approve": 0.999, "decision": "approve", "confidence": 0.999, "reasons": [],
                                     "mode": "autopilot"}
    for kind in ("refund", "freeze", "action", "capture"):
        assert rt.try_autopilot(kind, {"id": "ACT-1", "type": kind, "total": 10}) is None


def test_autopilot_release_is_logged_and_can_be_undone(trained_twin):
    team = _team(trained_twin)
    rt = team.reviewer_twin
    rt.set_autopilot(enabled=True)
    rt.predict = lambda kind, item: {"p_approve": 0.99, "decision": "approve", "confidence": 0.99, "reasons": [],
                                     "mode": "autopilot"}
    out = team.guarded_checkout(CART)
    assert out["status"] == "released_by_reviewer_twin" and out["pay_order_id"]
    assert not [r for r in rt.records if r["source"] == "real"]  # it never learns from its own decisions
    assert any(e["reviewer"] == "reviewer twin (autopilot)" for e in team.audit)
    rt.undo(out["hold_id"])
    assert team.holds[out["hold_id"]]["status"] == "rejected"
    with pytest.raises(CaptureRefused):
        team.capture(out["pay_order_id"])  # the cancelled checkout can never be charged
