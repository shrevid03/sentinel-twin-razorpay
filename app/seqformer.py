"""A small transaction-sequence transformer in NumPy (forward + backward + Adam), gradient-checked.

Inspired by Razorpay's Vulcan, a transformer foundation model for payments; this is our own tiny model and does not
use Vulcan. The graph (GAT / GraphSAGE) sees WHO an account is linked to; this sees HOW it behaves over time.

Input: each account's most recent events (orders and money transfers, oldest first) as tokens. Every event has six
categorical fields, each with its own embedding table, summed with a fixed sinusoidal position encoding:
    kind        order / transfer out / transfer in
    amount      log-scale bucket (8)
    category    merchant category, or "transfer"
    new_device  first time this account used this device (for orders)
    new_place   IP country differs from the account's country (orders), or a first-time counterparty (transfers)
    gap         time since the previous event, log bucket (7)
A learned [CLS] vector sits at position 0. One encoder layer: 2-head self-attention (padding masked) with a residual,
then a ReLU feed-forward block with a residual. The [CLS] output is the account's behaviour embedding, and a logistic
head on it gives a sequence risk score. Trained with class-weighted cross-entropy on training-split accounts only.
"""
from __future__ import annotations

import math

import numpy as np

CATS = ["coffee", "electronics", "books", "outdoor", "home", "apparel", "gift_cards", "plants", "gaming", "kitchen"]
FIELDS = {"kind": 4, "amount": 9, "category": len(CATS) + 2, "new_device": 3, "new_place": 3, "gap": 8}  # 0 = pad
MAX_EVENTS = 16


def _amount_bucket(inr):
    return int(min(8, max(1, 1 + math.floor(math.log(max(inr, 1.0) / 250.0, 3)))))  # ₹250, 750, 2.2k, 6.7k, 20k, 60k...


def _gap_bucket(minutes):
    if minutes is None:
        return 1
    return int(min(7, 2 + math.floor(math.log(max(minutes, 1.0), 6))))  # <6m, <36m, <3.6h, <21h, <5d, >5d


def all_account_events(twin, extra=None):
    """account_events for every account in one pass over orders and transfers (fast enough to run per score)."""
    by = {a: [] for a in twin.accounts}
    for o in twin.orders:
        if o["status"] != "blocked" and o["account"] in by:
            by[o["account"]].append({"ts": o["ts"], "kind": 1, "amount": o["amount"], "merchant": o["merchant"],
                                     "device": o.get("device"), "ip_country": o.get("ip_country"), "label": f"order {o['id']}"})
    if extra:
        by[extra["account"]].append({"ts": twin.clock, "kind": 1, "amount": extra["amount"], "merchant": extra["merchant"],
                                     "device": extra.get("device"), "ip_country": twin.ips.get(extra.get("ip")),
                                     "label": "this order"})
    for i, t in enumerate(twin.transfers):
        if t["src"] in by:
            by[t["src"]].append({"ts": t["ts"], "kind": 2, "amount": t["amount"], "peer": t["dst"], "label": f"transfer out T-{i + 1:05d}"})
        if t["dst"] in by:
            by[t["dst"]].append({"ts": t["ts"], "kind": 3, "amount": t["amount"], "peer": t["src"], "label": f"transfer in T-{i + 1:05d}"})
    return {a: _tokens(twin, a, evs) for a, evs in by.items()}


def account_events(twin, account, extra=None):
    """The account's recent events as token dicts (oldest first), plus readable labels for the UI."""
    evs = []
    acc = twin.accounts[account]
    for o in twin.orders:
        if o["account"] == account and o["status"] != "blocked":
            evs.append({"ts": o["ts"], "kind": 1, "amount": o["amount"], "merchant": o["merchant"], "device": o.get("device"),
                        "ip_country": o.get("ip_country", acc.country), "label": f"order {o['id']}"})
    if extra:
        evs.append({"ts": twin.clock, "kind": 1, "amount": extra["amount"], "merchant": extra["merchant"],
                    "device": extra.get("device"), "ip_country": twin.ips.get(extra.get("ip"), acc.country),
                    "label": "this order"})
    for i, t in enumerate(twin.transfers):
        if t["src"] == account:
            evs.append({"ts": t["ts"], "kind": 2, "amount": t["amount"], "peer": t["dst"], "label": f"transfer out T-{i + 1:05d}"})
        elif t["dst"] == account:
            evs.append({"ts": t["ts"], "kind": 3, "amount": t["amount"], "peer": t["src"], "label": f"transfer in T-{i + 1:05d}"})
    return _tokens(twin, account, evs)


