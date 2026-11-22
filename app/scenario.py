"""Scenario: a clean-looking new account linked to a known fraud ring, caught at two honest decision points.

1. Pre-checkout. At signup the account verified a phone number (OTP) that a known ring's accounts also use. That link
   exists before the checkout, with provenance "signup", so the pre-checkout decision may use it.
2. Pre-capture. A second, clean account passes the pre-checkout check, then pays with a UPI ID that a ring member used
   on an earlier payment. Orders use manual capture, so the payer's identifiers from THIS payment can be checked after
   authorization and before any money is captured; they never influence the earlier pre-checkout decision.

Both decisions are stored with their evidence timing and can be replayed. Everything runs on a fork with a demo
Razorpay client: no live state changes and no real order.
"""
from __future__ import annotations

from . import catalog
from .agents import CaptureRefused, Team
from .razorpay import Razorpay, payer_links

CART = [{"sku": "SKU-ELE-03", "qty": 1}]  # ₹9,999 mechanical keyboard: inside the ₹12,500 mandate, resellable
OUTCOME = {"awaiting_buyer_approval": "approve", "held_for_review": "review", "blocked_by_risk_engine": "block",
           "rejected_by_mandate_guard": "rejected by mandate"}


def _ring_contact(t):
    """A phone/email node shared by two or more accounts the model already knows are fraud."""
    best = None
    for a in t.accounts.values():
        if not a.segment.startswith("ring-"):
            continue
        for pid in sorted(a.pay_ids):
            if pid.startswith("CT-"):
                holders = [b for b in t.accounts.values() if pid in b.pay_ids]
                known = [b for b in holders if b.id in t.known_fraud]
                key = (len(known), len(holders), pid)
                if best is None or key > best[0]:
                    best = (key, pid, a.segment, holders, known)
    return best


def _evidence_view(dec):
    return [{k: e[k] for k in ("item", "kind", "available_at", "provenance")} for e in dec["evidence"]
            if e["kind"] in ("contact", "upi", "card", "known-fraud label", "device")]


def _new_account(t, name, prov):
    with t.provenance(prov):
        acc = t.new_account(name=name, country="IN", age_days=45, kyc=True)
        acc.devices.add(t.new_device())
        acc.ips.add(t.new_ip("IN"))
    with t.provenance("prior order history"):
        for amount, merchant in ((1899.0, "M-01"), (2499.0, "M-03")):
            t._hist_order(acc, min(acc.devices), min(acc.ips), amount=amount, merchant=merchant)
    t._dirty = True
    return acc


