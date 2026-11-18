"""Shared pieces for the new-account (cold-start) and hybrid evaluations.

`prep_amazon` / `prep_tfinance` rebuild exactly the per-seed samples, masks and 60/20/20 splits that
scripts/real_data_eval.py and scripts/tfinance_eval.py use (same RNG calls in the same order), so results line
up with those scripts seed by seed.

Neighbour features for the hybrid models:
  * label-free (a): mean and max of each neighbour feature, plus log degree;
  * label-aware (b): how many neighbours are *training* nodes, how many of those are fraud, and the fraud share.
    Only training labels are read, so validation and test labels never leak in.
"""
from __future__ import annotations

import os
import sys

import numpy as np
from sklearn.metrics import f1_score, roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import real_data_eval as amz  # noqa: E402
import tfinance_eval as tfin  # noqa: E402


def prep_amazon(data, seed, n):
    X_all, y_all, nets_all = data
    rng = np.random.default_rng(seed)
    idx = amz.stratified_sample(y_all, n, rng)
    X, y = X_all[idx], y_all[idx]
    X = (X - X.mean(0)) / (X.std(0) + 1e-8)
    tr, va, te = amz.split(y, rng)
    union = None
    for key, _, _ in amz.RELATIONS:
        A = nets_all[key][idx][:, idx]
        union = A if union is None else union + A
    mask = (union.toarray() > 0) | np.eye(len(y), dtype=bool)
    return X, y, mask, tr, va, te


def prep_tfinance(data, seed, n):
    X_all, y_all, src, dst = data
    rng = np.random.default_rng(seed)
    idx = tfin.stratified_sample(y_all, n, rng)
    pos = -np.ones(len(y_all), dtype=int)
    pos[idx] = np.arange(len(idx))
    keep = (pos[src] >= 0) & (pos[dst] >= 0)
    mask = np.eye(len(idx), dtype=bool)
    mask[pos[src[keep]], pos[dst[keep]]] = True
    mask |= mask.T
    X = np.log1p(np.maximum(X_all[idx], 0))
    X = (X - X.mean(0)) / (X.std(0) + 1e-8)
    y = y_all[idx]
    tr, va, te = tfin.split(y, rng)
    return X, y, mask, tr, va, te


def load(dataset, path):
    return amz.load(path) if dataset == "amazon" else tfin.load(path)


def prep(dataset, data, seed, n):
    return prep_amazon(data, seed, n) if dataset == "amazon" else prep_tfinance(data, seed, n)


def nbr_label_free(X, mask):
    """(a) mean and max of neighbours' features (self excluded) and log degree, from a dense mask."""
    A = mask.copy()
    np.fill_diagonal(A, False)
    deg = A.sum(1)
    mean = (A.astype(float) @ X) / np.maximum(deg, 1)[:, None]
    mx = np.zeros_like(X)
    for f in range(X.shape[1]):
        mx[:, f] = np.where(A, X[:, f][None, :], -np.inf).max(1)
    mx[deg == 0] = 0.0
    return np.column_stack([mean, mx, np.log1p(deg)])


def nbr_label_aware(mask, y, tr):
    """(b) known-fraud neighbours, using training labels only."""
    A = mask.copy()
    np.fill_diagonal(A, False)
    known = np.zeros(len(y), dtype=bool)
    known[tr] = True
    fraud = known & (y == 1)
    kn = A[:, known].sum(1).astype(float)
    kf = A[:, fraud].sum(1).astype(float)
    return np.column_stack([kf / np.maximum(kn, 1), np.log1p(kf), np.log1p(kn), (kf > 0).astype(float)])


def metrics(y, p, review=0.05):
    pred = (p >= 0.5).astype(int)
    k = int(np.ceil(review * len(y)))
    hits = int(y[np.argsort(-p, kind="stable")[:k]].sum())
    return {"auc": float(roc_auc_score(y, p)) if len(set(p.tolist())) > 1 else 0.5,
            "f1": float(f1_score(y, pred, zero_division=0)),
            "recall_top5": hits / max(int(y.sum()), 1), "precision_top5": hits / k}


def summarise(runs, models, keys=(("auc", "AUC"), ("f1", "F1@0.5"), ("recall_top5", "R@5%"), ("precision_top5", "P@5%"))):
    summary, lines = {}, []
    lines.append(f"  {'model':<34}" + "".join(f"{h:>16}" for _, h in keys))
    for m, label in models:
        if not all(m in r for r in runs):
            continue
        summary[m] = {}
        cells = []
        for k, _ in keys:
            v = np.array([r[m][k] for r in runs])
            summary[m][k] = {"mean": float(v.mean()), "std": float(v.std())}
            cells.append(f"{v.mean():.3f} ± {v.std():.3f}".rjust(16))
        lines.append(f"  {label:<34}" + "".join(cells))
    return summary, lines