def _tokens(twin, account, evs):
    acc = twin.accounts[account]
    evs = sorted(evs, key=lambda e: e["ts"])
    seen_dev, seen_peer, prev = set(), set(), None
    toks = []
    for e in evs:
        if e["kind"] == 1:
            cat = twin.merchants.get(e["merchant"], {}).get("category")
            c = CATS.index(cat) + 2 if cat in CATS else 1
            nd = 2 if (e["device"] and e["device"] not in seen_dev and seen_dev) else 1
            seen_dev.add(e["device"])
            npl = 2 if (e["ip_country"] or acc.country) != acc.country else 1
        else:
            c = 1
            nd = 1
            npl = 2 if (e["peer"] not in seen_peer and seen_peer) else 1
            seen_peer.add(e["peer"])
        toks.append({"kind": e["kind"], "amount": _amount_bucket(e["amount"]), "category": c, "new_device": nd,
                     "new_place": npl, "gap": _gap_bucket(None if prev is None else e["ts"] - prev),
                     "label": e["label"], "inr": round(e["amount"], 2)})
        prev = e["ts"]
    return toks[-MAX_EVENTS:]


def encode(seqs):
    """List of token lists -> int array (B, L, F) with padding 0, and a key mask (B, L+1) incl. [CLS]."""
    B, L = len(seqs), MAX_EVENTS
    ids = np.zeros((B, L, len(FIELDS)), dtype=np.int64)
    mask = np.zeros((B, L + 1), dtype=bool)
    mask[:, 0] = True
    for b, s in enumerate(seqs):
        for j, t in enumerate(s[-L:]):
            ids[b, j] = [t[f] for f in FIELDS]
            mask[b, j + 1] = True
    return ids, mask