def new_account_joins_ring(twin, cart=None):
    t = twin.fork(durable=True)  # its decisions are persisted (child epoch of the live event log)
    forks = twin.__dict__.setdefault("replay_forks", {})
    n = twin.audit.next("scenario") if twin.audit is not None else len(forks) + 1
    t.decision_prefix = f"SCN{n}-"
    forks[t.decision_prefix] = t  # so "Replay decision" can find these decisions later
    while len(forks) > 5:
        forks.pop(next(iter(forks)))
    pick = _ring_contact(t)
    if pick is None:
        return {"error": "no fraud ring with a shared phone/email in this twin"}
    _, contact, ring, holders, known = pick
    cart = cart or CART
    items = [(catalog.by_sku(c["sku"]), c.get("qty", 1)) for c in cart]
    total = round(sum(p["price"] * q for p, q in items), 2)
    merchant = items[0][0]["merchant"]

    # ---- 1. pre-checkout: a link the backend verified at signup
    acc = _new_account(t, "Rohan Mehta", "signup")
    with t.provenance("signup: phone verified by OTP"):
        acc.pay_ids.add(contact)
    team = Team(t, Razorpay())
    team.buyer, team.no_live = acc.id, True
    s = t.score_order(acc.id, merchant, total, min(acc.devices), min(acc.ips))
    rules_decision = t.decide(s["txn_score"], total, False)
    g = max(s["gat_risk"], 0.9 * s["device_risk"])
    gat_rules_risk = round(float(1 - (1 - g) * (1 - s["txn_score"] * 0.75)), 3)
    out = team.guarded_checkout(cart)
    rc = team.receipts.get(out.get("receipt_id"), {})
    dec1 = t.decisions[rc["risk"]["decision_id"]]
    graph_factors = [f for f in s["factors"] if any(k in f for k in ("shares", "known fraud", "GAT", "device shared"))]

    # ---- 2. pre-capture: the payer's UPI ID from this payment, checked after authorization, before capture
    mule_vpa = "mule.pool.17@okicici"
    for k, member in enumerate((known or holders)[:3]):  # a mule UPI ID the ring's accounts have paid from before
        t.link_payer(member.id, payer_links({"vpa": mule_vpa}), source=f"prior payment pay_RING00{k + 1} (ring member)")
    acc2 = _new_account(t, "Kavya Rao", "signup")
    team2 = Team(t, Razorpay())
    team2.buyer, team2.no_live = acc2.id, True
    out2 = team2.guarded_checkout(cart)
    rc2 = team2.receipts.get(out2.get("receipt_id"), {})
    dec2_pre = t.decisions[rc2["risk"]["decision_id"]]
    capture = {"status": None, "message": None, "decision": None}
    if out2.get("pay_order_id"):
        team2._demo_payer = {"vpa": mule_vpa, "contact": "+919812300017"}
        try:
            r = team2.capture(out2["pay_order_id"])
            capture.update(status=r.get("status"), message="captured")
        except CaptureRefused as e:
            capture.update(status="not captured", message=e.detail)
        did = team2.checkouts[out2["pay_order_id"]].get("pre_capture_decision")
        capture["decision"] = t.decisions[did] if did else None

    pre = capture["decision"]
    return {
        "account": {"id": acc.id, "name": acc.name, "age_days": acc.age_days, "kyc_verified": acc.kyc,
                    "email_domain": acc.email.split("@")[1], "orders_before": 2, "own_device": True},
        "ring": {"segment": ring, "shared_contact": contact, "accounts_on_contact": len(holders) + 1,
                 "known_fraud_on_contact": len(known), "shared_upi": None, "accounts_on_upi": len(holders) + 1,
                 "known_fraud_on_upi": len(known)},
        "order": {"cart": cart, "total": total, "merchant": t.merchants[merchant]["name"]},
        "rules_only": {"score": s["txn_score"], "decision": rules_decision,
                       "reasons": [f for f in s["factors"] if f not in graph_factors] or
                                  ["nothing unusual: known device, home IP, normal amount, 45-day-old KYC'd account"]},
        "gat_rules": {"risk": gat_rules_risk, "gat_risk": s["gat_risk"], "decision": t.decide(gat_rules_risk, total, False)},
        "graph": {"risk": s["risk"], "gat_risk": s["gat_risk"], "neighbour_signal": s["neighbour_signal"],
                  "decision": OUTCOME.get(out.get("status"), out.get("status")), "checkout_status": out.get("status"),
                  "reasons": graph_factors, "top_neighbours": t.explain(acc.id, top=5)["top_neighbors"]},
        "pre_checkout": {"decision_id": dec1["id"], "decided_at": dec1["decided_at"], "outcome": dec1["outcome"],
                         "score": dec1["score"], "evidence": _evidence_view(dec1), "replay": t.replay(dec1["id"])},
        "pre_capture": {"account": acc2.id, "name": acc2.name, "pre_checkout_outcome": dec2_pre["outcome"],
                        "pre_checkout_decision_id": dec2_pre["id"], "capture_status": capture["status"],
                        "message": capture["message"],
                        "decision_id": pre["id"] if pre else None, "outcome": pre["outcome"] if pre else None,
                        "score": pre["score"] if pre else None, "factors": pre["factors"] if pre else [],
                        "evidence": _evidence_view(pre) if pre else [],
                        "pre_checkout_replay": t.replay(dec2_pre["id"])},
        "receipt": {"id": rc.get("id"), "outcome": rc.get("outcome"),
                    "timeline": [e["event"] for e in rc.get("timeline", [])]},
        "note": "Runs on a fork of the twin with a demo Razorpay client: no live state changes, no real order.",
    }
