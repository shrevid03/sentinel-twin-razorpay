"""Held-out-ring evaluation on the twin: can the detectors catch fraud rings they never saw in training?

Usage:
    python scripts/ring_holdout_eval.py --seeds 5

Protocol: whole fraud rings (connected components of the fraud-only subgraph) are held out together, about 30% of
accounts per split; GNNs train with message passing only on edges between training accounts and then score the
full graph (inductive); normalisation is fitted on training accounts only. Models: random forest on per-account
features, random forest + structural features (PageRank, clustering, triangles, k-core), GraphSAGE (NumPy), a
GraphSAGE with every edge removed (a control: what the features alone give), and the GAT. Metrics at 0.5:
precision, recall, F1, recall by ring type, and an operational cost (missed fraud ₹5,000, false alarm ₹800).
The twin's accounts are linked when they share a device, IP or payment identifier, or moved money to each other.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import precision_recall_fscore_support

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.gat import GAT  # noqa: E402
from app.sage import SAGE  # noqa: E402

COST_MISSED, COST_FALSE_ALARM = 5000, 800


def load_twin(seed=3):
    from app.twin import Twin
    t = Twin(seed=seed)
    nodes, idx, X, mask = t.build_graph()
    accs = [a for a in t.accounts.values()]
    ai = {a.id: i for i, a in enumerate(accs)}
    feats = X[[idx[a.id] for a in accs]][:, 4:]  # per-account features (drop the node-type one-hots)
    holders = {}
    for a in accs:
        for k in a.devices | a.ips | a.pay_ids:
            holders.setdefault(k, []).append(ai[a.id])
    pairs = set()
    for hs in holders.values():
        if 2 <= len(hs) <= 30:  # a public wifi IP shared by dozens is not a meaningful link
            for i in hs:
                for j in hs:
                    if i < j:
                        pairs.add((i, j))
    for tr in t.transfers:
        i, j = ai[tr["src"]], ai[tr["dst"]]
        if i != j:
            pairs.add((min(i, j), max(i, j)))
    ei = np.array(sorted(pairs)).T
    y = np.array([int(a.fraud) for a in accs])
    seg = [a.segment.split("-")[0] for a in accs]
    rt = np.array(["none" if not a.fraud else {"ring": "device", "synthetic": "synthetic"}.get(s, s)
                   for a, s in zip(accs, seg)])
    return feats, y, ei, rt


def holdout_ring_masks(y, ei, test_frac=0.3, seed=7):
    """Whole fraud components share one random draw, so a ring is either all train or all test."""
    rng = np.random.default_rng(seed)
    N = len(y)
    ctime = rng.random(N)
    G = nx.Graph()
    G.add_nodes_from(range(N))
    for a, b in ei.T:
        if y[a] == 1 and y[b] == 1:
            G.add_edge(int(a), int(b))
    fraud = [i for i in range(N) if y[i] == 1]
    for comp in nx.connected_components(G.subgraph(fraud)):
        if len(comp) > 1:
            t = rng.random()
            for n in comp:
                ctime[n] = t
    thr = np.quantile(ctime, 1 - test_frac)
    return ctime <= thr, ctime > thr


def structural(x, ei, N):
    G = nx.Graph()
    G.add_nodes_from(range(N))
    G.add_edges_from((int(a), int(b)) for a, b in ei.T)
    pr, clu, tri, core = nx.pagerank(G, alpha=0.85), nx.clustering(G), nx.triangles(G), nx.core_number(G)
    return np.hstack([x, np.array([[pr[i], clu[i], tri[i], core[i]] for i in range(N)])])


def norm_train(x, train):
    return (x - x[train].mean(0)) / (x[train].std(0) + 1e-6)


def undirected(ei):
    return np.r_[ei[0], ei[1]], np.r_[ei[1], ei[0]]


def train_only(ei, train):
    keep = train[ei[0]] & train[ei[1]]
    return ei[:, keep]


def rf(xtr, ytr, xall, seed=0):
    clf = RandomForestClassifier(n_estimators=200, class_weight="balanced", n_jobs=-1, random_state=seed)
    return clf.fit(xtr, ytr).predict(xall)


def sage_inductive(x, y, ei, train, seed, epochs=200):
    xs = norm_train(x, train)
    tr_idx = np.where(train)[0]
    m = SAGE(x.shape[1], hidden=64, seed=1000 + seed)
    m.fit(xs, *undirected(train_only(ei, train)), y, tr_idx, epochs=epochs)
    return (m.predict_proba(xs, *undirected(ei)) >= 0.5).astype(int), m


def gat_inductive(x, y, ei, train, seed, epochs=200):
    xs = norm_train(x, train)
    N = len(y)
    full = np.eye(N, dtype=bool)
    full[ei[0], ei[1]] = full[ei[1], ei[0]] = True
    tro = train_only(ei, train)
    trm = np.eye(N, dtype=bool)
    trm[tro[0], tro[1]] = trm[tro[1], tro[0]] = True
    tr_idx = np.where(train)[0]
    m = GAT(x.shape[1], hidden=8, heads=4, seed=seed)
    m.fit(xs, trm, y, tr_idx, tr_idx[:50], epochs=epochs, lr=0.01)
    return (m.predict_proba(xs, full) >= 0.5).astype(int)


def prf(yt, yp):
    p, r, f, _ = precision_recall_fscore_support(yt, yp, average="binary", zero_division=0)
    return float(p), float(r), float(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--no-gat", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    t0 = time.time()
    x, y, ei, rt = load_twin()
    a.dataset = "twin"
    kinds = [k for k in ("cycle", "fanout", "collusion", "device", "synthetic") if k in set(rt)]
    print(f"{a.dataset}: {len(y):,} accounts, {ei.shape[1]:,} edges, {y.sum()} fraud ({y.mean():.1%}); "
          f"ring types {dict(zip(*np.unique(rt[y == 1], return_counts=True)))}", flush=True)
    xs_all = structural(x, ei, len(y))
    names = ["rf", "rf_structural", "graphsage_no_edges", "graphsage"] + ([] if a.no_gat else ["gat"])
    scores, costs, pooled = {n: [] for n in names}, {n: [] for n in names}, {n: {k: [0, 0] for k in kinds} for n in names}
    for s in range(a.seeds):
        train, test = holdout_ring_masks(y, ei, 0.3, seed=s)
        preds = {"rf": rf(x[train], y[train], x), "rf_structural": rf(xs_all[train], y[train], xs_all),
                 "graphsage": sage_inductive(x, y, ei, train, s, a.epochs)[0],
                 # control: the same GraphSAGE with every edge removed (features only)
                 "graphsage_no_edges": sage_inductive(x, y, np.zeros((2, 0), dtype=int), train, s, a.epochs)[0]}
        if not a.no_gat:
            preds["gat"] = gat_inductive(x, y, ei, train, s, a.epochs)
        line = []
        for n, pr in preds.items():
            yt, pt = y[test], pr[test]
            scores[n].append(prf(yt, pt))
            fp, fn = int(((pt == 1) & (yt == 0)).sum()), int(((pt == 0) & (yt == 1)).sum())
            costs[n].append(fn * COST_MISSED + fp * COST_FALSE_ALARM)
            for i in np.where(test & (y == 1))[0]:
                if rt[i] in pooled[n]:
                    pooled[n][rt[i]][1] += 1
                    pooled[n][rt[i]][0] += int(pr[i] == 1)
            line.append(f"{n} F1 {scores[n][-1][2]:.3f}")
        print(f"seed {s}: test fraud {int(y[test].sum())} | " + " | ".join(line), flush=True)

    label = {"rf": "Random forest (per-row)", "rf_structural": "Random forest + structural",
             "graphsage": "GraphSAGE (NumPy, inductive)", "gat": "GAT (NumPy, inductive)",
             "graphsage_no_edges": "Control: GraphSAGE, no edges"}
    summary = {}
    print(f"\nSummary: {a.dataset}, held-out unseen rings, mean ± std over {a.seeds} seeds")
    print(f"  {'model':<32}{'precision':>16}{'recall':>16}{'F1':>16}{'cost (₹)':>22}")
    for n in names:
        arr, c = np.array(scores[n]), np.array(costs[n])
        summary[n] = {"precision": [arr[:, 0].mean(), arr[:, 0].std()], "recall": [arr[:, 1].mean(), arr[:, 1].std()],
                      "f1": [arr[:, 2].mean(), arr[:, 2].std()], "cost": [c.mean(), c.std()],
                      "per_ring_recall": {k: (v[0] / v[1] if v[1] else None) for k, v in pooled[n].items()}}
        print(f"  {label[n]:<32}" + "".join(f"{m:>9.3f} ± {sd:<5.3f}" for m, sd in
                                             (summary[n]["precision"], summary[n]["recall"], summary[n]["f1"]))
              + f"{c.mean():>14,.0f} ± {c.std():,.0f}")
    print("\n  Recall by ring type (pooled over seeds):")
    print(f"  {'model':<32}" + "".join(f"{k:>12}" for k in kinds))
    for n in names:
        print(f"  {label[n]:<32}" + "".join(
            f"{summary[n]['per_ring_recall'][k]:>12.2f}" if summary[n]["per_ring_recall"][k] is not None else f"{'-':>12}"
            for k in kinds))
    print(f"  total time {round((time.time() - t0) / 60, 1)} min")
    out = a.out or "ring_holdout_results.json"
    with open(out, "w") as f:
        json.dump({"args": vars(a), "summary": summary}, f, indent=2, default=float)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
