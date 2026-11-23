"""Risk approval never overrides the buyer's spending limit: the mandate is checked first, in code, and a cart over the
per-order limit is rejected before any risk decision or Razorpay order, however permissive the risk policy is."""
from app.agents import Team
from app.razorpay import Razorpay
from app.twin import Twin


def test_permissive_risk_policy_cannot_override_the_per_order_limit():
    team = Team(Twin(seed=3), Razorpay())
    team.twin.train(20)
    # the most permissive risk policy possible: nothing is ever reviewed or blocked, auto-approve up to ₹2,50,000
    team.twin.policy = {**team.twin.policy, "review_threshold": 1.01, "block_threshold": 1.01, "max_auto_amount": 250000.0}
    team.set_mandate(per_order_limit=12500, monthly_budget=35000)
    r = team.guarded_checkout([{"sku": "SKU-COF-02", "qty": 5}])        # 5 × ₹2,699 = ₹13,495 > ₹12,500
    assert r["status"] == "rejected_by_mandate_guard"
    assert any("per-order limit" in x for x in r["reasons"])
    assert "pay_order_id" not in r
    ok = team.guarded_checkout([{"sku": "SKU-COF-02", "qty": 1}])       # ₹2,699 is within the limit
    assert ok.get("pay_order_id")
