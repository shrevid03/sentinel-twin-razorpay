"""Nothing crashes when the LLM, Razorpay or the network is down, or keys are missing or wrong."""
import pytest

from app.agents import Team
from app.razorpay import Razorpay, RazorpayError
from app.twin import Twin


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(40)
    return t


class _DeadClient:
    def __getattr__(self, name):
        raise ConnectionError("LLM provider unreachable")


def test_llm_outage_falls_back_everywhere(trained_twin):
    team = Team(trained_twin.fork(), Razorpay())
    team.brain.client = _DeadClient()  # pretend a key is configured but every call fails
    team.brain.provider = "gemini"
    assert team.brain.live
    assert team.concierge("s", "What's the status of the twin?", "en-IN")["reply"]
    assert team.investigate(None, [])["verdict"]
    assert team.red_team([])["scorecard"]
    case = team.casebook.open_case(next(a.id for a in team.twin.accounts.values() if a.segment == "cycle-1"))
    assert team.casebook.ask(case["id"], "Is there a money loop?")["answer"]


def test_razorpay_unreachable_is_a_clean_error(monkeypatch, trained_twin):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_dummy123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("RAZORPAY_API_BASE", "http://127.0.0.1:9")  # nothing listens here
    rp = Razorpay()
    assert rp.live
    with pytest.raises(RazorpayError, match="unreachable"):
        rp.create_order([{"name": "x", "price": 100.0, "qty": 1, "sku": "S"}], "ref")
    team = Team(trained_twin.fork(), rp)
    team.twin.policy = {**team.twin.policy, "review_threshold": 1.01, "block_threshold": 1.01}
    with pytest.raises(RazorpayError):
        team.guarded_checkout([{"sku": "SKU-COF-02", "qty": 1}])
    assert team.mandate["spent"] == 0  # nothing was charged or counted


def test_live_keys_never_used_without_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_ABCDEF")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "x")
    monkeypatch.delenv("RAZORPAY_ALLOW_LIVE", raising=False)
    assert Razorpay().mode == "demo"


def test_api_survives_razorpay_outage(monkeypatch):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.post("/api/chat", json={"text": "Buy the coffee dripper SKU-COF-02", "session": "o1"})
    assert r.status_code == 200 and r.json()["reply"]
    assert c.post("/api/razorpay/orders/order_UNKNOWN/capture", json={}).status_code in (400, 404, 409, 502)
