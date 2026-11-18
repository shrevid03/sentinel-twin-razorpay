"""Evaluate the Sentinel Twin GAT on T-Finance (Tang et al., ICML 2022, "Rethinking Graph Neural Networks for
Anomaly Detection"; also part of GADBench).

Usage:
    python scripts/tfinance_eval.py ~/fraud-data/tfinance/dataset/tfinance --n 2500 --seeds 3 --epochs 200

T-Finance is a real transaction network: 39,357 accounts, 10 account features, ~21M undirected edges (transaction
records between accounts), and labels for anomalous accounts (fraud, money laundering, online gambling; ~4.6%).
The file is a DGL `save_graphs` binary. DGL has no wheel for this Python, so the arrays are read directly from the
file: DGL writes each array with a fixed NDArray header (magic 0xDD5E40F096B4A13F), and the arrays are picked out by
shape (two edge lists, a one-hot label matrix and the feature matrix).

Protocol, matching the Amazon evaluation: for each seed, a stratified sample of N accounts, induced subgraph,
features log1p-transformed and standardised, 60/20/20 split. Models: class-weighted logistic regression, random
forest, the app's GAT with no edges, and the GAT with the transaction edges. Metrics on the test split: AUC, F1 at
0.5, and recall/precision when the riskiest 5% are reviewed. Also reports the neighbour statistic on the full graph.

GAT variants added after the first runs (app/gat.py options, both off by default): "skip" adds each node's own
transformed features to the attention output (A @ Z + Z); "filter" keeps, for each node, the half of its neighbours
whose label-aware score (a logistic regression fitted on training nodes only) is closest to its own (CARE-GNN style).
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.gat import GAT  # noqa: E402

NDARRAY_MAGIC = struct.pack("<Q", 0xDD5E40F096B4A13F)


def read_dgl_arrays(path):
    """Yield every NDArray stored in a DGL save_graphs file."""
    buf = open(path, "rb").read()
    pos = 0
    while True:
        i = buf.find(NDARRAY_MAGIC, pos)
        if i < 0:
            return
        o = i + 16 + 8  # magic, reserved, device (type, id)
        (ndim,) = struct.unpack_from("<i", buf, o)
        o += 4
        code, bits, _ = struct.unpack_from("<BBH", buf, o)
        o += 4
        shape = struct.unpack_from("<" + "q" * ndim, buf, o)
        o += 8 * ndim
        (nbytes,) = struct.unpack_from("<q", buf, o)
        o += 8
        dt = {0: "i", 1: "u", 2: "f"}[code] + str(bits // 8)
        yield np.frombuffer(buf, dtype=dt, count=int(np.prod(shape)), offset=o).reshape(shape)
        pos = o + nbytes


def load(path):
    arrs = list(read_dgl_arrays(path))
    edges = [a for a in arrs if a.ndim == 1 and len(a) > 1000]
    labels = [a for a in arrs if a.ndim == 2 and a.shape[1] == 2]
    feats = [a for a in arrs if a.ndim == 2 and a.dtype.kind == "f"]
    assert len(edges) == 2 and len(labels) == 1 and len(feats) == 1, [a.shape for a in arrs]
    src, dst = edges
    y = labels[0].argmax(1).astype(int)
    X = feats[0].astype(np.float64)
    return X, y, src, dst


def neighbour_stats(y, src, dst):
    n = len(y)
    deg = np.bincount(src, minlength=n)
    anom_nb = np.bincount(src, weights=(y[dst] == 1), minlength=n)
    a = (y == 1) & (deg > 0)
    l = (y == 0) & (deg > 0)
    return {"avg_degree": round(float(len(src) / n), 1),
            "anomalous_neighbour_share_of_anomalous": round(float((anom_nb[a] / deg[a]).mean()), 3),
            "anomalous_neighbour_share_of_normal": round(float((anom_nb[l] / deg[l]).mean()), 3),
            "base_rate": round(float(y.mean()), 3)}


def stratified_sample(y, n, rng):
    idx = []
    for c in (0, 1):
        pool = np.where(y == c)[0]
        idx.append(rng.choice(pool, size=int(round(n * len(pool) / len(y))), replace=False))
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


def metrics(y, p, review=0.05):
    pred = (p >= 0.5).astype(int)
    k = int(np.ceil(review * len(y)))
    hits = int(y[np.argsort(-p)[:k]].sum())
    return {"auc": roc_auc_score(y, p), "f1": f1_score(y, pred, zero_division=0),
            "precision": precision_score(y, pred, zero_division=0), "recall": recall_score(y, pred, zero_division=0),
            "recall_top5": hits / max(int(y.sum()), 1), "precision_top5": hits / k}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--n", type=int, default=2500)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--out", default=os.path.expanduser("~/fraud-data/tfinance_results.json"))
    a = ap.parse_args()
    t0 = time.time()

    X_all, y_all, src, dst = load(a.path)
    print(f"T-Finance: {len(y_all):,} accounts, {len(src) // 2:,} undirected edges, {X_all.shape[1]} features, "
          f"anomalous {y_all.sum():,} ({y_all.mean():.1%})")
    ns = neighbour_stats(y_all, src, dst)
    print(f"Neighbour stats (full graph): average degree {ns['avg_degree']}; share of an anomalous account's neighbours "
          f"that are anomalous {ns['anomalous_neighbour_share_of_anomalous']:.3f} vs {ns['anomalous_neighbour_share_of_normal']:.3f} "
          f"for a normal account (base rate {ns['base_rate']:.3f})", flush=True)

    runs = []
    for seed in range(a.seeds):
        rng = np.random.default_rng(seed)
        idx = stratified_sample(y_all, a.n, rng)
        pos = -np.ones(len(y_all), dtype=int)
        pos[idx] = np.arange(len(idx))
        keep = (pos[src] >= 0) & (pos[dst] >= 0)
        mask = np.eye(len(idx), dtype=bool)
        mask[pos[src[keep]], pos[dst[keep]]] = True
        mask |= mask.T
        X = np.log1p(np.maximum(X_all[idx], 0))
        X = (X - X.mean(0)) / (X.std(0) + 1e-8)
        y = y_all[idx]
        tr, va, te = split(y, rng)
        n_edges = int((mask.sum() - len(idx)) // 2)
        res = {"seed": seed, "n": len(idx), "anomalous": int(y.sum()), "edges": n_edges,
               "avg_degree": round(2 * n_edges / len(idx), 1)}
        print(f"\nseed {seed}: n={len(idx)} anomalous={y.sum()} edges in sample {n_edges:,} "
              f"(avg degree {res['avg_degree']}) train/val/test={len(tr)}/{len(va)}/{len(te)}", flush=True)
        lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X[tr], y[tr])
        rf = RandomForestClassifier(n_estimators=200, n_jobs=-1, random_state=seed).fit(X[tr], y[tr])
        for name, mdl in (("logreg", lr), ("rf", rf)):
            res[name] = metrics(y[te], mdl.predict_proba(X[te])[:, 1])
            print(f"  {name:<10} AUC {res[name]['auc']:.4f}  F1 {res[name]['f1']:.3f}", flush=True)
        eye = np.eye(len(idx), dtype=bool)
        for name, mk, opt in (("gat_none", eye, {}), ("gat_edges", mask, {}), ("gat_edges_skip", mask, {"skip": True}),
                              ("gat_edges_skip_filter", mask, {"skip": True, "filter_keep": 0.5})):
            t1 = time.time()
            model = GAT(X.shape[1], hidden=8, heads=4, seed=seed, **opt)
            hist = model.fit(X, mk, y, tr, va, epochs=a.epochs, lr=a.lr)
            p = model.predict_proba(X, mk)
            res[name] = metrics(y[te], p[te])
            res[name]["val_auc"] = hist[-1]["val_auc"]
            res[name]["seconds"] = round(time.time() - t1, 1)
            print(f"  {name:<22} AUC {res[name]['auc']:.4f}  F1 {res[name]['f1']:.3f}  (val {hist[-1]['val_auc']:.4f}, "
                  f"{res[name]['seconds']}s)", flush=True)
        runs.append(res)

    models = [("logreg", "LogReg (features)"), ("rf", "RandomForest (features)"),
              ("gat_none", "GAT, no edges"), ("gat_edges", "GAT + transaction edges"),
              ("gat_edges_skip", "GAT + edges, skip"), ("gat_edges_skip_filter", "GAT + edges, skip+filter")]
    keys = [("auc", "AUC"), ("f1", "F1@0.5"), ("precision", "Prec@0.5"), ("recall", "Rec@0.5"),
            ("recall_top5", "R@5%"), ("precision_top5", "P@5%")]
    summary = {}
    print(f"\nSummary (mean ± std over {a.seeds} seeds, N={a.n}, test set = 20%, "
          f"avg degree in samples {np.mean([r['avg_degree'] for r in runs]):.1f})")
    print(f"  {'model':<26}" + "".join(f"{h:>16}" for _, h in keys))
    for m, label in models:
        summary[m] = {}
        cells = []
        for k, _ in keys:
            v = np.array([r[m][k] for r in runs])
            summary[m][k] = {"mean": float(v.mean()), "std": float(v.std())}
            cells.append(f"{v.mean():.3f} ± {v.std():.3f}".rjust(16))
        print(f"  {label:<26}" + "".join(cells))
    print(f"  GAT: {a.epochs} epochs, lr {a.lr}, hidden 8 x 4 heads; LogReg and GAT class-weighted, RandomForest unweighted")
    print(f"  total time {round((time.time() - t0) / 60, 1)} min")
    with open(a.out, "w") as f:
        json.dump({"args": vars(a), "neighbour_stats": ns, "runs": runs, "summary": summary}, f, indent=2, default=float)
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
