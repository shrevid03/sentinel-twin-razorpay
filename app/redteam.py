"""Attack library for the red-team agent.

Every attack runs against a *fork* of the digital twin, so the live state is
never polluted. Each returns how much fraud value got through.
"""
from __future__ import annotations

import random

from .money import K

ATTACKS = {
    "fraud_ring": {
        "description": "Freshly created accounts that share a small pool of devices and IPs, buying resellable goods.",
        "params": {"size": "accounts in the ring (3-12)", "shared_devices": "devices shared by the ring (1-6)",
                   "amount": "rupees per order (1680-126000)", "age_days": "account age in days (1-365)"},
    },
    "card_testing": {
        "description": "One account fires many tiny orders to test stolen cards, then a large purchase.",
        "params": {"probes": "number of small test orders (3-30)", "final_amount": "rupees, size of the cash-out order (4200-252000)"},
    },
    "account_takeover": {
        "description": "A long-standing good customer is hijacked: new device, foreign IP, high-value order.",
        "params": {"amount": "rupees, value of the takeover order (4200-420000)", "foreign": "true to use a foreign IP"},
    },
    "synthetic_identity": {
        "description": "An aged, KYC-verified fake identity builds trust with small orders, then busts out.",
        "params": {"warmup_orders": "small trust-building orders (0-10)", "bust_amount": "rupees, final bust-out order (8400-420000)"},
    },
    "device_farm_evasion": {
        "description": "Ring members each use a brand-new device to dodge device links, but share one proxy IP.",
        "params": {"size": "accounts (3-12)", "amount": "rupees per order (1680-126000)"},
    },
    "layering_cycle": {
        "description": "Ordinary-looking accounts pass money round a closed loop (layering), then each cashes out.",
        "params": {"size": "accounts in the loop (4-12)", "amount": "rupees per cash-out order (1680-126000)"},
    },
    "mule_fanout": {
        "description": "A hub pays out to fresh mule accounts in a short window; each mule then cashes out.",
        "params": {"mules": "mule accounts (3-15)", "amount": "rupees per cash-out order (1680-126000)",
                   "new_hub": "true for a fresh hub, false to reuse a known fan-out hub"},
    },
    "collusion_ring": {
        "description": "A cluster of buyers all pay a cluster of colluding sellers (fake demand), then buy.",
        "params": {"buyers": "buyer accounts (2-8)", "amount": "rupees per order (1680-126000)",
                   "fresh_sellers": "true for new sellers, false to reuse known colluding sellers"},
    },
    "prompt_injection": {
        "description": "A poisoned product listing tells the shopping agent to ignore its mandate and buy gift cards.",
        "params": {},
    },
}


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


_DIRECTION = {  # how each parameter change reads in plain words
    ("amount", -1): "smaller amounts", ("final_amount", -1): "smaller cash-out", ("bust_amount", -1): "smaller bust-out",
    ("age_days", 1): "older accounts", ("shared_devices", 1): "spread over more devices (fewer accounts per device)",
    ("shared_devices", -1): "fewer shared devices", ("size", -1): "fewer accounts", ("probes", -1): "fewer test orders",
    ("warmup_orders", 1): "more trust-building orders", ("foreign", -1): "local IP instead of a foreign one",
    ("size", 1): "a longer loop", ("mules", -1): "fewer mules", ("new_hub", 1): "a fresh hub nobody has flagged",
    ("buyers", -1): "fewer buyers", ("fresh_sellers", 1): "new sellers with no history",
}


def _flag(v, default):
    return default if v is None else str(v).lower() not in ("false", "0", "no")


def why_adapted(prev, new):
    """Explain an adaptation: what was intercepted last time, and which knobs moved which way."""
    pp, np_ = prev.get("params") or {}, new.get("params") or {}
    shown = ", ".join(f"{k} {v}" for k, v in pp.items() if k != "victim")
    moves = []
    for k, v in np_.items():
        old = pp.get(k)
        if k == "victim" or old is None or old == v:
            continue
        try:
            d = 1 if float(v) > float(old) else -1
        except (TypeError, ValueError):
            d = -1 if v in (False, "false") else 1
        moves.append(_DIRECTION.get((k, d), f"{k} {old} -> {v}"))
    return (f"{prev['attack']} intercepted at {prev.get('detection_rate', 0):.0%} ({shown}) -> "
            + (", ".join(moves) or "same parameters, new draw"))


