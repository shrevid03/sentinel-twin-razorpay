"""Defense drill (policy hardening on synthetic attack traffic): the adaptive red team finds a gap, the what-if
simulator picks a safer decision policy (the GAT itself is not retrained),
and the *same* seeded attack is replayed to prove it is now stopped.

Everything runs on one frozen fork of the twin, so the before and after numbers compare like with like,
and live traffic keeps flowing untouched until the winning policy is applied.
"""
from __future__ import annotations

import itertools
import time

from .money import K, rs
from .redteam import run_attack, why_adapted

# What a false positive costs the merchant. A legit order sent to manual review costs analyst time plus
# checkout friction; a legit order blocked is a lost sale (the whole order value).
REVIEW_COST_INR = 4.0 * K  # ₹336
MAX_LEGIT_REVIEW_RATE = 0.08  # never accept a policy that reviews more than 8% of good customers

PROBES = [  # round 1: the standard playbooks
    ("fraud_ring", {}), ("card_testing", {}), ("account_takeover", {}),
    ("synthetic_identity", {}), ("device_farm_evasion", {}),
]
ADAPT = {  # round 2: how the red team mutates a playbook that got caught
    "fraud_ring": {"size": 3, "shared_devices": 3, "amount": 60 * K, "age_days": 200},
    "card_testing": {"probes": 4, "final_amount": 120 * K},
    "account_takeover": {"amount": 250 * K, "foreign": False},
    "synthetic_identity": {"warmup_orders": 8, "bust_amount": 250 * K},
    "device_farm_evasion": {"size": 3, "amount": 80 * K},
}
GRID = {
    "review_threshold": [0.5, 0.4, 0.3],
    "block_threshold": [0.8, 0.75],
    "max_auto_amount": [1000 * K, 600 * K, 400 * K],
    "review_new_device_over": [300 * K, 150 * K, 100 * K],
    "review_spend_multiple": [0, 4, 3, 2.5],
}


def _slim(r):
    return {k: r.get(k) for k in ("attack", "params", "seed", "fraud_inr", "stopped_inr", "evaded_inr",
                                  "blocked_inr", "reviewed_inr", "detection_rate", "why_adapted",
                                  "decided_by")} | {"orders": r.get("orders", [])}


def _cost(m):
    return round(m["good_blocked_inr"] + REVIEW_COST_INR * m["good_reviewed"], 2)


def _score(m):
    """Fraud dollars stopped minus what the false positives cost."""
    return round(m["fraud_caught_inr"] - _cost(m), 2)


def _metrics(m):
    legit = max(m["good_total"], 1)
    return {**m, "false_positive_cost_inr": _cost(m), "net_benefit_inr": _score(m),
            "legit_review_rate": round(m["good_reviewed"] / legit, 4),
            "legit_affected": m["good_reviewed"] + m["good_blocked"]}


