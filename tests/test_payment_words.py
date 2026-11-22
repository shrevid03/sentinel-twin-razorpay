"""The customer is never told "nothing was charged" for a payment that was authorized."""
import json
import tempfile
import os

import pytest

from app import payment_words as words
from app.agents import CaptureRefused, Team
from app.razorpay import Razorpay
from app.store import Store
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(30)
    return t


def _team(twin, review=1.01, block=1.01):
    url = os.getenv("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "w.db")
    s = Store(url)
    s.reset()
    team = Team(twin.fork(), Razorpay(), store=s)
    team.no_live = True
    team.twin.policy = {**team.twin.policy, "review_threshold": review, "block_threshold": block}
    return team


def test_every_state_has_honest_words():
    assert "nothing was charged" in words.NO_PAYMENT.lower()
    for st in ("authorized", "not_captured", "failed", "capture_requested", "unconfirmed", "captured", "refunded"):
        assert "nothing was charged" not in words.for_state(st).lower(), st
    assert "temporary hold or debit" in words.NOT_CAPTURED and "reverses it automatically" in words.NOT_CAPTURED
    assert "5-7 working days" in words.NOT_CAPTURED and "5-7 working days" in words.REFUND_INITIATED
    assert words.for_state("refunded").startswith("Refund initiated")


def test_pre_capture_refusal_never_says_nothing_charged(trained_twin):
    team = _team(trained_twin)
    out = team.guarded_checkout(CART)
    oid = out["pay_order_id"]
    team.twin.policy = {**team.twin.policy, "review_threshold": 0.0, "block_threshold": 1.01}  # pre-capture says review
    with pytest.raises(CaptureRefused) as e:
        team.capture(oid)
    assert "nothing was charged" not in e.value.detail.lower()
    assert "temporary hold or debit" in e.value.detail
    rc = team.receipt_view(team.receipts[out["receipt_id"]])
    assert rc["payment_status"]["state"] == "not_captured"
    assert rc["payment_status"]["customer_message"] == words.NOT_CAPTURED
    assert "nothing was charged" not in json.dumps(rc).lower()
    assert "demo gateway" in rc["payment_status"]["gateway_note"]


def test_no_payment_states_say_nothing_charged(trained_twin):
    team = _team(trained_twin)
    out = team.guarded_checkout(CART)
    rc = team.receipt_view(team.receipts[out["receipt_id"]])
    assert rc["payment_status"]["state"] == "created" and rc["payment_status"]["customer_message"] == words.NO_PAYMENT
    held = _team(trained_twin, review=0.0)
    h = held.guarded_checkout(CART)
    rc = held.receipt_view(held.receipts[h["receipt_id"]])
    assert rc["payment_status"]["state"] == "no_payment" and "nothing was charged" in rc["payment_status"]["customer_message"]


def test_captured_and_refunded_words(trained_twin):
    team = _team(trained_twin)
    out = team.guarded_checkout(CART)
    team.capture(out["pay_order_id"])
    rc = team.receipt_view(team.receipts[out["receipt_id"]])
    assert rc["payment_status"]["state"] == "captured" and rc["payment_status"]["customer_message"] == words.CAPTURED
