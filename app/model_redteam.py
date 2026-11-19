"""Structural red team against the MODEL: attack the detector itself, then harden it.

The defense drill (app/drill.py) hardens the decision policy. This hardens the detector itself:
  attack    the adversary thins its own ring (drops a share of fraud-to-fraud edges) and adds "cover" edges from
            each fraud account to random normal accounts;
  measure   recall on held-out rings: clean -> under attack -> after adversarial retraining, where the hardened
            model is trained on one perturbation and evaluated on a different, fresh one;
  ablation  hardening severity (train on a 50% vs a 70% perturbation);
  RED/BLUE  bounded co-evolution: each round RED tries a menu of perturbations against the CURRENT model and keeps
            the one that hurts held-out recall most; BLUE retrains on a fresh realisation of it.

Notes:
  * Feat(mode="recompute") rebuilds degree-type features from the attacked edges, so an attack that adds links also
    changes what the account looks like; "frozen" keeps the original features;
  * mixed hardening (perturb half the rings, keep half clean) keeps clean-ring recall from collapsing;
  * everything runs on a copy: the live twin is never touched.
Uses the NumPy GraphSAGE (app/sage.py).
"""
from __future__ import annotations

import networkx as nx
import numpy as np

from .sage import SAGE

STRATEGIES = [(0.3, 2), (0.5, 2), (0.5, 4), (0.7, 4), (0.7, 6)]  # (drop share of ring edges, cover edges per node)


def holdout_ring_masks(y, ei, test_frac=0.3, seed=7):
    rng = np.random.default_rng(seed)
    N = len(y)
    ctime = rng.random(N)
    G = nx.Graph()
    G.add_nodes_from(range(N))
    G.add_edges_from((int(a), int(b)) for a, b in ei.T if y[a] == 1 and y[b] == 1)
    for comp in nx.connected_components(G.subgraph([i for i in range(N) if y[i] == 1])):
        if len(comp) > 1:
            t = rng.random()
            for n in comp:
                ctime[n] = t
    thr = np.quantile(ctime, 1 - test_frac)
    return ctime <= thr, ctime > thr


def attack(ei, y, drop_frac=0.7, cover_k=4, seed=0):
    """Thin intra-ring edges and add cover edges from ring nodes to normal nodes."""
    r = np.random.default_rng(seed)
    ring = y == 1
    normal = np.where(~ring)[0]
    keep = ~(ring[ei[0]] & ring[ei[1]] & (r.random(ei.shape[1]) < drop_frac))
    fraud = np.where(ring)[0]
    cover = np.stack([np.repeat(fraud, cover_k), r.choice(normal, size=len(fraud) * cover_k)])
    return np.concatenate([ei[:, keep], cover], axis=1)


def attack_some_rings(ei, y, drop_frac, cover_k, seed, share=0.5):
    """Mixed hardening: perturb only a random share of the fraud rings and leave the rest clean, so the retrained
    model keeps seeing both what rings normally look like and what they look like when they hide."""
    r = np.random.default_rng(seed)
    G = nx.Graph()
    G.add_edges_from((int(a), int(b)) for a, b in ei.T if y[a] == 1 and y[b] == 1)
    hide = np.zeros(len(y), dtype=bool)
    for comp in nx.connected_components(G):
        if r.random() < share:
            hide[list(comp)] = True
    yy = np.where(hide, y, 0)  # only the hiding rings perturb themselves
    return attack(ei, yy, drop_frac, cover_k, seed)


def degree_features(x, ei, cols):
    """Recompute the degree-type columns of x from (attacked) edges. cols maps a feature name to its column."""
    x = x.copy()
    n = len(x)
    out_deg = np.bincount(ei[0], minlength=n)
    in_deg = np.bincount(ei[1], minlength=n)
    if "out_degree" in cols:
        x[:, cols["out_degree"]] = out_deg
    if "in_degree" in cols:
        x[:, cols["in_degree"]] = in_deg
    if "n_counterparties" in cols:
        nb = [set() for _ in range(n)]
        for a, b in ei.T:
            nb[a].add(b)
            nb[b].add(a)
        x[:, cols["n_counterparties"]] = [len(s) for s in nb]
    return x


def _und(ei):
    return np.r_[ei[0], ei[1]], np.r_[ei[1], ei[0]]


def _train_only(ei, train):
    return ei[:, train[ei[0]] & train[ei[1]]]


def _norm(x, train):
    return (x - x[train].mean(0)) / (x[train].std(0) + 1e-6)


def train_inductive(x, ei, y, train, seed, epochs=200):
    m = SAGE(x.shape[1], hidden=64, seed=seed)
    m.fit(_norm(x, train), *_und(_train_only(ei, train)), y, np.where(train)[0], epochs=epochs)
    return m


def ring_recall(model, x, ei, y, train, test):
    p = model.predict_proba(_norm(x, train), *_und(ei))
    sel = (y == 1) & test
    return float((p[sel] >= 0.5).mean()) if sel.any() else float("nan")


def legit_flag_rate(model, x, ei, y, train, test):
    p = model.predict_proba(_norm(x, train), *_und(ei))
    sel = (y == 0) & test
    return float((p[sel] >= 0.5).mean())