def defense_drill(twin, seed=7, apply=False, log=None):
    log = log if log is not None else []
    t0 = time.time()
    base = twin.fork()  # one frozen snapshot for the whole drill
    before_policy = dict(base.policy)

    def say(step, text):
        log.append({"step": step, "text": text})

    # ---- 1. probe, then adapt the attacks that were caught
    probes = [run_attack(base, k, p, seed=seed + i) for i, (k, p) in enumerate(PROBES)]
    for r in probes:
        say("attack", f"{r['attack']}: {r['detection_rate']:.0%} of {rs(r['fraud_inr'])} stopped")
    adapted = []
    for i, r in enumerate(probes):
        if r["detection_rate"] >= 0.8 and r["attack"] in ADAPT:
            a = run_attack(base, r["attack"], ADAPT[r["attack"]], seed=seed + 100 + i)
            a["why_adapted"] = why_adapted(r, a)
            a["decided_by"] = "scripted drill (fixed, reproducible playbook)"
            adapted.append(a)
            say("adapt", f"{a['why_adapted']}: {rs(a['evaded_inr'])} got through")
    breaches = [r for r in probes + adapted if r["evaded_inr"] > 0]
    breaches.sort(key=lambda r: -r["evaded_inr"])
    attack_orders = [o for r in probes + adapted for o in r["orders"]]

    # ---- 2. search policies on replayed traffic + the attack orders, pricing false positives
    candidates = []
    keys = list(GRID)
    for combo in itertools.product(*GRID.values()):
        pol = dict(zip(keys, combo))
        if pol["block_threshold"] <= pol["review_threshold"]:
            continue
        m = _metrics(base.what_if(pol, extra_orders=attack_orders)["proposed"])
        candidates.append({"policy": {**before_policy, **pol}, **m})
    baseline = _metrics(base.what_if(before_policy, extra_orders=attack_orders)["proposed"])
    ok = [c for c in candidates if c["legit_review_rate"] <= MAX_LEGIT_REVIEW_RATE]
    # best net benefit; on ties prefer fewer false positives, then the smallest change from today's policy
    ok.sort(key=lambda c: (-c["net_benefit_inr"], c["false_positive_cost_inr"],
                           sum(c["policy"][k] != before_policy.get(k) for k in GRID)))
    best = ok[0] if ok and ok[0]["net_benefit_inr"] > baseline["net_benefit_inr"] else None
    say("search", f"tested {len(candidates)} policies; {len(ok)} keep legit reviews under "
                  f"{MAX_LEGIT_REVIEW_RATE:.0%}")

    # ---- 3. replay the identical breaching attacks under the chosen policy, plus fresh variants
    verify, fresh, fresh_before = [], [], []
    if best:
        hardened = base.fork()
        hardened.policy = dict(best["policy"])
        for r in breaches:
            verify.append(run_attack(hardened, r["attack"], r["params"], seed=r["seed"]))
        # held-out check: same playbooks, new random draws the policy was never tuned on
        # ...run under BOTH policies on the same frozen base, so the held-out comparison is like for like
        for i, r in enumerate(breaches):
            fresh.append(run_attack(hardened, r["attack"], r["params"], seed=seed + 1000 + i))
            fresh_before.append(run_attack(base, r["attack"], r["params"], seed=seed + 1000 + i))
        say("verify", f"same attacks replayed: {rs(sum(v['evaded_inr'] for v in verify))} got through "
                      f"(was {rs(sum(b['evaded_inr'] for b in breaches))})")

    # ---- 4. why a graph? the same orders through rules only, GAT + rules, and GAT + rules hardened
    compare = base.compare_engines(attack_orders, best["policy"] if best else None, review_cost=REVIEW_COST_INR)

    def tot(rows, k):
        return round(sum(r[k] for r in rows), 2)

    applied = False
    if best and apply:
        twin.policy = dict(best["policy"])
        applied = True
        twin._event("policy", "Defense drill applied a hardened policy: "
                    + ", ".join(f"{k}={v}" for k, v in best["policy"].items() if before_policy.get(k) != v),
                    level="warn")
    twin._event("drill", f"Defense drill: red team got {rs(tot(breaches, 'evaded_inr'))} through; "
                         + (f"after the fix {rs(tot(verify, 'evaded_inr'))}" if best else "no safer policy found"),
                level="ok" if best else "warn")
    return {
        "seed": seed, "seconds": round(time.time() - t0, 1), "log": log,
        "review_cost_inr": REVIEW_COST_INR, "max_legit_review_rate": MAX_LEGIT_REVIEW_RATE,
        "probes": [_slim(r) for r in probes], "adapted": [_slim(r) for r in adapted],
        "breaches": [_slim(r) for r in breaches],
        "before_policy": before_policy, "after_policy": best["policy"] if best else None,
        "baseline": baseline, "chosen": best,
        "top_candidates": [{k: c[k] for k in ("policy", "catch_rate", "fraud_caught_inr", "good_reviewed",
                                               "good_blocked_inr", "false_positive_cost_inr", "net_benefit_inr",
                                               "legit_review_rate", "fraud_stopped_per_100_flagged_inr")}
                           for c in ok[:6]],
        "verify": [_slim(r) for r in verify], "fresh": [_slim(r) for r in fresh],
        "fresh_before": [_slim(r) for r in fresh_before], "compare": compare,
        "summary": {
            "breach_evaded_before_inr": tot(breaches, "evaded_inr"),
            "breach_evaded_after_inr": tot(verify, "evaded_inr") if best else None,
            "fresh_evaded_before_inr": tot(fresh_before, "evaded_inr") if best else None,
            "fresh_evaded_after_inr": tot(fresh, "evaded_inr") if best else None,
            "breach_blocked_after_inr": tot(verify, "blocked_inr") if best else None,
            "breach_reviewed_after_inr": tot(verify, "reviewed_inr") if best else None,
            "fresh_attempted_inr": tot(fresh, "fraud_inr") if best else None,
            "catch_rate_before": baseline["catch_rate"], "catch_rate_after": best["catch_rate"] if best else None,
            "per_100_flagged_before_inr": baseline["fraud_stopped_per_100_flagged_inr"],
            "per_100_flagged_after_inr": best["fraud_stopped_per_100_flagged_inr"] if best else None,
            "orders_flagged_before": baseline["orders_flagged"],
            "orders_flagged_after": best["orders_flagged"] if best else None,
            "fp_cost_before_inr": baseline["false_positive_cost_inr"],
            "fp_cost_after_inr": best["false_positive_cost_inr"] if best else None,
            "legit_reviewed_before": baseline["good_reviewed"],
            "legit_reviewed_after": best["good_reviewed"] if best else None,
            "legit_blocked_before_inr": baseline["good_blocked_inr"],
            "legit_blocked_after_inr": best["good_blocked_inr"] if best else None,
            "legit_orders": baseline["good_total"], "applied": applied,
        },
    }
