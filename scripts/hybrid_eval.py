"""Hybrid models: give logistic regression and the random forest the graph, three ways.

Usage:
    python scripts/hybrid_eval.py amazon   ~/fraud-data/Amazon.mat                      --seeds 3
    python scripts/hybrid_eval.py tfinance ~/fraud-data/tfinance/dataset/tfinance       --seeds 5
    python scripts/hybrid_eval.py elliptic ~/fraud-data/elliptic                        --epochs 80

Same protocols (seeds, samples, splits; Elliptic's temporal split and local 93 features) as the per-dataset scripts.
Extra features:
  (a) label-free neighbour aggregates: mean and max of neighbours' features, and log degree;
  (b) label-aware: number of neighbours that are training accounts, how many of them are fraud, and the share.
      Only training labels are read. On Elliptic edges never cross time steps, so a test transaction never has a
      neighbour with a known label; (b) would be zero for every test row, so it is left out there.
  (c) the GAT (edges + skip connection) risk score, out-of-fold for training rows: the training split is cut in
      two, a GAT trained on each half scores the other half, and a GAT trained on the full training split scores
      validation and test rows (on Elliptic the halves are alternate time steps). No validation or test labels
      are used anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

import graph_common as gc
from app.gat import GAT


def oof_gat_scores(X, mask, y, tr, va, seed, epochs, lr):
    """Out-of-fold GAT+skip scores for training rows; full-training-split scores for everything else."""
    rng = np.random.default_rng(1000 + seed)
    halves = np.array_split(rng.permutation(tr), 2)
    s = np.zeros(len(y))
    for i in (0, 1):
        model = GAT(X.shape[1], hidden=8, heads=4, seed=seed, skip=True)
        model.fit(X, mask, y, halves[1 - i], va, epochs=epochs, lr=lr)
        s[halves[i]] = model.predict_proba(X, mask)[halves[i]]
    model = GAT(X.shape[1], hidden=8, heads=4, seed=seed, skip=True)
    model.fit(X, mask, y, tr, va, epochs=epochs, lr=lr)
    full = model.predict_proba(X, mask)
    rest = np.setdiff1d(np.arange(len(y)), tr)
    s[rest] = full[rest]
    return s, full


def fit_models(blocks, y, tr, te, seed, rf_kw, res, prefix_rows):
    """blocks: dict name -> feature matrix (all rows). prefix_rows: list of (model key, block names)."""
    for key, names in prefix_rows:
        M = np.column_stack([blocks[b] for b in names])
        if key.startswith("logreg"):
            mu, sd = M[tr].mean(0), M[tr].std(0) + 1e-8
            Z = (M - mu) / sd
            mdl = LogisticRegression(max_iter=3000, class_weight="balanced").fit(Z[tr], y[tr])
            p = mdl.predict_proba(Z[te])[:, 1]
        else:
            mdl = RandomForestClassifier(random_state=seed, n_jobs=-1, **rf_kw).fit(M[tr], y[tr])
            p = mdl.predict_proba(M[te])[:, 1]
        res[key] = gc.metrics(y[te], p)
        res[key]["_p"] = p
        print(f"  {key:<20} AUC {res[key]['auc']:.4f}  F1 {res[key]['f1']:.3f}  R@5% {res[key]['recall_top5']:.3f}",
              flush=True)


ROWS_LABELS = [("logreg", "LogReg (features)"), ("logreg_hybrid", "LogReg + (a)+(b)+(c)"),
               ("rf", "RandomForest (features)"), ("rf_a", "RF + (a) neighbour aggregates"),
               ("rf_ab", "RF + (a) + (b) known-fraud share"), ("rf_c", "RF + (c) GAT score"),
               ("rf_abc", "RF + (a) + (b) + (c)"), ("gat_skip", "GAT + edges, skip (alone)")]


def run_sampled(a):
    data = gc.load(a.dataset, a.path)
    runs = []
    rows = [("logreg", ["x"]), ("logreg_hybrid", ["x", "a", "b", "c"]), ("rf", ["x"]), ("rf_a", ["x", "a"]),
            ("rf_ab", ["x", "a", "b"]), ("rf_c", ["x", "c"]), ("rf_abc", ["x", "a", "b", "c"])]
    for seed in range(a.seeds):
        X, y, mask, tr, va, te = gc.prep(a.dataset, data, seed, a.n)
        print(f"\nseed {seed}: n={len(y)} fraud={y.sum()} train/val/test={len(tr)}/{len(va)}/{len(te)}", flush=True)
        t1 = time.time()
        s, full = oof_gat_scores(X, mask, y, tr, va, seed, a.epochs, a.lr)
        res = {"seed": seed, "gat_seconds": round(time.time() - t1)}
        res["gat_skip"] = gc.metrics(y[te], full[te])
        print(f"  {'gat_skip':<20} AUC {res['gat_skip']['auc']:.4f}  F1 {res['gat_skip']['f1']:.3f}  "
              f"R@5% {res['gat_skip']['recall_top5']:.3f}  (3 GAT fits, {res['gat_seconds']}s)", flush=True)
        blocks = {"x": X, "a": gc.nbr_label_free(X, mask), "b": gc.nbr_label_aware(mask, y, tr), "c": s[:, None]}
        fit_models(blocks, y, tr, te, seed, {"n_estimators": 200}, res, rows)
        for k in list(res):
            if isinstance(res[k], dict):
                res[k].pop("_p", None)
        runs.append(res)
    return runs, ROWS_LABELS


def run_elliptic(a):
    import elliptic_eval as ell
    X_all, y, step, src, dst = ell.load(a.path)
    lab = y >= 0
    tr = lab & np.isin(step, list(ell.TRAIN_STEPS))
    te = lab & np.isin(step, list(ell.TEST_STEPS))
    Xl = X_all[:, :93]
    Zl = (Xl - Xl[tr].mean(0)) / (Xl[tr].std(0) + 1e-8)

    # (a) aggregates over the full transaction graph (undirected), local features only
    n = len(y)
    u, v = np.concatenate([src, dst]), np.concatenate([dst, src])
    A = sp.csr_matrix((np.ones(len(u)), (u, v)), shape=(n, n))
    A.data[:] = 1.0
    deg = np.asarray(A.sum(1)).ravel()
    mean = (A @ Zl) / np.maximum(deg, 1)[:, None]
    mx = pd.DataFrame(Zl[v].astype(np.float32)).groupby(u).max()
    mxa = np.zeros_like(Zl)
    mxa[mx.index.to_numpy()] = mx.to_numpy()
    agg = np.column_stack([mean, mxa, np.log1p(deg)])
    print(f"Elliptic: {n:,} txs; neighbour aggregates built ({agg.shape[1]} columns)", flush=True)

    # (c) GAT + skip: out-of-fold over alternate training steps, full model for test
    rng = np.random.default_rng(a.seed)
    g_tr = [ell.step_graph(t, step, y, src, dst, a.cap, rng) for t in ell.TRAIN_STEPS]
    g_te = [ell.step_graph(t, step, y, src, dst, a.cap, rng) for t in ell.TEST_STEPS]
    s = np.full(n, np.nan)
    for half in (0, 1):
        fit_on = [g for i, g in enumerate(g_tr) if i % 2 != half]
        score = [g for i, g in enumerate(g_tr) if i % 2 == half]
        t1 = time.time()
        print(f"\nGAT+skip on {len(fit_on)} training steps, scoring the other {len(score)}", flush=True)
        model = ell.train_gat(fit_on, Zl, y, a.epochs, a.lr, a.seed, skip=True)
        idx, p = ell.gat_predict(model, score, Zl)
        s[idx] = p
        print(f"  done ({time.time() - t1:.0f}s)", flush=True)
    t1 = time.time()
    print(f"\nGAT+skip on all {len(g_tr)} training steps, scoring the test steps", flush=True)
    model = ell.train_gat(g_tr, Zl, y, a.epochs, a.lr, a.seed, skip=True)
    idx, p = ell.gat_predict(model, g_te, Zl)
    s[idx] = p
    print(f"  done ({time.time() - t1:.0f}s)", flush=True)
    assert not np.isnan(s[tr | te]).any()
    s = np.nan_to_num(s)

    tr_i, te_i = np.where(tr)[0], np.where(te)[0]
    res = {"gat_skip": ell.metrics(y[te], s[te]) | {"_p": s[te]}}
    blocks = {"x": Xl, "a": agg, "c": s[:, None]}
    rows = [("logreg", ["x"]), ("logreg_hybrid", ["x", "a", "c"]), ("rf", ["x"]), ("rf_a", ["x", "a"]),
            ("rf_c", ["x", "c"]), ("rf_ac", ["x", "a", "c"])]
    for key, names in rows:
        M = np.column_stack([blocks[b] for b in names])
        if key.startswith("logreg"):
            Z = (M - M[tr].mean(0)) / (M[tr].std(0) + 1e-8)
            p = LogisticRegression(max_iter=3000, class_weight="balanced").fit(Z[tr_i], y[tr_i]).predict_proba(Z[te_i])[:, 1]
        else:
            p = RandomForestClassifier(n_estimators=50, max_features=50, n_jobs=-1, random_state=a.seed) \
                .fit(M[tr_i], y[tr_i]).predict_proba(M[te_i])[:, 1]
        res[key] = ell.metrics(y[te], p) | {"_p": p}
        print(f"  {key:<14} F1 {res[key]['f1']:.3f}  AUC {res[key]['auc']:.3f}  R@5% {res[key]['recall_top5']:.3f}",
              flush=True)
    te_steps = step[te]
    for key in res:
        p = res[key].pop("_p")
        for period, sel in (("before", te_steps < ell.SHUTDOWN_STEP), ("after", te_steps >= ell.SHUTDOWN_STEP)):
            res[key][f"f1_{period}_shutdown"] = f1_score(y[te][sel], (p[sel] >= 0.5).astype(int), zero_division=0)
    labels = [("logreg", "LogReg, local 93"), ("logreg_hybrid", "LogReg + (a)+(c)"), ("rf", "RandomForest, local 93"),
              ("rf_a", "RF + (a) neighbour aggregates"), ("rf_c", "RF + (c) GAT score"), ("rf_ac", "RF + (a) + (c)"),
              ("gat_skip", "GAT + edges, skip (alone)")]
    print(f"\nSummary: Elliptic illicit class, test steps 35-49, local 93 features, seed {a.seed}, threshold 0.5")
    print(f"  {'model':<32}{'F1':>7}{'Prec':>7}{'Recall':>8}{'AUC':>7}{'R@5%':>7}{'P@5%':>7}{'F1<43':>8}{'F1>=43':>8}")
    for key, label in labels:
        m = res[key]
        print(f"  {label:<32}{m['f1']:>7.3f}{m['precision']:>7.3f}{m['recall']:>8.3f}{m['auc']:>7.3f}"
              f"{m['recall_top5']:>7.3f}{m['precision_top5']:>7.3f}{m['f1_before_shutdown']:>8.3f}{m['f1_after_shutdown']:>8.3f}")
    print("  (b) left out: Elliptic edges never cross time steps, so test txs have no neighbour with a known label")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["amazon", "tfinance", "elliptic"])
    ap.add_argument("path")
    ap.add_argument("--n", type=int, default=2500)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0, help="Elliptic only")
    ap.add_argument("--cap", type=int, default=2500, help="Elliptic only")
    ap.add_argument("--epochs", type=int, default=None, help="default 200 (Amazon, T-Finance) or 80 (Elliptic)")
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    a.epochs = a.epochs or (80 if a.dataset == "elliptic" else 200)
    t0 = time.time()
    if a.dataset == "elliptic":
        out = {"args": vars(a), "results": run_elliptic(a)}
    else:
        runs, labels = run_sampled(a)
        summary, lines = gc.summarise(runs, labels)
        print(f"\nSummary: {a.dataset} hybrids (mean ± std over {a.seeds} seeds, N={a.n}, test set = 20%)")
        print("\n".join(lines))
        out = {"args": vars(a), "runs": runs, "summary": summary}
    print(f"  total time {round((time.time() - t0) / 60, 1)} min")
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2, default=float)
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
