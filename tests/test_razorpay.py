"""Razorpay-specific guarantees: Checkout signature verification, payment/order binding, signed webhooks."""
import hashlib
import hmac
import json

import pytest

from app import agents
from app.razorpay import Razorpay, RazorpayError
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]  # ₹2,699 pour-over set


def _sig(secret, msg):
    return hmac.new(secret.encode(), msg if isinstance(msg, bytes) else msg.encode(), hashlib.sha256).hexdigest()


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(40)
    return t


def test_checkout_signature_matches_razorpay_formula(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s3cret")
    rp = Razorpay()
    assert rp.mode == "test"
    good = _sig("s3cret", "order_A|pay_B")
    assert rp.verify_payment_signature("order_A", "pay_B", good)
    assert not rp.verify_payment_signature("order_A", "pay_C", good)  # payment swapped
    assert not rp.verify_payment_signature("order_X", "pay_B", good)  # order swapped
    assert not rp.verify_payment_signature("order_A", "pay_B", "")


def test_live_keys_are_refused_by_default(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s3cret")
    rp = Razorpay()
    assert rp.mode == "demo" and rp.refused_live_key and not rp.live


def test_forged_signature_never_reaches_capture(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s3cret")
    rp = Razorpay()
    hit = []
    monkeypatch.setattr(rp, "_req", lambda *a, **k: hit.append(a) or {})
    with pytest.raises(RazorpayError, match="signature"):
        rp.capture_order("order_A", "pay_B", "forged")
    assert hit == []  # no API call at all on a bad signature


def test_payment_from_another_order_is_refused(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s3cret")
    rp = Razorpay()
    calls = []

    def fake(method, path, json=None):
        calls.append((method, path))
        return {"id": "pay_B", "order_id": "order_OTHER", "status": "authorized", "amount": 268800, "currency": "INR"}
    monkeypatch.setattr(rp, "_req", fake)
    with pytest.raises(RazorpayError, match="does not belong"):
        rp.capture_order("order_A", "pay_B", _sig("s3cret", "order_A|pay_B"))
    assert calls == [("GET", "/v1/payments/pay_B?expand[]=card")]  # fetched, never captured


def test_live_capture_flow_captures_authorized_payment_once(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "s3cret")
    rp = Razorpay()
    calls = []

    def fake(method, path, json=None):
        calls.append((method, path, json))
        if path == "/v1/orders":
            return {"id": "order_A", "amount": json["amount"], "currency": json["currency"], "status": "created"}
        if method == "GET":
            return {"id": "pay_B", "order_id": "order_A", "status": "authorized", "amount": 268800, "currency": "INR"}
        return {"id": "pay_B", "order_id": "order_A", "status": "captured", "amount": 268800, "currency": "INR"}
    monkeypatch.setattr(rp, "_req", fake)
    o = rp.create_order([{"name": "x", "price": 2699.0, "qty": 1, "sku": "S"}], "twin-1")
    assert calls[0][2]["amount"] == 269900 and calls[0][2]["currency"] == "INR"  # ₹2,699 in paise
    assert calls[0][2]["payment"]["capture"] == "manual"
    res = rp.capture_order(o["id"], "pay_B", _sig("s3cret", "order_A|pay_B"))
    assert res["status"] == "COMPLETED" and res["capture_id"] == "pay_B"
    assert [c[:2] for c in calls[1:]] == [("GET", "/v1/payments/pay_B?expand[]=card"), ("POST", "/v1/payments/pay_B/capture")]


def test_demo_checkout_end_to_end(trained_twin):
    team = agents.Team(trained_twin, Razorpay())
    team.twin.policy = {**team.twin.policy, "review_threshold": 1.01, "block_threshold": 1.01}
    out = team.guarded_checkout(CART)
    assert out["pay_order_id"].startswith("order_DEMO") and out["charge"]["currency"] == "INR"
    res = team.capture(out["pay_order_id"])
    assert res["status"] == "COMPLETED" and res["capture_id"].startswith("pay_DEMO")
    tw = next(o for o in team.twin.orders if o.get("pay_order_id") == out["pay_order_id"])
    assert tw["status"] == "captured" and tw["capture_id"] == res["capture_id"]


def test_signed_webhook_is_processed_and_tampered_one_is_not(monkeypatch):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    monkeypatch.setenv("AUTO_TRAFFIC", "0")
    from fastapi.testclient import TestClient

    from app import main
    monkeypatch.setattr(main.razorpay, "webhook_secret", "whsec")
    monkeypatch.setenv("ALLOW_UNVERIFIED_WEBHOOKS", "0")
    order = main.twin.orders[1]
    order["capture_id"], order["status"] = "pay_WEBHOOK1", "captured"
    client = TestClient(main.app)
    raw = json.dumps({"event": "refund.processed",
                      "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_WEBHOOK1"}}}}).encode()
    bad = client.post("/api/razorpay/webhook", content=raw.replace(b"rfnd_1", b"rfnd_2"),
                      headers={"X-Razorpay-Signature": _sig("whsec", raw), "Content-Type": "application/json"})
    assert bad.json() == {"received": True, "verified": False, "processed": False}
    assert order["status"] == "captured"
    ok = client.post("/api/razorpay/webhook", content=raw,
                     headers={"X-Razorpay-Signature": _sig("whsec", raw), "Content-Type": "application/json"})
    assert ok.json()["verified"] is True and ok.json()["processed"] is True
    assert order["status"] == "refunded"


def test_webhook_replay_is_processed_once(monkeypatch):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    monkeypatch.setenv("ALLOW_UNVERIFIED_WEBHOOKS", "1")
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    body = json.dumps({"event": "payment.failed", "payload": {"payment": {"entity": {"order_id": "order_REPLAY1"}}}})
    h = {"content-type": "application/json", "x-razorpay-event-id": "evt_replay_1"}
    first = c.post("/api/razorpay/webhook", content=body, headers=h).json()
    second = c.post("/api/razorpay/webhook", content=body, headers=h).json()
    assert first["processed"] and second.get("duplicate") and not second["processed"]
    assert c.post("/api/razorpay/webhook", content="[]", headers={"content-type": "application/json"}).status_code == 400