def _summary(kind, params, orders, notes=""):
    fraud = [o for o in orders if o["fraud"]]
    total = sum(o["amount"] for o in fraud)
    stopped = sum(o["amount"] for o in fraud if o["decision"] != "approve")  # intercepted: blocked or reviewed
    blocked = sum(o["amount"] for o in fraud if o["decision"] == "block")
    return {
        "attack": kind, "params": params, "orders": [
            {"id": o["id"], "account": o["account"], "amount": o["amount"], "decision": o["decision"],
             "risk": o["risk"], "factors": o["factors"][:3], "fraud": o["fraud"],
             "new_device": o.get("new_device", False), "spend_ratio": o.get("spend_ratio", 0),
             "gat_risk": o.get("gat_risk"), "txn_score": o.get("txn_score"), "attack": kind} for o in orders],
        "fraud_inr": round(total, 2), "stopped_inr": round(stopped, 2), "evaded_inr": round(total - stopped, 2),
        "blocked_inr": round(blocked, 2), "reviewed_inr": round(stopped - blocked, 2),
        "detection_rate": round(stopped / total, 3) if total else 1.0, "notes": notes,
    }


def run_attack(twin, kind, params=None, shopper_guard=None, seed=None):
    """Run one attack on a forked twin. Returns a result summary."""
    p = dict(params or {})
    t = twin.fork()
    if seed is not None:  # a fixed seed replays the identical attack
        t.rng = random.Random(seed)
    r = t.rng
    orders = []
    if kind == "fraud_ring":
        size = int(_clamp(p.get("size"), 3, 12, 6))
        sd = int(_clamp(p.get("shared_devices"), 1, 6, 2))
        amt = _clamp(p.get("amount"), 20 * K, 1500 * K, 400 * K)
        age = int(_clamp(p.get("age_days"), 1, 365, 10))
        members, _, _ = t.spawn_ring(size=size, label="redteam-ring", shared_devices=sd, age_range=(age, age + 5))
        for m in members:
            orders.append(t.place_order(m.id, r.choice(["M-02", "M-07", "M-09"]), amt * r.uniform(0.85, 1.15),
                                        source="redteam"))
        p = {"size": size, "shared_devices": sd, "amount": amt, "age_days": age}
    elif kind == "card_testing":
        n = int(_clamp(p.get("probes"), 3, 30, 12))
        final = _clamp(p.get("final_amount"), 50 * K, 3000 * K, 800 * K)
        acc = t.new_account(age_days=2, fraud=True, segment="redteam-cardtest", disposable=True, kyc=False)
        acc.devices.add(t.new_device())
        acc.ips.add(t.new_ip("VN"))
        for _ in range(n):
            orders.append(t.place_order(acc.id, "M-07", r.uniform(1 * K, 5 * K), source="redteam"))
            t.clock += 0.5
        orders.append(t.place_order(acc.id, "M-02", final, source="redteam"))
        p = {"probes": n, "final_amount": final}
    elif kind == "account_takeover":
        amt = _clamp(p.get("amount"), 50 * K, 5000 * K, 1200 * K)
        foreign = str(p.get("foreign", True)).lower() not in ("false", "0", "no")
        victims = [a for a in t.accounts.values() if not a.fraud and a.age_days > 365]
        victim = r.choice(victims)
        dev = t.new_device()
        ip = t.new_ip(r.choice(["RU", "NG", "RO"]) if foreign else victim.country)
        o = t.place_order(victim.id, "M-02", amt, device=dev, ip=ip, source="redteam", fraud=True)
        orders.append(o)
        p = {"amount": amt, "foreign": foreign, "victim": victim.id}
    elif kind == "synthetic_identity":
        warm = int(_clamp(p.get("warmup_orders"), 0, 10, 4))
        bust = _clamp(p.get("bust_amount"), 100 * K, 5000 * K, 1500 * K)
        acc = t.new_account(age_days=r.randint(200, 400), fraud=True, segment="redteam-synthetic", kyc=True)
        dev, ip = t.new_device(), t.new_ip(acc.country)
        acc.devices.add(dev)
        acc.ips.add(ip)
        for _ in range(warm):
            orders.append(t.place_order(acc.id, r.choice(list(t.merchants)), r.uniform(15 * K, 60 * K), source="redteam"))
            t.clock += 90
        orders.append(t.place_order(acc.id, "M-02", bust, source="redteam"))
        p = {"warmup_orders": warm, "bust_amount": bust}
    elif kind == "device_farm_evasion":
        size = int(_clamp(p.get("size"), 3, 12, 6))
        amt = _clamp(p.get("amount"), 20 * K, 1500 * K, 350 * K)
        proxy = t.new_ip("US")
        for _ in range(size):
            acc = t.new_account(age_days=r.randint(3, 20), fraud=True, segment="redteam-farm", disposable=True)
            acc.devices.add(t.new_device())
            acc.ips.add(proxy)
            orders.append(t.place_order(acc.id, "M-09", amt * r.uniform(0.9, 1.1), source="redteam"))
        p = {"size": size, "amount": amt}
    elif kind in ("layering_cycle", "mule_fanout", "collusion_ring"):
        amt = _clamp(p.get("amount"), 20 * K, 1500 * K, 300 * K)
        if kind == "layering_cycle":
            size = int(_clamp(p.get("size"), 4, 12, 6))
            members = t.spawn_flow_ring("cycle", size=size, label="redteam-cycle", history=False)
            p = {"size": size, "amount": amt}
        elif kind == "mule_fanout":
            n = int(_clamp(p.get("mules"), 3, 15, 8))
            new_hub = _flag(p.get("new_hub"), False)
            hubs = [a for a in t.accounts.values() if a.segment.startswith("fanout-")
                    and any(x["src"] == a.id for x in t.transfers)] if not new_hub else []
            hub = max(hubs, key=lambda a: sum(x["src"] == a.id for x in t.transfers)) if hubs else None
            members = t.spawn_flow_ring("fanout", size=n, label="redteam-fanout", history=False, hub=hub)
            p = {"mules": n, "amount": amt, "new_hub": hub is None}
        else:
            nb = int(_clamp(p.get("buyers"), 2, 8, 4))
            fresh = _flag(p.get("fresh_sellers"), False)
            known = [] if fresh else sorted({x["dst"] for x in t.transfers if x["pattern"] == "collusion"})
            sellers = [t.accounts[s] for s in known] or None
            members = t.spawn_flow_ring("collusion", size=nb * 2 if sellers is None else nb + len(sellers),
                                        label="redteam-collusion", history=False, sellers=sellers)
            members = members[:nb] if sellers else members
            p = {"buyers": nb, "amount": amt, "fresh_sellers": sellers is None}
        for m in members:
            orders.append(t.place_order(m.id, r.choice(["M-02", "M-07", "M-09"]), amt * r.uniform(0.85, 1.15),
                                        source="redteam", fraud=True))
    elif kind == "prompt_injection":
        if shopper_guard is None:
            return {"attack": kind, "error": "no shopper guard available"}
        res = shopper_guard()
        return {"attack": kind, "params": {}, "orders": [], "fraud_inr": res.get("attempted_inr", 0),
                "stopped_inr": res.get("stopped_inr", 0), "evaded_inr": res.get("evaded_inr", 0),
                "detection_rate": res.get("detection_rate", 1.0), "notes": res.get("notes", ""),
                "agent_trace": res.get("trace", [])}
    else:
        return {"attack": kind, "error": f"unknown attack. Choose from {list(ATTACKS)}"}
    out = _summary(kind, p, orders)
    out["seed"] = seed
    return out


def scorecard(results):
    total = sum(r.get("fraud_inr", 0) for r in results)
    stopped = sum(r.get("stopped_inr", 0) for r in results)
    return {"attacks_run": len(results), "fraud_attempted_inr": round(total, 2), "stopped_inr": round(stopped, 2),
            "evaded_inr": round(total - stopped, 2),
            "resilience": round(stopped / total, 3) if total else 1.0,
            "weakest": sorted(({"attack": r["attack"], "detection_rate": r.get("detection_rate", 1)}
                               for r in results if "error" not in r), key=lambda x: x["detection_rate"])[:3]}
