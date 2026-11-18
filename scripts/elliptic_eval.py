"""Evaluate the Sentinel Twin GAT on the Elliptic Bitcoin transaction graph (Weber et al., 2019).

Usage:
    python scripts/elliptic_eval.py ~/fraud-data/elliptic --epochs 40

Data: elliptic_txs_features.csv, elliptic_txs_classes.csv and elliptic_txs_edgelist.csv (public mirror used by
PyTorch Geometric: https://data.pyg.org/datasets/elliptic/). 203,769 transactions in 49 time steps; class 1 =
illicit, 2 = licit, unknown otherwise. Edges are Bitcoin payment flows and never cross time steps.

Protocol (as in the paper): train on time steps 1-34, test on 35-49, loss and metrics on labelled nodes only.

Headline comparison uses ONLY the local transaction features for every model, so any gain from edges is the
graph's own. The paper's "94 local features" include the time step; it is dropped here because it carries no
signal under a temporal split, leaving 93. The 72 aggregated features already summarise each transaction's 1-hop
neighbourhood, so models on all 165 features are reported only as a reference.

Models: logistic regression and random forest (the paper's best) on features; the app's GAT with no edges
(identity mask); the same GAT with the transaction edges. The GAT is dense, so each time step is one graph made of
its labelled nodes plus their 1-hop neighbours, capped at --cap nodes (labelled nodes always kept).

GAT variants added after the first runs (app/gat.py options, both off by default): "skip" adds each node's own
transformed features to the attention output (A @ Z + Z); "filter" keeps, for each node, the half of its neighbours
whose label-aware score (a logistic regression fitted on training nodes only) is closest to its own (CARE-GNN style).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.gat import GAT  # noqa: E402

TRAIN_STEPS = range(1, 35)
TEST_STEPS = range(35, 50)
SHUTDOWN_STEP = 43  # dark-market shutdown; the paper shows every model failing after it


def load(d):
    feats = pd.read_csv(os.path.join(d, "elliptic_txs_features.csv"), header=None)
    cls = pd.read_csv(os.path.join(d, "elliptic_txs_classes.csv"))
    edges = pd.read_csv(os.path.join(d, "elliptic_txs_edgelist.csv"))
    ids = feats[0].to_numpy()
    step = feats[1].to_numpy().astype(int)
    X_all = feats.iloc[:, 2:].to_numpy(dtype=np.float64)  # 93 local + 72 aggregated = 165
    lab = cls.set_index("txId").loc[ids, "class"].astype(str).to_numpy()
    y = np.where(lab == "1", 1, np.where(lab == "2", 0, -1))
    pos = {t: i for i, t in enumerate(ids)}
    src = edges["txId1"].map(pos).to_numpy()
    dst = edges["txId2"].map(pos).to_numpy()
    return X_all, y, step, src, dst


def neighbour_stats(y, src, dst):
    """Share of an illicit node's labelled neighbours that are illicit (edges taken as undirected)."""
    a, b = np.concatenate([src, dst]), np.concatenate([dst, src])
    keep = (y[a] == 1) & (y[b] >= 0)
    if not keep.any():
        return {}
    df = pd.DataFrame({"n": a[keep], "illicit": y[b[keep]] == 1})
    per = df.groupby("n")["illicit"].mean()
    illicit_nodes = int((y == 1).sum())
    keep_l = (y[a] == 0) & (y[b] >= 0)
    per_l = pd.DataFrame({"n": a[keep_l], "licit": y[b[keep_l]] == 0}).groupby("n")["licit"].mean()
    lab_edges = (y[src] >= 0) & (y[dst] >= 0)
    return {
        "illicit_nodes": illicit_nodes,
        "illicit_with_labelled_neighbour": int(len(per)),
        "illicit_neighbour_share_of_illicit": round(float(per.mean()), 3),
        "licit_neighbour_share_of_licit": round(float(per_l.mean()), 3),
        "labelled_edge_same_label_share": round(float((y[src][lab_edges] == y[dst][lab_edges]).mean()), 3),
        "illicit_base_rate": round(float((y == 1).sum() / (y >= 0).sum()), 3),
    }


def step_graph(t, step, y, src, dst, cap, rng):
    """Nodes of time step t: labelled nodes plus 1-hop neighbours (capped), and the dense mask."""
    in_t = np.where(step == t)[0]
    labelled = in_t[y[in_t] >= 0]
    lset = set(labelled.tolist())
    e = np.isin(src, in_t)
    s, d = src[e], dst[e]
    nb = set(d[np.isin(s, labelled)].tolist()) | set(s[np.isin(d, labelled)].tolist())
    nb -= lset
    nb = np.array(sorted(nb), dtype=int)
    room = max(cap - len(labelled), 0)
    if len(nb) > room:
        nb = rng.choice(nb, size=room, replace=False)
    nodes = np.concatenate([labelled, nb])
    idx = {n: i for i, n in enumerate(nodes)}
    mask = np.eye(len(nodes), dtype=bool)
    for u, v in zip(s, d):
        i, j = idx.get(u), idx.get(v)
        if i is not None and j is not None:
            mask[i, j] = mask[j, i] = True
    return nodes, np.arange(len(labelled)), mask


