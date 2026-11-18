"""Evaluate the Sentinel Twin GAT on the real Amazon fraud graph (CARE-GNN / Dou et al. 2020).

Usage:
    python scripts/real_data_eval.py ~/fraud-data/Amazon.mat --n 2500 --seeds 3 --epochs 200

Amazon.mat holds 11,944 users with 25 handcrafted features, a fraud label and three
relations: U-P-U (reviewed the same product), U-S-U (same star rating within a week)
and U-V-U (top-5% mutual review-text similarity). Following the CARE-GNN protocol only
users with index >= 3305 are used (the first 3,305 are unlabelled).

GAT variants added after the first runs (app/gat.py options, both off by default): "skip" adds each node's own
transformed features to the attention output (A @ Z + Z); "filter" keeps, for each node, the half of its neighbours
whose label-aware score (a logistic regression fitted on training nodes only) is closest to its own (CARE-GNN style).

For each seed a stratified sample of N labelled users is drawn, features are
standardised, and a 60/20/20 split is used. Models:
  * logreg      class-weighted numpy logistic regression on features only
  * gat_none    the app's GAT with an identity mask (no edges, features only)
  * gat_upu / gat_usu / gat_uvu / gat_all   GAT on each relation's induced subgraph
                (| identity) and on their union
Metrics: test AUC and recall/precision when reviewing the top 5% and 10% riskiest users.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import scipy.io
import scipy.sparse as sp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.gat import GAT, auc  # noqa: E402

FIRST_LABELLED = 3305
RELATIONS = [("upu", "net_upu", "U-P-U"), ("usu", "net_usu", "U-S-U"), ("uvu", "net_uvu", "U-V-U")]
REVIEW_PCTS = (0.05, 0.10)


def load(path):
    d = scipy.io.loadmat(path)
    X = d["features"]
    X = X.toarray() if sp.issparse(X) else np.asarray(X)
    y = np.asarray(d["label"]).ravel().astype(int)
    nets = {key: sp.csr_matrix(d[name]) for key, name, _ in RELATIONS}
    keep = np.arange(FIRST_LABELLED, len(y))
    X, y = X[keep].astype(float), y[keep]
    nets = {k: A[keep][:, keep] for k, A in nets.items()}
    return X, y, nets


def relation_stats(y, nets):
    lines = []
    for key, _, label in RELATIONS:
        A = sp.triu(nets[key], k=1).tocoo()
        same = float(np.mean(y[A.row] == y[A.col])) if A.nnz else float("nan")
        lines.append(f"  {label}: {A.nnz:,} edges, same-label edge share {same:.3f}")
    A = sp.triu(sum(nets.values()), k=1).tocoo()
    same = float(np.mean(y[A.row] == y[A.col]))
    lines.append(f"  ALL  : {A.nnz:,} edges, same-label edge share {same:.3f}")
    return lines


def stratified_sample(y, n, rng):
    idx = []
    for c in (0, 1):
        pool = np.where(y == c)[0]
        k = int(round(n * len(pool) / len(y)))
        idx.append(rng.choice(pool, size=k, replace=False))
    idx = np.concatenate(idx)
    rng.shuffle(idx)
    return idx


def split(y, rng):
    tr, va, te = [], [], []
    for c in (0, 1):
        pool = rng.permutation(np.where(y == c)[0])
        a, b = int(0.6 * len(pool)), int(0.8 * len(pool))
        tr += list(pool[:a]); va += list(pool[a:b]); te += list(pool[b:])
    return np.array(tr), np.array(va), np.array(te)


def logreg(X, y, tr, epochs=500, lr=0.1, wd=1e-3):
    counts = np.bincount(y[tr], minlength=2).astype(float)
    cw = counts.sum() / (2 * np.maximum(counts, 1))
    w, b = np.zeros(X.shape[1]), 0.0
    Xt, yt, st = X[tr], y[tr], cw[y[tr]]
    for _ in range(epochs):
        p = 1 / (1 + np.exp(-(Xt @ w + b)))
        g = st * (p - yt) / len(tr)
        w -= lr * (Xt.T @ g + wd * w)
        b -= lr * g.sum()
    return X @ w + b


def topk(y_true, score, pct):
    k = max(1, int(np.ceil(pct * len(y_true))))
    hits = y_true[np.argsort(-score)[:k]].sum()
    return float(hits / max(1, y_true.sum())), float(hits / k)


def metrics(y_te, s_te):
    out = {"auc": auc(y_te, s_te)}
    for pct in REVIEW_PCTS:
        r, p = topk(y_te, s_te, pct)
        tag = f"{int(pct * 100)}"
        out[f"recall@{tag}%"], out[f"precision@{tag}%"] = r, p
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat")
    ap.add_argument("--n", type=int, default=2500)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--out", default=os.path.expanduser("~/fraud-data/real_data_results.json"))
    a = ap.parse_args()

    X_all, y_all, nets_all = load(a.mat)
    print(f"Amazon users (index >= {FIRST_LABELLED}): {len(y_all):,}, fraud {y_all.sum():,} "
          f"({y_all.mean():.1%}), features {X_all.shape[1]}")
    print("Relation stats (labelled users):")
    for line in relation_stats(y_all, nets_all):
        print(line)

    runs = []
    for seed in range(a.seeds):
        rng = np.random.default_rng(seed)
        idx = stratified_sample(y_all, a.n, rng)
        X, y = X_all[idx], y_all[idx]
        X = (X - X.mean(0)) / (X.std(0) + 1e-8)
        tr, va, te = split(y, rng)
        eye = np.eye(len(y), dtype=bool)
        masks = {"gat_none": eye}
        union = None
        for key, _, _ in RELATIONS:
            A = nets_all[key][idx][:, idx]
            union = A if union is None else union + A
            masks[f"gat_{key}"] = (A.toarray() > 0) | eye
        masks["gat_all"] = (union.toarray() > 0) | eye
        masks["gat_all_skip"] = masks["gat_all"]
        masks["gat_all_skip_filter"] = masks["gat_all"]
        opts = {"gat_all_skip": {"skip": True}, "gat_all_skip_filter": {"skip": True, "filter_keep": 0.5}}

        res = {"seed": seed, "n": len(y), "fraud": int(y.sum()),
               "edges": {k: int((m.sum() - len(y)) // 2) for k, m in masks.items()}}
        res["logreg"] = metrics(y[te], logreg(X, y, tr)[te])
        print(f"\nseed {seed}: n={len(y)} fraud={y.sum()} train/val/test={len(tr)}/{len(va)}/{len(te)}")
        print(f"  logreg    AUC {res['logreg']['auc']:.4f}")
        for name, mask in masks.items():
            t0 = time.time()
            model = GAT(X.shape[1], hidden=8, heads=4, seed=seed, **opts.get(name, {}))
            hist = model.fit(X, mask, y, tr, va, epochs=a.epochs, lr=a.lr)
            s = model.predict_proba(X, mask)
            res[name] = metrics(y[te], s[te])
            res[name]["val_auc"] = hist[-1]["val_auc"]
            res[name]["seconds"] = round(time.time() - t0, 1)
            print(f"  {name:<19} AUC {res[name]['auc']:.4f}  (val {hist[-1]['val_auc']:.4f}, "
                  f"{res[name]['seconds']}s, {res['edges'][name]:,} edges)")
        runs.append(res)

    models = ["logreg", "gat_none", "gat_upu", "gat_usu", "gat_uvu", "gat_all", "gat_all_skip", "gat_all_skip_filter"]
    keys = ["auc"] + [f"{m}@{int(p * 100)}%" for p in REVIEW_PCTS for m in ("recall", "precision")]
    summary = {}
    print(f"\nSummary (mean ± std over {a.seeds} seeds, N={a.n}, test set = 20%)")
    print(f"  {'model':<19} " + "  ".join(f"{k:>16}" for k in keys))
    for m in models:
        summary[m] = {}
        cells = []
        for k in keys:
            vals = np.array([r[m][k] for r in runs])
            summary[m][k] = {"mean": float(vals.mean()), "std": float(vals.std())}
            cells.append(f"{vals.mean():.3f} ± {vals.std():.3f}".rjust(16))
        print(f"  {m:<19} " + "  ".join(cells))

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"args": vars(a), "relation_stats": relation_stats(y_all, nets_all),
                   "runs": runs, "summary": summary}, f, indent=2)
    print(f"\nWrote {a.out}")


if __name__ == "__main__":
    main()
