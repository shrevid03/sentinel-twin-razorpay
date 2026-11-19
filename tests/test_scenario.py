"""New account joins a fraud ring: rules approve it, the known-fraud-neighbour signal stops it, nothing live changes."""
import pytest
from fastapi.testclient import TestClient

from app.scenario import new_account_joins_ring
from app.twin import Twin


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(120)
    return t


def test_rules_approve_but_the_graph_engine_stops_the_checkout(trained_twin):
    n_acc, n_ord = len(trained_twin.accounts), len(trained_twin.orders)
    r = new_account_joins_ring(trained_twin)
    assert r["rules_only"]["decision"] == "approve"
    assert r["graph"]["decision"] in ("review", "block")
    assert r["graph"]["checkout_status"] in ("held_for_review", "blocked_by_risk_engine")
    assert r["graph"]["neighbour_signal"]["known_fraud"] >= 1
    assert any("UPI ID" in f for f in r["graph"]["reasons"])
    assert r["receipt"]["outcome"] == r["graph"]["checkout_status"]
    assert r["ring"]["shared_upi"].startswith("UPI-")
    # it ran on a fork: the live twin is untouched
    assert len(trained_twin.accounts) == n_acc and len(trained_twin.orders) == n_ord


def test_scenario_route(monkeypatch):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    from app import main

    res = TestClient(main.app).post("/api/scenario/new-account")
    assert res.status_code == 200 and "rules_only" in res.json() and "graph" in res.json()
