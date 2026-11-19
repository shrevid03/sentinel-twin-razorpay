"""GraphSAGE in NumPy (forward + backward + Adam), a selectable alternative to the GAT (TWIN_MODEL=graphsage).

Architecture: SAGEConv(in -> 64) -> ReLU -> Dropout(0.3) -> SAGEConv(64 -> 64) -> ReLU -> Linear(64 -> 2).
SAGEConv with the mean aggregator, as in PyG: out_i = W_l · mean_{j in N(i)} h_j + b_l + W_r · h_i.
Loss: class-weighted cross-entropy (PyTorch's weighted mean), Adam with L2 weight decay, lr 0.01, wd 5e-4.

It works on edge lists (src -> dst; pass both directions for an undirected graph), so it scales to Elliptic's
200k nodes without a dense mask, and needs no PyTorch: the app stays light enough for a free Render instance.
"""
from __future__ import annotations

import numpy as np


def _glorot(rng, fan_in, fan_out):
    a = np.sqrt(6.0 / (fan_in + fan_out))
    return rng.uniform(-a, a, size=(fan_in, fan_out))


class MeanAgg:
    """mean_{j in N(i)} h_j over an edge list, and its transpose (for backprop)."""

    def __init__(self, src, dst, n):
        self.src, self.dst, self.n = np.asarray(src, dtype=np.int64), np.asarray(dst, dtype=np.int64), n
        self.deg = np.bincount(self.dst, minlength=n).astype(float)
        self.inv = np.where(self.deg > 0, 1.0 / np.maximum(self.deg, 1), 0.0)

    def __call__(self, H):
        out = np.zeros((self.n, H.shape[1]))
        np.add.at(out, self.dst, H[self.src])
        return out * self.inv[:, None]

    def T(self, G):
        Gs = G * self.inv[:, None]
        out = np.zeros((self.n, G.shape[1]))
        np.add.at(out, self.src, Gs[self.dst])
        return out