def _posenc(L, d):
    pos = np.arange(L)[:, None]
    i = np.arange(d)[None, :]
    ang = pos / np.power(10000, (2 * (i // 2)) / d)
    return np.where(i % 2 == 0, np.sin(ang), np.cos(ang))


class SeqFormer:
    def __init__(self, d=16, heads=2, ff=32, seed=0):
        r = np.random.default_rng(seed)
        self.d, self.h, self.dh = d, heads, d // heads
        g = lambda *s: r.normal(0, 1 / math.sqrt(s[0]), size=s)  # noqa: E731
        self.emb = {f: r.normal(0, 0.3, size=(n, d)) for f, n in FIELDS.items()}
        for f in self.emb:
            self.emb[f][0] = 0.0  # padding contributes nothing
        self.cls = r.normal(0, 0.3, size=d)
        self.Wq, self.Wk, self.Wv, self.Wo = g(d, d), g(d, d), g(d, d), g(d, d)
        self.W1, self.b1, self.W2, self.b2 = g(d, ff), np.zeros(ff), g(ff, d), np.zeros(d)
        self.w, self.b = r.normal(0, 0.1, size=d), 0.0
        self.P = _posenc(MAX_EVENTS + 1, d)

    def params(self):
        return [*self.emb.values(), self.cls, self.Wq, self.Wk, self.Wv, self.Wo, self.W1, self.b1, self.W2, self.b2,
                self.w, np.array([self.b])]

    # ------------------------------------------------------------- forward
    def forward(self, ids, mask):
        B, L, _ = ids.shape
        E = np.zeros((B, L + 1, self.d))
        for k, f in enumerate(FIELDS):
            E[:, 1:] += self.emb[f][ids[:, :, k]]
        E[:, 0] = self.cls
        E = E + self.P[None, : L + 1]
        Q = (E @ self.Wq).reshape(B, L + 1, self.h, self.dh).transpose(0, 2, 1, 3)
        Kk = (E @ self.Wk).reshape(B, L + 1, self.h, self.dh).transpose(0, 2, 1, 3)
        V = (E @ self.Wv).reshape(B, L + 1, self.h, self.dh).transpose(0, 2, 1, 3)
        S = Q @ Kk.transpose(0, 1, 3, 2) / math.sqrt(self.dh)
        S = np.where(mask[:, None, None, :], S, -1e9)
        S = S - S.max(-1, keepdims=True)
        A = np.exp(S)
        A = A / A.sum(-1, keepdims=True)
        O = (A @ V).transpose(0, 2, 1, 3).reshape(B, L + 1, self.d)
        X1 = E + O @ self.Wo
        Hpre = X1 @ self.W1 + self.b1
        H = np.maximum(Hpre, 0)
        X2 = X1 + H @ self.W2 + self.b2
        c = X2[:, 0]
        z = c @ self.w + self.b
        self._c = dict(ids=ids, mask=mask, E=E, Q=Q, K=Kk, V=V, A=A, O=O, X1=X1, Hpre=Hpre, H=H, X2=X2, cls=c)
        return z

    def embed(self, ids, mask):
        self.forward(ids, mask)
        return self._c["cls"]

    def attention_from_cls(self):
        """Average attention of the [CLS] query over events (heads averaged), per sequence."""
        return self._c["A"][:, :, 0, 1:].mean(1)

    # ------------------------------------------------------------ backward
    def backward(self, dz):
        c = self._c
        B, L1, d = c["E"].shape
        g = {}
        g["w"] = c["cls"].T @ dz
        g["b"] = np.array([dz.sum()])
        dX2 = np.zeros_like(c["X2"])
        dX2[:, 0] = dz[:, None] * self.w[None, :]
        g["W2"] = np.einsum("blf,bld->fd", c["H"], dX2)
        g["b2"] = dX2.sum((0, 1))
        dH = dX2 @ self.W2.T
        dHpre = dH * (c["Hpre"] > 0)
        g["W1"] = np.einsum("bld,blf->df", c["X1"], dHpre)
        g["b1"] = dHpre.sum((0, 1))
        dX1 = dX2 + dHpre @ self.W1.T
        g["Wo"] = np.einsum("bld,ble->de", c["O"], dX1)
        dO = (dX1 @ self.Wo.T).reshape(B, L1, self.h, self.dh).transpose(0, 2, 1, 3)
        dA = dO @ c["V"].transpose(0, 1, 3, 2)
        dV = c["A"].transpose(0, 1, 3, 2) @ dO
        dS = c["A"] * (dA - (dA * c["A"]).sum(-1, keepdims=True)) / math.sqrt(self.dh)
        dQ = dS @ c["K"]
        dK = dS.transpose(0, 1, 3, 2) @ c["Q"]
        merge = lambda t: t.transpose(0, 2, 1, 3).reshape(B, L1, d)  # noqa: E731
        dQ, dK, dV = merge(dQ), merge(dK), merge(dV)
        g["Wq"] = np.einsum("bld,ble->de", c["E"], dQ)
        g["Wk"] = np.einsum("bld,ble->de", c["E"], dK)
        g["Wv"] = np.einsum("bld,ble->de", c["E"], dV)
        dE = dX1 + dQ @ self.Wq.T + dK @ self.Wk.T + dV @ self.Wv.T
        g["cls"] = dE[:, 0].sum(0)
        gemb = {}
        for k, f in enumerate(FIELDS):
            ge = np.zeros_like(self.emb[f])
            np.add.at(ge, c["ids"][:, :, k].ravel(), dE[:, 1:].reshape(-1, d))
            ge[0] = 0.0
            gemb[f] = ge
        return [*(gemb[f] for f in FIELDS), g["cls"], g["Wq"], g["Wk"], g["Wv"], g["Wo"], g["W1"], g["b1"], g["W2"],
                g["b2"], g["w"], g["b"]]

    def loss_and_grads(self, ids, mask, y, cw):
        z = self.forward(ids, mask)
        p = 1 / (1 + np.exp(-z))
        w = cw[y]
        loss = -np.sum(w * (y * np.log(p + 1e-12) + (1 - y) * np.log(1 - p + 1e-12))) / w.sum()
        dz = w * (p - y) / w.sum()
        return loss, self.backward(dz)

    def fit(self, ids, mask, y, epochs=150, lr=0.01, wd=1e-4):
        y = np.asarray(y).astype(int)
        n1 = max(int(y.sum()), 1)
        cw = np.array([1.0, (len(y) - n1) / n1])
        params = self.params()
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        for t in range(1, epochs + 1):
            _, grads = self.loss_and_grads(ids, mask, y, cw)
            for i, (p, gr) in enumerate(zip(params, grads)):
                gr = gr + wd * p
                m[i] = 0.9 * m[i] + 0.1 * gr
                v[i] = 0.999 * v[i] + 0.001 * gr * gr
                p -= lr * (m[i] / (1 - 0.9 ** t)) / (np.sqrt(v[i] / (1 - 0.999 ** t)) + 1e-8)
            self.b = float(params[-1][0])
            for f in self.emb:
                self.emb[f][0] = 0.0
        return self

    def predict(self, ids, mask):
        return 1 / (1 + np.exp(-self.forward(ids, mask)))
