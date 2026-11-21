"""Does the GAT earn its place? A chronological holdout on the twin's order stream.

Usage:
    python scripts/model_trial.py --seeds 3 --ticks 300

For each twin seed:
  1. Train the GAT on the seeded twin (as the app does), then run a live order stream (ordinary traffic plus fresh
     attacks injected over time: device rings, layering cycles, mule fan-out, collusion, card testing, account
     takeover, synthetic identities). Every order is scored at the moment it is placed, so its features use only
     links that existed then.
  2. Fraud labels arrive late, like chargebacks: during the training window only the seeded training labels are
     known; at the end of the training window the fraud accounts seen in it become known labels.
  3. Split by time: first 60% of orders = training, next 20% = validation, last 20% = test.
  4. Models: (a) rules only; (b) random forest on explicit graph features (account features, degree, shared
     identifiers, known-fraud-neighbour share and counts, neighbour aggregates, transfer partners) plus the
     transaction signals; (c) the same random forest plus the GAT's score as one more feature.
  5. Thresholds are frozen on validation so each model sends the same share of orders (1%, 2%, 5%) to manual
     review; then on the test window we report fraud rupees caught, good orders and rupees sent to review or
     blocked (block tier = top 0.5% on validation), and per-decision latency.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
from sklearn.ensemble import RandomForestClassifier

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.money import K  # noqa: E402
from app.twin import Twin  # noqa: E402

BUDGETS = (0.01, 0.02, 0.05)
BLOCK = 0.005


def graph_features(t, account, device, ip):
    """Explicit graph features at this moment (no model output; labels only if already known)."""
    acc = t.accounts[account]
    ids = acc.devices | acc.ips | acc.pay_ids | {device, ip}
    linked = [b for b in t.accounts.values() if b.id != account and (b.devices | b.ips | b.pay_ids) & ids]
    kf = t.known_fraud_signal(account, extra_ids=(device, ip))
    shared = t.shared_identifiers(account, extra_device=device)
    known_shared = sum(sum(1 for b in t.accounts.values() if b.id != account and b.id in t.known_fraud
                           and (s["id"] in b.devices or s["id"] in b.pay_ids)) for s in shared)
    ages = [min(b.age_days, 2000) / 2000 for b in linked] or [0]
    kyc = [float(b.kyc) for b in linked] or [1]
    disp = [float(b.email.endswith(("mailinator.com", "tempmail.dev", "10minute.email"))) for b in linked] or [0]
    partners = t.xfer_nb.get(account, set())
    return [len(linked), np.log1p(len(linked)), kf["share"], kf["known_fraud"], kf["labelled"], len(shared), known_shared,
            float(np.mean(ages)), float(np.min(ages)), float(np.mean(kyc)), float(np.mean(disp)), len(partners),
            sum(1 for p in partners if p in t.known_fraud)]


class Recorder:
    """Wraps place_order: capture the features of every order at the moment it is scored."""

    def __init__(self, t):
        self.t, self.rows = t, []
        self._place = t.place_order
        t.place_order = self.place

    def place(self, account, merchant, amount, device=None, ip=None, source="simulated", fraud=None, **kw):
        t = self.t
        acc = t.accounts[account]
        device = device or min(acc.devices, default=None) or t.new_device()
        ip = ip or min(acc.ips, default=None) or t.new_ip(acc.country)
        if device not in t.devices:
            t.devices.add(device)
        if ip not in t.ips:
            t.ips[ip] = acc.country
        t0 = time.perf_counter()
        s = t.score_order(account, merchant, amount, device, ip)
        t1 = time.perf_counter()
        g = graph_features(t, account, device, ip)
        t2 = time.perf_counter()
        o = self._place(account, merchant, amount, device, ip, source=source, fraud=fraud, **kw)
        self.rows.append({"seq": o["seq"], "amount": o["amount"], "fraud": bool(o["fraud"]), "account": account,
                          "rules": s["txn_score"], "gat": s["gat_risk"], "acc_feats": s["features"][4:21], "graph": g,
                          "txn": [s["txn_score"], s["spend_ratio"], float(device not in acc.devices)],
                          "t_score_ms": (t1 - t0) * 1000, "t_graph_ms": (t2 - t1) * 1000, "source": source})
        return o


def inject(t, r, kind):
    """A fresh attack placed into the LIVE stream (new accounts the model has never seen a label for)."""
    if kind == "ring":
        members, _, _ = t.spawn_ring(size=r.randint(3, 6), label="stream-ring")
        for m in members:
            t.place_order(m.id, r.choice(["M-02", "M-07", "M-09"]), r.uniform(150, 600) * K, source="attack", fraud=True)
    elif kind in ("cycle", "fanout", "collusion"):
        for m in t.spawn_flow_ring(kind, size=r.randint(4, 8), label=f"stream-{kind}", history=False):
            t.place_order(m.id, r.choice(["M-02", "M-07", "M-09"]), r.uniform(100, 500) * K, source="attack", fraud=True)
    elif kind == "card_testing":
        acc = t.new_account(age_days=2, fraud=True, segment="stream-cardtest", disposable=True, kyc=False)
        acc.devices.add(t.new_device())
        acc.ips.add(t.new_ip("VN"))
        for _ in range(r.randint(4, 10)):
            t.place_order(acc.id, "M-07", r.uniform(1, 5) * K, source="attack", fraud=True)
        t.place_order(acc.id, "M-02", r.uniform(300, 900) * K, source="attack", fraud=True)
    elif kind == "takeover":
        v = r.choice([a for a in t.accounts.values() if not a.fraud and a.age_days > 365])
        t.place_order(v.id, "M-02", r.uniform(300, 1500) * K, device=t.new_device(), ip=t.new_ip(r.choice(["RU", "NG"])),
                      source="attack", fraud=True)
    elif kind == "synthetic":
        acc = t.new_account(age_days=r.randint(200, 400), fraud=True, segment="stream-synthetic", kyc=True)
        acc.devices.add(t.new_device())
        acc.ips.add(t.new_ip(acc.country))
        for _ in range(r.randint(2, 5)):
            t.place_order(acc.id, r.choice(list(t.merchants)), r.uniform(15, 60) * K, source="attack", fraud=True)
        t.place_order(acc.id, "M-02", r.uniform(400, 1500) * K, source="attack", fraud=True)


def run_seed(seed, ticks, epochs, fraud_rate=0.02, inject_every=20):
    import random
    t = Twin(seed=seed)
    t.train(epochs)
    rec = Recorder(t)
    r = random.Random(1000 + seed)
    kinds = ["ring", "cycle", "fanout", "collusion", "card_testing", "takeover", "synthetic"]
    train_end_tick = int(ticks * 0.6)
    for k in range(ticks):
        t.tick(6, fraud_rate=fraud_rate)
        if k % inject_every == inject_every // 2:
            inject(t, r, r.choice(kinds))
        if k == train_end_tick:  # chargebacks for the training window arrive: those fraud accounts become known
            seq = t._bump()
            for row in rec.rows:
                if row["fraud"] and row["account"] not in t.known_fraud:
                    t.known_fraud.add(row["account"])
                    t.label_meta[row["account"]] = {"seq": seq, "at": round(t.clock, 2), "prov": "chargeback"}
    return rec.rows, t


def evaluate(rows, seed):
    n = len(rows)
    a, b = int(n * 0.6), int(n * 0.8)
    tr, va, te = rows[:a], rows[a:b], rows[b:]

    def mat(rs, gat):
        return np.array([x["acc_feats"] + x["graph"] + x["txn"] + ([x["gat"]] if gat else []) for x in rs])

    y_tr = np.array([x["fraud"] for x in tr])
    scores = {"rules": {"va": np.array([x["rules"] for x in va]), "te": np.array([x["rules"] for x in te])}}
    lat = {}
    for name, gat in (("rf_graph", False), ("rf_graph_gat", True)):
        rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample",
                                    n_jobs=-1, random_state=seed).fit(mat(tr, gat), y_tr)
        scores[name] = {"va": rf.predict_proba(mat(va, gat))[:, 1], "te": rf.predict_proba(mat(te, gat))[:, 1]}
        one = mat(te[:1], gat)
        t0 = time.perf_counter()
        for _ in range(50):
            rf.predict_proba(one)
        lat[name] = (time.perf_counter() - t0) / 50 * 1000
    y_te = np.array([x["fraud"] for x in te])
    amt = np.array([x["amount"] for x in te])
    jit = np.random.default_rng(seed)  # break ties (rule scores are coarse) so a review budget means what it says
    for k in scores:
        for part in ("va", "te"):
            scores[k][part] = scores[k][part] + jit.uniform(0, 1e-6, size=len(scores[k][part]))
    out = {"orders": {"train": len(tr), "validation": len(va), "test": len(te)},
           "test_fraud_orders": int(y_te.sum()), "test_fraud_inr": float(amt[y_te].sum()), "budgets": {}}
    for bud in BUDGETS:
        out["budgets"][bud] = {}
        for name, s in scores.items():
            thr = np.quantile(s["va"], 1 - bud)  # frozen on validation
            thr_block = np.quantile(s["va"], 1 - BLOCK)
            flag = s["te"] >= thr
            block = s["te"] >= thr_block
            out["budgets"][bud][name] = {
                "flagged_share": float(flag.mean()), "fraud_inr_caught": float(amt[flag & y_te].sum()),
                "fraud_inr_share": float(amt[flag & y_te].sum() / max(amt[y_te].sum(), 1)),
                "fraud_orders_caught": int((flag & y_te).sum()), "good_orders_flagged": int((flag & ~y_te).sum()),
                "good_inr_flagged": float(amt[flag & ~y_te].sum()), "good_orders_blocked": int((block & ~y_te).sum()),
                "good_inr_blocked": float(amt[block & ~y_te].sum())}
    t_score = np.array([x["t_score_ms"] for x in te])  # GAT + rules scoring (build graph + forward pass)
    t_graph = np.array([x["t_graph_ms"] for x in te])  # explicit graph features
    out["latency_ms"] = {
        "rules": {"p50": 0.05, "p95": 0.1, "note": "rules alone are arithmetic on a few counters"},
        "rf_graph": {"p50": float(np.percentile(t_graph, 50) + lat["rf_graph"]),
                     "p95": float(np.percentile(t_graph, 95) + lat["rf_graph"])},
        "rf_graph_gat": {"p50": float(np.percentile(t_graph + t_score, 50) + lat["rf_graph_gat"]),
                         "p95": float(np.percentile(t_graph + t_score, 95) + lat["rf_graph_gat"])},
        "gat_scoring_alone": {"p50": float(np.percentile(t_score, 50)), "p95": float(np.percentile(t_score, 95))}}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--ticks", type=int, default=300)
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--first-seed", type=int, default=3)
    ap.add_argument("--fraud-rate", type=float, default=0.02, help="share of ordinary traffic from known-bad accounts")
    ap.add_argument("--inject-every", type=int, default=20, help="ticks between fresh attacks")
    ap.add_argument("--merge", nargs="*", help="combine result JSONs from parallel runs instead of running")
    ap.add_argument("--out", default="model_trial_results.json")
    a = ap.parse_args()
    t0 = time.time()
    res = []
    if a.merge:
        for f in a.merge:
            for r in json.load(open(f))["seeds"]:
                r["budgets"] = {float(k): v for k, v in r["budgets"].items()}  # JSON keys come back as strings
                res.append(r)
        a.seeds = len(res)
    for s in ([] if a.merge else range(a.seeds)):
        rows, _ = run_seed(a.first_seed + s, a.ticks, a.epochs, a.fraud_rate, a.inject_every)
        r = evaluate(rows, a.first_seed + s)
        r["fraud_share_of_orders"] = sum(x["fraud"] for x in rows) / len(rows)
        res.append(r)
        print(f"seed {a.first_seed + s}: {r['orders']} test fraud {r['test_fraud_orders']} orders / "
              f"₹{r['test_fraud_inr']:,.0f}; fraud = {r['fraud_share_of_orders']:.1%} of all orders", flush=True)
    names = [("rules", "Rules only"), ("rf_graph", "RF + graph features"), ("rf_graph_gat", "RF + graph features + GAT")]
    print(f"\nSummary over {a.seeds} seeds (mean ± std), chronological holdout, thresholds frozen on validation")
    summary = {}
    for bud in BUDGETS:
        print(f"\n  Review volume {bud:.0%} of orders")
        print(f"  {'model':<28}{'flagged':>9}{'fraud ₹ caught':>20}{'share of fraud ₹':>18}{'good flagged':>14}"
              f"{'good ₹ flagged':>18}{'good blocked':>14}")
        for k, label in names:
            v = lambda f: np.array([r["budgets"][bud][k][f] for r in res])  # noqa: E731
            summary.setdefault(str(bud), {})[k] = {f: [float(v(f).mean()), float(v(f).std())]
                                                   for f in res[0]["budgets"][bud][k]}
            print(f"  {label:<28}{v('flagged_share').mean():>8.1%} "
                  f"{v('fraud_inr_caught').mean():>12,.0f} ± {v('fraud_inr_caught').std():<6,.0f}"
                  f"{v('fraud_inr_share').mean():>12.1%} ± {v('fraud_inr_share').std():<4.1%}"
                  f"{v('good_orders_flagged').mean():>9.1f} ± {v('good_orders_flagged').std():<3.1f}"
                  f"{v('good_inr_flagged').mean():>12,.0f} ± {v('good_inr_flagged').std():<5,.0f}"
                  f"{v('good_orders_blocked').mean():>9.1f} ± {v('good_orders_blocked').std():<3.1f}")
    print("\n  Latency per decision (ms, mean over seeds):")
    for k in ("rules", "rf_graph", "rf_graph_gat", "gat_scoring_alone"):
        p50 = np.mean([r["latency_ms"][k]["p50"] for r in res])
        p95 = np.mean([r["latency_ms"][k]["p95"] for r in res])
        print(f"  {k:<28} p50 {p50:8.2f}   p95 {p95:8.2f}")
        summary.setdefault("latency_ms", {})[k] = {"p50": float(p50), "p95": float(p95)}
    print(f"  total time {round((time.time() - t0) / 60, 1)} min")
    with open(a.out, "w") as f:
        json.dump({"args": vars(a), "seeds": res, "summary": summary}, f, indent=2, default=float)
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
