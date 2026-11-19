"""Scenario: a brand-new account with clean history joins a known fraud ring through a shared UPI ID.

This is the case the real-data tests point to (docs/REAL_DATA.md, "New accounts"): on its own the account looks
normal, so rules approve it; the graph sees that its UPI ID is shared with accounts already labelled fraud.

Everything runs on a fork of the twin, with a throwaway agent team and the Razorpay client in demo mode, so
nothing touches live state or creates a real order. The agent checkout goes through the same code path as a real
one: mandate check in code, then the risk gate.
"""
from __future__ import annotations

from . import catalog
from .agents import Team
from .razorpay import Razorpay

CART = [{"sku": "SKU-ELE-03", "qty": 1}]  # ₹9,999 mechanical keyboard: inside the ₹12,500 mandate, resellable

OUTCOME = {"awaiting_buyer_approval": "approve", "held_for_review": "review", "blocked_by_risk_engine": "block",
           "rejected_by_mandate_guard": "rejected by mandate"}


def _pick_ring(t):
    """The ring whose most-shared UPI ID links the most accounts the model already knows are fraud."""
    best = None
    for a in t.accounts.values():
        if not a.segment.startswith("ring-"):
            continue
        for pid in sorted(a.pay_ids):
            if not pid.startswith("UPI-"):
                continue
            holders = [b for b in t.accounts.values() if pid in b.pay_ids]
            known = [b for b in holders if b.id in t.known_fraud]
            key = (len(known), len(holders), pid)
            if best is None or key > best[0]:
                best = (key, pid, a.segment, holders, known)
    return best


def new_account_joins_ring(twin, cart=None):
    t = twin.fork()
    pick = _pick_ring(t)
    if pick is None:
        return {"error": "no fraud ring with a shared UPI ID in this twin"}
    _, upi, ring, holders, known = pick

    # a clean-looking newcomer: KYC verified, ordinary email, its own phone and home IP, a little normal history
    acc = t.new_account(name="Rohan Mehta", country="IN", age_days=45, kyc=True)
    acc.devices.add(t.new_device())
    acc.ips.add(t.new_ip("IN"))
    for amount, merchant in ((1899.0, "M-01"), (2499.0, "M-03")):
        t._hist_order(acc, min(acc.devices), min(acc.ips), amount=amount, merchant=merchant)
    acc.pay_ids.add(upi)  # ...but it pays from a UPI ID the ring's mule accounts also use
    t._dirty = True

    team = Team(t, Razorpay())
    team.buyer = acc.id
    team.no_live = True
    cart = cart or CART
    # the rules engine alone: same transaction signals, no graph
    items = [(catalog.by_sku(c["sku"]), c.get("qty", 1)) for c in cart]
    total = round(sum(p["price"] * q for p, q in items), 2)
    merchant = items[0][0]["merchant"]
    s = t.score_order(acc.id, merchant, total, min(acc.devices), min(acc.ips))
    rules_decision = t.decide(s["txn_score"], total, False)
    # the GAT + rules engine without the known-fraud-neighbour signal (how the app scored before it was added)
    g = max(s["gat_risk"], 0.9 * s["device_risk"])
    gat_rules_risk = round(float(1 - (1 - g) * (1 - s["txn_score"] * 0.75)), 3)

    out = team.guarded_checkout(cart)
    receipt = team.receipts.get(out.get("receipt_id"), {})
    explain = t.explain(acc.id, top=5)
    graph_factors = [f for f in s["factors"] if any(k in f for k in ("shares", "known fraud", "GAT", "device shared"))]
    return {
        "account": {"id": acc.id, "name": acc.name, "age_days": acc.age_days, "kyc_verified": acc.kyc,
                    "email_domain": acc.email.split("@")[1], "orders_before": 2, "own_device": True},
        "ring": {"segment": ring, "shared_upi": upi, "accounts_on_upi": len(holders) + 1,
                 "known_fraud_on_upi": len(known)},
        "order": {"cart": cart, "total": total, "merchant": t.merchants[merchant]["name"]},
        "rules_only": {"score": s["txn_score"], "decision": rules_decision,
                       "reasons": [f for f in s["factors"] if f not in graph_factors] or
                                  ["nothing unusual: known device, home IP, normal amount, 45-day-old KYC'd account"]},
        "gat_rules": {"risk": gat_rules_risk, "gat_risk": s["gat_risk"],
                      "decision": t.decide(gat_rules_risk, total, False)},
        "graph": {"risk": s["risk"], "gat_risk": s["gat_risk"], "neighbour_signal": s["neighbour_signal"],
                  "decision": OUTCOME.get(out.get("status"), out.get("status")), "checkout_status": out.get("status"),
                  "reasons": graph_factors, "top_neighbours": explain["top_neighbors"]},
        "receipt": {"id": receipt.get("id"), "outcome": receipt.get("outcome"),
                    "timeline": [e["event"] for e in receipt.get("timeline", [])]},
        "note": "Runs on a fork of the twin with a demo Razorpay client: no live state changes, no real order.",
    }