def metrics(y, p, thr=0.5, review=0.05):
    pred = (p >= thr).astype(int)
    k = int(np.ceil(review * len(y)))
    hits = int(y[np.argsort(-p)[:k]].sum())
    return {"f1": f1_score(y, pred, zero_division=0), "precision": precision_score(y, pred, zero_division=0),
            "recall": recall_score(y, pred, zero_division=0), "auc": roc_auc_score(y, p) if len(set(y)) > 1 else float("nan"),
            "recall_top5": hits / max(int(y.sum()), 1), "precision_top5": hits / k}


def train_gat(graphs, X, y, epochs, lr, seed, wd=5e-4, use_edges=True, **opt):
    model = GAT(X.shape[1], hidden=8, heads=4, seed=seed, **opt)
    if model.filter_keep:  # similarity model on the labelled training nodes only
        lab_nodes = np.concatenate([n[li] for n, li, _ in graphs])
        model.fit_similarity(X[lab_nodes], y[lab_nodes], np.arange(len(lab_nodes)))
    masks = [model.filter_mask(X[n], m) if use_edges else np.eye(len(n), dtype=bool) for n, _, m in graphs]
    params = model.params()
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    ys = np.concatenate([y[n[li]] for n, li, _ in graphs])
    counts = np.bincount(ys, minlength=2).astype(float)
    cw = counts.sum() / (2 * np.maximum(counts, 1))
    rng = np.random.default_rng(seed)
    t = 0
    for ep in range(epochs):
        losses = []
        for gi in rng.permutation(len(graphs)):
            nodes, li, _ = graphs[gi]
            mk = masks[gi]
            yl = np.full(len(nodes), 0)
            yl[li] = y[nodes[li]]
            loss, grads, _ = model.loss_and_grads(X[nodes], mk, yl, li, cw)
            losses.append(loss)
            t += 1
            for i, (p, g) in enumerate(zip(params, grads)):
                g = g + wd * p
                m[i] = 0.9 * m[i] + 0.1 * g
                v[i] = 0.999 * v[i] + 0.001 * g * g
                p -= lr * (m[i] / (1 - 0.9 ** t)) / (np.sqrt(v[i] / (1 - 0.999 ** t)) + 1e-8)
        if (ep + 1) % 10 == 0 or ep == 0:
            print(f"    epoch {ep + 1}: mean loss {np.mean(losses):.4f}", flush=True)
    return model