class SAGE:
    def __init__(self, in_dim, hidden=64, n_classes=2, dropout=0.3, seed=0):
        rng = np.random.default_rng(seed)
        self.rng = np.random.default_rng(seed + 1)
        self.dropout = dropout
        self.Wl1, self.bl1, self.Wr1 = _glorot(rng, in_dim, hidden), np.zeros(hidden), _glorot(rng, in_dim, hidden)
        self.Wl2, self.bl2, self.Wr2 = _glorot(rng, hidden, hidden), np.zeros(hidden), _glorot(rng, hidden, hidden)
        self.W3, self.b3 = _glorot(rng, hidden, n_classes), np.zeros(n_classes)
        self.history = []

    def params(self):
        return [self.Wl1, self.bl1, self.Wr1, self.Wl2, self.bl2, self.Wr2, self.W3, self.b3]

    # ------------------------------------------------------------- forward
    def forward(self, X, agg, train=False):
        c = {"X": X, "aX": agg(X)}
        c["Z1"] = c["aX"] @ self.Wl1 + self.bl1 + X @ self.Wr1
        H1 = np.maximum(c["Z1"], 0)
        if train and self.dropout > 0:
            c["D1"] = (self.rng.random(H1.shape) >= self.dropout) / (1 - self.dropout)
            H1 = H1 * c["D1"]
        c["H1"] = H1
        c["aH1"] = agg(H1)
        c["Z2"] = c["aH1"] @ self.Wl2 + self.bl2 + H1 @ self.Wr2
        c["H2"] = np.maximum(c["Z2"], 0)
        logits = c["H2"] @ self.W3 + self.b3
        self._cache = c
        return logits

    def embed(self, X, agg):
        self.forward(X, agg)
        return self._cache["H2"]

    @staticmethod
    def softmax(z):
        e = np.exp(z - z.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)

    # ------------------------------------------------------------ backward
    def backward(self, dlogits, agg):
        c = self._cache
        g = {}
        g["W3"] = c["H2"].T @ dlogits
        g["b3"] = dlogits.sum(0)
        dZ2 = (dlogits @ self.W3.T) * (c["Z2"] > 0)
        g["Wl2"] = c["aH1"].T @ dZ2
        g["bl2"] = dZ2.sum(0)
        g["Wr2"] = c["H1"].T @ dZ2
        dH1 = agg.T(dZ2 @ self.Wl2.T) + dZ2 @ self.Wr2.T
        if "D1" in c:
            dH1 = dH1 * c["D1"]
        dZ1 = dH1 * (c["Z1"] > 0)
        g["Wl1"] = c["aX"].T @ dZ1
        g["bl1"] = dZ1.sum(0)
        g["Wr1"] = c["X"].T @ dZ1
        return [g[k] for k in ("Wl1", "bl1", "Wr1", "Wl2", "bl2", "Wr2", "W3", "b3")]

    def loss_and_grads(self, X, agg, y, train_idx, class_weight, train=True):
        logits = self.forward(X, agg, train=train)
        P = self.softmax(logits)
        w = class_weight[y[train_idx]]
        W = w.sum()
        loss = -np.sum(w * np.log(P[train_idx, y[train_idx]] + 1e-12)) / W
        d = np.zeros_like(P)
        d[train_idx] = P[train_idx]
        d[train_idx, y[train_idx]] -= 1
        d[train_idx] *= (w / W)[:, None]
        return loss, self.backward(d, agg), P

    # --------------------------------------------------------------- train
    def fit(self, X, src, dst, y, train_idx, epochs=200, lr=0.01, wd=5e-4, class_weight=None):
        agg = MeanAgg(src, dst, len(X))
        y = np.asarray(y)
        if class_weight is None:  # weight the fraud class by n_normal / n_fraud on the training nodes
            n1 = max(int((y[train_idx] == 1).sum()), 1)
            class_weight = np.array([1.0, float((y[train_idx] == 0).sum()) / n1])
        params = self.params()
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        for t in range(1, epochs + 1):
            loss, grads, _ = self.loss_and_grads(X, agg, y, train_idx, class_weight)
            for i, (p, gr) in enumerate(zip(params, grads)):
                gr = gr + wd * p  # PyTorch Adam's weight_decay: L2 added to the gradient
                m[i] = 0.9 * m[i] + 0.1 * gr
                v[i] = 0.999 * v[i] + 0.001 * gr * gr
                p -= lr * (m[i] / (1 - 0.9 ** t)) / (np.sqrt(v[i] / (1 - 0.999 ** t)) + 1e-8)
            if t % 50 == 0 or t == epochs:
                self.history.append({"epoch": t, "loss": round(float(loss), 4)})
        return self.history

    def predict_proba(self, X, src, dst):
        return self.softmax(self.forward(X, MeanAgg(src, dst, len(X))))[:, 1]


def mask_edges(mask):
    """Directed edge lists (both directions) from a dense boolean adjacency mask, self-loops dropped."""
    m = mask.copy()
    np.fill_diagonal(m, False)
    src, dst = np.nonzero(m)
    return src, dst


class SageMaskModel:
    """GraphSAGE behind the same interface as the app's GAT (fit / predict_proba on a dense mask)."""
    kind = "graphsage"

    def __init__(self, in_dim, hidden=64, seed=7):
        self.net = SAGE(in_dim, hidden=hidden, seed=seed)
        self.n_heads = 0
        self.skip = False

    def fit(self, X, mask, y, train_idx, val_idx, epochs=200, lr=0.01):
        src, dst = mask_edges(mask)
        return self.net.fit(X, src, dst, y, train_idx, epochs=epochs, lr=lr)

    def predict_proba(self, X, mask):
        src, dst = mask_edges(mask)
        return self.net.predict_proba(X, src, dst)

    def embed(self, X, mask):
        src, dst = mask_edges(mask)
        return self.net.embed(X, MeanAgg(src, dst, len(X)))
