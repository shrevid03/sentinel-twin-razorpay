"""No raw emails, phone numbers, UPI ids, card numbers or keys in API responses, the ledger or event log."""
import json
import re

from fastapi.testclient import TestClient

EMAIL = re.compile(r"\b[a-z0-9._%+-]{2,}@[a-z0-9.-]+\.[a-z]{2,}\b", re.I)
PHONE = re.compile(r"(?<![\w.])(?:\+91[- ]?)?[6-9]\d{9}(?!\w)")  # standalone, not inside a hash
KEYS = re.compile(r"rzp_(test|live)_[A-Za-z0-9]{6,}|sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}")


def test_api_responses_carry_no_raw_pii(monkeypatch):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    monkeypatch.setenv("RATE_LIMIT", "0")
    from app import main
    c = TestClient(main.app)
    c.post("/api/chat", json={"text": "Buy the coffee dripper SKU-COF-02", "session": "p1"})
    acc = c.get("/api/accounts").json()[0]["id"]
    bodies = [c.get(p).text for p in ("/api/stats", "/api/accounts", f"/api/account/{acc}", "/api/events",
                                      "/api/orders", "/api/actions", "/api/ledger", "/api/receipts",
                                      "/api/reviewer-twin", "/api/guard", "/api/razorpay/calls", "/api/config")]
    blob = "\n".join(bodies)
    assert not EMAIL.findall(blob), EMAIL.findall(blob)[:3]
    assert not PHONE.findall(blob), PHONE.findall(blob)[:3]
    assert not KEYS.findall(blob)
    assert "okaxis" not in blob  # demo payer UPI id is only ever stored hashed
