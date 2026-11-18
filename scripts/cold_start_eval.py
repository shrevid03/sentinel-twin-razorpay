"""New-account (cold-start) test: what happens when the accounts we score have little or no history?

Usage:
    python scripts/cold_start_eval.py tfinance ~/fraud-data/tfinance/dataset/tfinance --seeds 5 --condition few
    python scripts/cold_start_eval.py amazon ~/fraud-data/Amazon.mat --seeds 3 --condition none

Same seeds, samples and 60/20/20 splits as tfinance_eval.py / real_data_eval.py. Training and validation accounts
keep their full features. Only the TEST accounts are made "new":
  * few:  they keep only the ~20% least informative features (ranked by single-feature AUC on the training split,
          so no test labels are used); every other feature is set to its training mean.
  * none: every feature is set to its training mean, so the account looks like a blank average account.
Their links are kept, as for a new account that shares a device, IP or counterparty with existing accounts. That
is the best case for the graph: a truly new account would usually also have fewer links.

Models (all trained on full-feature training accounts): class-weighted logistic regression, random forest,
random forest + neighbour features (label-free aggregates and the share of neighbours that are known fraud, from
training labels only), the GAT without edges, and the GAT with edges + skip connection (and + neighbour filter).
With no features, the feature-only models give every new account the same score (AUC 0.5 by construction);
the GAT without edges is then skipped for the same reason.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

import graph_common as gc
from app.gat import GAT

KEEP_FRACTION = 0.2


def rewire(mask, rng):
    """Control: degree-preserving random rewiring (configuration model; self-loops and duplicates dropped).

    Each account keeps (about) its number of links, but who it links to is random, so any signal the GAT gets
    from these edges can only come from degree, not from fraud linking to fraud."""
    iu, ju = np.where(np.triu(mask, k=1))
    stubs = np.concatenate([iu, ju])
    rng.shuffle(stubs)
    u, v = stubs[: len(iu)], stubs[len(iu):]
    out = np.eye(len(mask), dtype=bool)
    out[u, v] = True
    out[v, u] = True
    np.fill_diagonal(out, True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["amazon", "tfinance"])
    ap.add_argument("path")
    ap.add_argument("--condition", choices=["full", "few", "none"], required=True)
    ap.add_argument("--n", type=int, default=2500)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--no-gat", action="store_true", help="feature models only (quick)")
    ap.add_argument("--filter", action="store_true", help="also run GAT + skip + neighbour filter")
    ap.add_argument("--rewire", action="store_true",
                    help="control: replace the edges with a degree-preserving random rewiring (GAT skip and RF+nbr only)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t0 = time.time()
    data = gc.load(a.dataset, a.path)
    print(f"{a.dataset}: cold-start condition '{a.condition}', {a.seeds} seeds, N={a.n}", flush=True)

    runs = []
    for seed in range(a.seeds):
        X, y, mask, tr, va, te = gc.prep(a.dataset, data, seed, a.n)
        if a.rewire:
            real_edges = int(np.triu(mask, k=1).sum())
            mask = rewire(mask, np.random.default_rng(500 + seed))
            print(f"  rewired: {real_edges:,} real edges -> {int(np.triu(mask, k=1).sum()):,} random edges", flush=True)
        Xc = X.copy()
        info = np.array([abs(roc_auc_score(y[tr], X[tr, f]) - 0.5) for f in range(X.shape[1])])
        n_keep = max(1, int(round(KEEP_FRACTION * X.shape[1])))
        kept = np.argsort(info)[:n_keep] if a.condition == "few" else (
            np.arange(X.shape[1]) if a.condition == "full" else np.array([], dtype=int))
        blank = np.setdiff1d(np.arange(X.shape[1]), kept)
        Xc[np.ix_(te, blank)] = X[tr][:, blank].mean(0)
        if a.condition == "none":  # leakage guard: every test row is the same blank vector, in training and scoring
            assert np.allclose(Xc[te], Xc[te][0]), "test rows differ"
        res = {"seed": seed, "kept_features": kept.tolist()}
        print(f"\nseed {seed}: test accounts keep {len(kept)} of {X.shape[1]} features {kept.tolist()}", flush=True)

        lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X[tr], y[tr])
        rf = RandomForestClassifier(n_estimators=200, n_jobs=-1, random_state=seed).fit(X[tr], y[tr])
        res["logreg"] = gc.metrics(y[te], lr.predict_proba(Xc[te])[:, 1])
        res["rf"] = gc.metrics(y[te], rf.predict_proba(Xc[te])[:, 1])
        H = np.column_stack([Xc, gc.nbr_label_free(Xc, mask), gc.nbr_label_aware(mask, y, tr)])
        H_tr = np.column_stack([X[tr], H[tr, X.shape[1]:]])  # training accounts: full own features
        rfn = RandomForestClassifier(n_estimators=200, n_jobs=-1, random_state=seed).fit(H_tr, y[tr])
        res["rf_nbr"] = gc.metrics(y[te], rfn.predict_proba(H[te])[:, 1])
        for m in ("logreg", "rf", "rf_nbr"):
            print(f"  {m:<22} AUC {res[m]['auc']:.4f}  R@5% {res[m]['recall_top5']:.3f}", flush=True)

        if not a.no_gat:
            eye = np.eye(len(y), dtype=bool)
            variants = [("gat_skip", mask, {"skip": True})]
            if a.filter:
                variants.append(("gat_skip_filter", mask, {"skip": True, "filter_keep": 0.5}))
            if a.condition != "none" and not a.rewire:
                variants.insert(0, ("gat_none", eye, {}))
            for name, mk, opt in variants:
                t1 = time.time()
                model = GAT(X.shape[1], hidden=8, heads=4, seed=seed, **opt)
                model.fit(Xc, mk, y, tr, va, epochs=a.epochs, lr=a.lr)
                res[name] = gc.metrics(y[te], model.predict_proba(Xc, mk)[te])
                print(f"  {name:<22} AUC {res[name]['auc']:.4f}  R@5% {res[name]['recall_top5']:.3f}  "
                      f"({time.time() - t1:.0f}s)", flush=True)
        runs.append(res)

    models = [("logreg", "LogReg (features)"), ("rf", "RandomForest (features)"),
              ("rf_nbr", "RandomForest + neighbour features"), ("gat_none", "GAT, no edges"),
              ("gat_skip", "GAT + edges, skip"), ("gat_skip_filter", "GAT + edges, skip+filter")]
    summary, lines = gc.summarise(runs, models)
    print(f"\nSummary: {a.dataset}, new test accounts, condition '{a.condition}'"
          f"{' — CONTROL: edges randomly rewired (degree kept)' if a.rewire else ''} "
          f"(mean ± std over {a.seeds} seeds, N={a.n}, test set = 20%)")
    print("\n".join(lines))
    print(f"  total time {round((time.time() - t0) / 60, 1)} min")
    with open(a.out, "w") as f:
        json.dump({"args": vars(a), "runs": runs, "summary": summary}, f, indent=2, default=float)
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