def gat_predict(model, graphs, X, use_edges=True):
    out_idx, out_p = [], []
    for nodes, li, mask in graphs:
        mk = mask if use_edges else np.eye(len(nodes), dtype=bool)
        p = model.predict_proba(X[nodes], mk)
        out_idx.append(nodes[li])
        out_p.append(p[li])
    return np.concatenate(out_idx), np.concatenate(out_p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--cap", type=int, default=2500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=os.path.expanduser("~/fraud-data/elliptic_results.json"))
    a = ap.parse_args()
    t0 = time.time()

    X_all, y, step, src, dst = load(a.data_dir)
    lab = y >= 0
    print(f"Elliptic: {len(y):,} transactions, {len(src):,} edges, {lab.sum():,} labelled, "
          f"{(y == 1).sum():,} illicit ({(y == 1).sum() / lab.sum():.1%} of labelled)")
    ns = neighbour_stats(y, src, dst)
    print("Neighbour stats (labelled nodes, edges undirected):")
    print(f"  share of an illicit tx's labelled neighbours that are illicit: {ns['illicit_neighbour_share_of_illicit']:.3f} "
          f"(base rate {ns['illicit_base_rate']:.3f}; {ns['illicit_with_labelled_neighbour']:,} of {ns['illicit_nodes']:,} "
          f"illicit txs have a labelled neighbour)")
    print(f"  share of a licit tx's labelled neighbours that are licit: {ns['licit_neighbour_share_of_licit']:.3f}; "
          f"same-label share of labelled-labelled edges: {ns['labelled_edge_same_label_share']:.3f}")

    tr = lab & np.isin(step, list(TRAIN_STEPS))
    te = lab & np.isin(step, list(TEST_STEPS))
    LOCAL = slice(0, 93)
    results, preds = {}, {}

    def feat_models(cols, tag):
        Xs = X_all[:, cols]
        mu, sd = Xs[tr].mean(0), Xs[tr].std(0) + 1e-8
        Z = (Xs - mu) / sd
        lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Z[tr], y[tr])
        rf = RandomForestClassifier(n_estimators=50, max_features=50, n_jobs=-1, random_state=a.seed).fit(Xs[tr], y[tr])
        for name, mdl, M in ((f"logreg_{tag}", lr, Z), (f"rf_{tag}", rf, Xs)):
            preds[name] = mdl.predict_proba(M[te])[:, 1]
            results[name] = metrics(y[te], preds[name])
            print(f"  {name:<14} F1 {results[name]['f1']:.3f}  AUC {results[name]['auc']:.3f}", flush=True)

    print("\nFeature-only models")
    feat_models(LOCAL, "local")
    feat_models(slice(0, 165), "all165")

    print(f"\nBuilding per-step graphs (labelled + 1-hop neighbours, cap {a.cap})", flush=True)
    rng = np.random.default_rng(a.seed)
    Xl = X_all[:, LOCAL]
    Zl = (Xl - Xl[tr].mean(0)) / (Xl[tr].std(0) + 1e-8)
    g_tr = [step_graph(t, step, y, src, dst, a.cap, rng) for t in TRAIN_STEPS]
    g_te = [step_graph(t, step, y, src, dst, a.cap, rng) for t in TEST_STEPS]
    sizes = [len(g[0]) for g in g_tr + g_te]
    capped = sum(1 for g in g_tr + g_te if len(g[0]) >= a.cap)
    print(f"  {len(sizes)} step graphs, {min(sizes)}-{max(sizes)} nodes, {capped} hit the cap; "
          f"edges kept: {sum(int((g[2].sum() - len(g[0])) // 2) for g in g_tr + g_te):,}", flush=True)

    order = None
    for name, use_edges, opt in (("gat_none_local", False, {}), ("gat_edges_local", True, {}),
                                 ("gat_edges_skip", True, {"skip": True}),
                                 ("gat_edges_skip_filter", True, {"skip": True, "filter_keep": 0.5})):
        t1 = time.time()
        print(f"\n{name}: training {a.epochs} epochs over {len(g_tr)} step graphs", flush=True)
        model = train_gat(g_tr, Zl, y, a.epochs, a.lr, a.seed, use_edges=use_edges, **opt)
        idx, p = gat_predict(model, g_te, Zl, use_edges=use_edges)
        if order is None:
            order = idx
        pos = {n: i for i, n in enumerate(idx)}
        te_idx = np.where(te)[0]
        p_full = np.array([p[pos[n]] for n in te_idx])
        preds[name] = p_full
        results[name] = metrics(y[te], p_full)
        results[name]["seconds"] = round(time.time() - t1)
        print(f"  {name:<14} F1 {results[name]['f1']:.3f}  AUC {results[name]['auc']:.3f}  ({results[name]['seconds']}s)", flush=True)

    # before vs after the dark-market shutdown (illicit F1 per period)
    te_steps = step[te]
    for name in preds:
        for period, sel in (("before_shutdown", te_steps < SHUTDOWN_STEP), ("after_shutdown", te_steps >= SHUTDOWN_STEP)):
            pr = (preds[name][sel] >= 0.5).astype(int)
            results[name][f"f1_{period}"] = f1_score(y[te][sel], pr, zero_division=0)

    rows = ["logreg_local", "rf_local", "gat_none_local", "gat_edges_local", "gat_edges_skip", "gat_edges_skip_filter",
            "logreg_all165", "rf_all165"]
    label = {"logreg_local": "LogReg, local 93", "rf_local": "RandomForest, local 93",
             "gat_none_local": "GAT no edges, local 93", "gat_edges_local": "GAT + tx edges, local 93",
             "gat_edges_skip": "GAT + edges, skip", "gat_edges_skip_filter": "GAT + edges, skip+filter",
             "logreg_all165": "LogReg, all 165*", "rf_all165": "RandomForest, all 165*"}
    print(f"\nSummary: illicit class on test steps 35-49 ({te.sum():,} labelled txs, {(y[te] == 1).sum():,} illicit), threshold 0.5")
    print(f"  {'model':<26}{'F1':>7}{'Prec':>7}{'Recall':>8}{'AUC':>7}{'R@5%':>7}{'P@5%':>7}{'F1<43':>8}{'F1>=43':>8}")
    for r in rows:
        m = results[r]
        print(f"  {label[r]:<26}{m['f1']:>7.3f}{m['precision']:>7.3f}{m['recall']:>8.3f}{m['auc']:>7.3f}"
              f"{m['recall_top5']:>7.3f}{m['precision_top5']:>7.3f}"
              f"{m['f1_before_shutdown']:>8.3f}{m['f1_after_shutdown']:>8.3f}")
    print("  * reference only: includes 72 precomputed neighbour aggregates")
    print("  F1/Prec/Recall at threshold 0.5; R@5%/P@5% = recall/precision when the riskiest 5% of test txs are reviewed")
    print("  LogReg and GAT use balanced class weights (favour recall at 0.5); RandomForest is unweighted, as in the paper")
    print(f"  GAT: {a.epochs} epochs, lr {a.lr}, hidden 8 x 4 heads, step graphs capped at {a.cap} nodes; seed {a.seed}")
    print(f"  total time {round((time.time() - t0) / 60, 1)} min")

    with open(a.out, "w") as f:
        json.dump({"args": vars(a), "neighbour_stats": ns, "results": results}, f, indent=2, default=float)
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
