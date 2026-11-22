"""Durable auditability: a decision made before a restart replays identically afterwards, from the database alone.

"Restart" = a brand-new Twin and Team on the same database. The new twin is even built from a DIFFERENT seed, so a
replay that secretly re-created the world from the seed could not match. Set TEST_DATABASE_URL=postgresql://... to
run against Postgres."""
import os
import tempfile

import pytest

from app.agents import Team
from app.razorpay import Razorpay, payer_links
from app.store import Store
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]


def _url():
    return os.getenv("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "audit.db")


def _process(url, seed):
    """One 'process': fresh twin trained from a seed, a team on the shared database."""
    t = Twin(seed=seed)
    t.train(30)
    team = Team(t, Razorpay(), store=Store(url))
    team.no_live = True
    return t, team


@pytest.fixture(scope="module")
def before_restart():
    url = _url()
    Store(url).reset()
    t, team = _process(url, seed=3)
    out = team.guarded_checkout(CART)
    rid = out["receipt_id"]
    did = team.receipts[rid]["risk"]["decision_id"]
    rec = t.decisions[did]
    # LATER evidence, in the same process, after the decision: the buyer's account picks up a payer UPI ID that a
    # known-fraud account also uses. A correct replay must ignore it.
    fraudster = sorted(t.known_fraud)[0]
    t.link_payer(fraudster, payer_links({"vpa": "late.mule@okaxis"}), source="later payment (fraud ring)")
    t.link_payer(team.buyer, payer_links({"vpa": "late.mule@okaxis"}), source="later payment pay_LATE01")
    t.flush_events()
    ledger_head = team.ledger.verify()
    return {"url": url, "did": did, "rec": rec, "rid": rid, "ledger": ledger_head, "version": rec["model_version"],
            "in_memory": t.replay(did, durable=False)}


def test_replay_after_restart_is_identical_and_ignores_later_evidence(before_restart):
    b = before_restart
    assert b["in_memory"]["identical"]
    t2, team2 = _process(b["url"], seed=99)  # restart, with a different world in memory
    assert b["did"] not in t2.decisions
    # yet more evidence after the restart, in the new process
    t2.link_payer(team2.buyer, payer_links({"vpa": "late.mule@okaxis"}), source="post-restart payment")
    r = t2.replay(b["did"])
    assert r["replayable"] and r["identical"], r
    assert r["source"].startswith("persisted graph event log")
    assert r["stored"] == {"score": b["rec"]["score"], "outcome": b["rec"]["outcome"]}
    assert r["evidence_identical"] and r["features_identical"]
    assert any(e["provenance"] == "later payment pay_LATE01" for e in r["evidence_added_since"])
    assert r["events_replayed"] > 100


def test_receipts_ledger_and_decision_ids_survive_restart(before_restart):
    b = before_restart
    t2, team2 = _process(b["url"], seed=3)
    assert b["rid"] in team2.receipts  # the receipt survived
    assert team2.receipts[b["rid"]]["risk"]["decision_id"] == b["did"]
    v = team2.ledger.verify()
    assert v["ok"] and v["records"] >= b["ledger"]["records"]  # the hash chain continues across the restart
    assert team2.ledger.records(case_id=b["did"], kind="risk_decision")
    out = team2.guarded_checkout(CART)
    new_did = team2.receipts[out["receipt_id"]]["risk"]["decision_id"]
    assert new_did != b["did"]  # ids never repeat after a restart
    assert team2.audit_store.decision(b["did"])["id"] == b["did"]


def test_missing_model_artifact_is_not_replayable(before_restart):
    b = before_restart
    t2, team2 = _process(b["url"], seed=3)
    team2.audit_store.delete_model(b["version"])
    r = t2.replay(b["did"])
    assert r["replayable"] is False and "not in the artifact store" in r["reason"]
    t2.attach_audit(team2.audit_store)  # re-save the live models (does not bring the deleted one back
    assert t2.replay(b["did"])["replayable"] is (b["version"] in t2.model_versions)  # unless it is the same model)


def test_scenario_decisions_are_durable(before_restart):
    from app.scenario import new_account_joins_ring
    b = before_restart
    t, team = _process(b["url"], seed=3)
    out = new_account_joins_ring(t)
    did = out["pre_capture"]["decision_id"]
    t2, _ = _process(b["url"], seed=5)
    r = t2.replay(did)
    assert r["replayable"] and r["identical"], r


def test_two_writers_share_one_ledger_chain():
    """Found on Postgres: two Team objects on one database used to collide on the next ledger seq."""
    import threading
    url = _url()
    Store(url).reset()
    (_, a), (_, b) = _process(url, seed=3), _process(url, seed=3)
    errs = []

    def write(team, tag):
        try:
            for k in range(15):
                team.ledger.append({"type": "note", "case_id": f"{tag}-{k}", "by": "test"})
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=write, args=(t, n)) for t, n in ((a, "A"), (b, "B"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    v = a.ledger.verify()
    assert v["ok"] and v == b.ledger.verify()
    assert {r["case_id"] for r in a.ledger.records(kind="note")} >= {f"{x}-{k}" for x in "AB" for k in range(15)}
