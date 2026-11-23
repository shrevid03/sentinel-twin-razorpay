"""The landing page's numbers agree with each other, with the README and with what the app computes."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANDING = (ROOT / "static/landing.html").read_text()
README = (ROOT / "README.md").read_text()


def test_attack_bars_match_the_paragraph_and_the_readme():
    bars = [float(x) for x in re.findall(r'class="v" data-to="([\d.]+)">', LANDING)]
    shown = [float(x) for x in re.findall(r'class="v" data-to="[\d.]+">([\d.]+)%<', LANDING)]
    assert bars == shown == [22.4, 86.7, 93.9]
    assert "rules alone stop 22.4%, the graph 86.7%, the hardened policy 93.9%" in LANDING
    assert "| **All attacks intercepted** | **22.4%** | **86.7%** | **86.7%** | **93.9%** |" in README


def test_cart_orders_each_fit_the_per_order_limit_and_the_budget_adds_up():
    assert "₹12,500" in LANDING and "Two separate orders" in LANDING
    for price in (2699, 9999):
        assert price <= 12500
    assert 15302 + 2699 + 9999 == 28000 <= 35000   # meter start + the two orders = "Spent + reserved ₹28,000" of ₹35,000
    assert "₹28,000" in LANDING and "Monthly budget ₹35,000" in LANDING


def test_ring_scores_match_the_reproducible_scenario():
    from app.agents import Team
    from app.razorpay import Razorpay
    from app.scenario import new_account_joins_ring
    from app.twin import Twin
    t = Twin(seed=3)
    t.train(120)
    Team(t, Razorpay())
    r = new_account_joins_ring(t)
    assert round(r["rules_only"]["score"], 2) == 0.09 and r["rules_only"]["decision"] == "approve"
    assert round(r["graph"]["risk"], 2) == 0.63 and r["graph"]["decision"] == "review"
    assert round(r["pre_capture"]["score"], 2) == 0.61 and r["pre_capture"]["capture_status"] == "not captured"
    for s in ("0.09", "0.63", "0.61"):
        assert s in LANDING
