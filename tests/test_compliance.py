"""compliance_rules.yaml is enforced in code; the ledger is append-only and hash-chained; evidence is deterministic."""
import pytest

from app import compliance
from app.agents import Team
from app.compliance import ComplianceError, Ledger
from app.evidence import evidence_pack
from app.razorpay import Razorpay
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(120)
    return t


def test_rules_load_and_every_hook_is_implemented():
    ids = {r["id"] for r in compliance.RULES}
    assert {"RBI-NJ-01", "RBI-AUD-02", "RBI-ACC-03", "RBI-RPT-04"} <= ids
    assert {r["enforce"] for r in compliance.RULES} <= compliance.HOOKS


def test_unknown_hook_refuses_to_load(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text("rules:\n  - id: X-1\n    name: x\n    rule: x\n    enforce: does_not_exist\n")
    with pytest.raises(ValueError):
        compliance.load_rules(str(p))


def test_decisions_need_reason_and_reviewer(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    team.twin.policy = {**team.twin.policy, "review_threshold": 0.0, "block_threshold": 1.01}
    hid = team.guarded_checkout(CART)["hold_id"]
    with pytest.raises(ComplianceError, match="RBI-ACC-03"):
        team.decide_hold(hid, False, "asha", "")
    with pytest.raises(ComplianceError, match="RBI-ACC-03"):
        team.decide_hold(hid, False, "", "looks odd")
    assert team.decide_hold(hid, False, "asha", "looks odd")["status"] == "rejected"


def test_no_machine_can_classify_fraud(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    ring = next(a for a in team.twin.accounts.values() if a.segment == "cycle-1")
    case = team.casebook.open_case(ring.id)
    assert case["recommendation"] in ("review", "auto_clear")  # a model "flag" is always routed to a human
    with pytest.raises(ComplianceError, match="RBI-NJ-01"):
        team.casebook.decide(case["id"], "confirm_fraud", "score is high", "reviewer twin (autopilot)")
    a = team.propose_action("freeze", ring.id, "ring", ["evidence"])
    with pytest.raises(ComplianceError, match="RBI-NJ-01"):
        team.decide_action(a["id"], True, "model", "auto")
    out = team.casebook.decide(case["id"], "confirm_fraud", "closed loop confirmed", "asha")
    assert out["decision"]["reportable"] is True  # RBI-RPT-04
    assert team.twin.accounts[ring.id].status == "frozen"


def test_ledger_is_append_only_and_tamper_evident():
    lg = Ledger(path="")
    lg.append({"type": "recommendation", "case_id": "C1", "recommendation": "review"})
    lg.append({"type": "decision", "case_id": "C1", "human_decision": "clear", "reason": "ok", "reviewer": "asha"})
    assert lg.verify()["ok"] and not hasattr(lg, "update") and not hasattr(lg, "delete")
    rec = lg.records()[0]
    rec["recommendation"] = "clear"  # editing a returned copy does not touch the ledger
    assert lg.verify()["ok"]
    lg._records[0]["recommendation"] = "clear"  # tampering with the stored record breaks the chain
    assert lg.verify() == {"ok": False, "broken_at": 1, "records": 2}


def test_model_and_human_records_are_separate(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    acc = next(a for a in team.twin.accounts.values() if a.segment == "fanout-1")
    case = team.casebook.open_case(acc.id)
    team.casebook.decide(case["id"], "monitor", "watch the hub", "asha")
    recs = team.ledger.records(case_id=case["id"])
    assert [r["type"] for r in recs] == ["recommendation", "decision"]
    assert recs[0]["by"] == "model" and recs[1]["by"] == "human" and recs[1]["reason"] == "watch the hub"


def test_evidence_is_deterministic_and_pointed(trained_twin):
    t = trained_twin.fork()
    acc = next(a for a in t.accounts.values() if a.segment == "cycle-1")
    a, b = evidence_pack(t, acc.id), evidence_pack(t, acc.id)
    assert a == b and a["pattern"] == "cycle"
    assert all(s["evidence"] and s["source"] for s in a["reasoning_steps"])
    legit = next(x for x in t.accounts.values() if not x.fraud and x.id not in t.xfer_nb)
    assert evidence_pack(t, legit.id)["pattern"] in ("unclear", "shared_identifiers")


def test_copilot_answers_only_from_evidence(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    acc = next(a for a in team.twin.accounts.values() if a.segment == "cycle-1")
    case = team.casebook.open_case(acc.id)
    assert "loop" in team.casebook.ask(case["id"], "Is there a money loop?")["answer"]
    assert "don't have that information" in team.casebook.ask(case["id"], "What is their salary?")["answer"]


def test_precedent_surfaces_similar_resolved_case(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    c1 = team.casebook.open_case(next(a for a in team.twin.accounts.values() if a.segment == "cycle-1").id)
    team.casebook.decide(c1["id"], "confirm_fraud", "closed loop", "asha")
    c2 = team.casebook.open_case(next(a for a in team.twin.accounts.values() if a.segment == "cycle-2").id)
    assert c2["precedent"] and c2["precedent"][0]["case_id"] == c1["id"]


def test_copilot_summary_and_case_id_citation(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    case = team.casebook.open_case(next(a.id for a in team.twin.accounts.values() if a.segment == "ring-1"))
    s = team.casebook.ask(case["id"], "Summarize this case")["answer"]
    assert s.startswith("Recommendation:")
    assert team.casebook._cites_only_case_ids(team.casebook.cases[case["id"]], f"{case['id']} concerns {case['account']}")