class Feat:
    """Feature view for an edge set: frozen or recomputed from the edges."""

    def __init__(self, x, cols=None, mode="frozen"):
        self.x, self.cols, self.mode = x, cols or {}, mode

    def __call__(self, ei):
        return self.x if self.mode == "frozen" or not self.cols else degree_features(self.x, ei, self.cols)


def robustness(x, y, ei, seeds=5, epochs=200, feat=None, drop=0.7, cover=4, harden=(0.7, 4), mixed=False):
    """Held-out-ring robustness over several seeds: clean -> attacked -> hardened (on a fresh perturbation)."""
    feat = feat or Feat(x)
    rows = []
    for s in range(seeds):
        train, test = holdout_ring_masks(y, ei, 0.3, seed=s)
        clean = train_inductive(feat(ei), ei, y, train, 1000 + s, epochs)
        r_clean = ring_recall(clean, feat(ei), ei, y, train, test)
        e_att = attack(ei, y, drop, cover, seed=100 + s)
        r_att = ring_recall(clean, feat(e_att), e_att, y, train, test)
        e_hard = (attack_some_rings(ei, y, harden[0], harden[1], seed=200 + s) if mixed
                  else attack(ei, y, harden[0], harden[1], seed=200 + s))
        hard = train_inductive(feat(e_hard), e_hard, y, train, 2000 + s, epochs)
        e_eval = attack(ei, y, drop, cover, seed=300 + s)
        rows.append({"seed": s, "clean": r_clean, "attacked": r_att,
                     "hardened": ring_recall(hard, feat(e_eval), e_eval, y, train, test),
                     "hardened_clean": ring_recall(hard, feat(ei), ei, y, train, test),
                     "hardened_legit_flag_rate": legit_flag_rate(hard, feat(e_eval), e_eval, y, train, test),
                     "test_fraud": int((y[test] == 1).sum())})
    agg = {k: [float(np.nanmean([r[k] for r in rows])), float(np.nanstd([r[k] for r in rows]))]
           for k in ("clean", "attacked", "hardened", "hardened_clean", "hardened_legit_flag_rate")}
    return {"rows": rows, "mean_std": agg}


def coevolve(x, y, ei, rounds=3, seed=7, epochs=150, feat=None):
    """Bounded RED/BLUE: RED picks the strategy that hurts the current model most; BLUE retrains on a fresh draw."""
    feat = feat or Feat(x)
    train, test = holdout_ring_masks(y, ei, 0.3, seed=seed)
    model = train_inductive(feat(ei), ei, y, train, seed, epochs)
    out = [{"round": 0, "clean_recall": ring_recall(model, feat(ei), ei, y, train, test)}]
    for k in range(1, rounds + 1):
        tried = []
        for j, (d, c) in enumerate(STRATEGIES):
            e = attack(ei, y, d, c, seed=1000 * k + j)
            tried.append((ring_recall(model, feat(e), e, y, train, test), d, c))
        worst, d, c = min(tried)
        e_train = attack(ei, y, d, c, seed=5000 + k)
        model = train_inductive(feat(e_train), e_train, y, train, seed + k, epochs)
        e_fresh = attack(ei, y, d, c, seed=9000 + k)
        out.append({"round": k, "red_drop": d, "red_cover": c, "recall_before_blue": worst,
                    "tried": [{"drop": dd, "cover": cc, "recall": rr} for rr, dd, cc in tried],
                    "clean_recall_after_blue": ring_recall(model, feat(ei), ei, y, train, test),
                    "fresh_attack_recall_after_blue": ring_recall(model, feat(e_fresh), e_fresh, y, train, test)})
    return out


def twin_account_graph(twin):
    """The twin's accounts as a graph: an edge when two accounts share a device, IP or payment id, or moved money."""
    nodes, idx, X, _ = twin.build_graph()
    accs = list(twin.accounts.values())
    ai = {a.id: i for i, a in enumerate(accs)}
    feats = X[[idx[a.id] for a in accs]][:, 4:]
    holders = {}
    for a in accs:
        for k in a.devices | a.ips | a.pay_ids:
            holders.setdefault(k, []).append(ai[a.id])
    pairs = set()
    for hs in holders.values():
        if 2 <= len(hs) <= 30:
            pairs.update((i, j) for i in hs for j in hs if i < j)
    for t in twin.transfers:
        i, j = ai[t["src"]], ai[t["dst"]]
        if i != j:
            pairs.add((min(i, j), max(i, j)))
    return feats, np.array([int(a.fraud) for a in accs]), np.array(sorted(pairs)).T


def run_on_twin(twin, seeds=3, rounds=3, epochs=150):
    """The live demo: robustness + RED/BLUE on a snapshot of the twin's account graph (nothing live changes)."""
    with twin.lock:
        x, y, ei = twin_account_graph(twin)
    rob = robustness(x, y, ei, seeds=seeds, epochs=epochs)
    rob_mixed = robustness(x, y, ei, seeds=seeds, epochs=epochs, mixed=True)
    return {"accounts": int(len(y)), "edges": int(ei.shape[1]), "fraud": int(y.sum()), "robustness": rob,
            "robustness_mixed": rob_mixed,
            "coevolution": coevolve(x, y, ei, rounds=rounds, epochs=epochs),
            "note": "GraphSAGE (NumPy) on a snapshot of the twin's account graph; the live twin is untouched."}
