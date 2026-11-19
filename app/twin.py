"""The digital twin: a live graph model of a Razorpay merchant ecosystem.

Every buyer account, device, IP address, payment identifier and merchant is a node. Edges link an
account to the devices and IPs it uses, the payment identifiers it pays with (UPI ID, card fingerprint,
hashed contact) and the merchants it buys from.
Orders created through the app (and Razorpay webhooks) are mirrored into
the twin, a GAT scores every account from its neighbourhood, and the twin can
be forked so red-team attacks and what-if policies run without touching the
real state.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .gat import GAT, auc
from .money import K, rs

COUNTRIES = ["US", "US", "US", "IN", "GB", "DE", "CA", "AU", "FR", "BR"]
RISKY_COUNTRIES = ["NG", "RU", "VN", "RO"]
EMAIL_DOMAINS = ["gmail.com", "outlook.com", "yahoo.com", "icloud.com", "proton.me"]
DISPOSABLE = ["mailinator.com", "tempmail.dev", "10minute.email"]
FIRST = ["Ava", "Liam", "Noah", "Mia", "Zoe", "Arjun", "Priya", "Sofia", "Leo", "Emma", "Kai", "Isha",
         "Omar", "Lena", "Maya", "Ravi", "Nina", "Jon", "Tara", "Ben", "Aria", "Dev", "Ella", "Sam"]
LAST = ["Shah", "Kim", "Lopez", "Patel", "Nguyen", "Smith", "Rossi", "Khan", "Meyer", "Silva",
        "Brown", "Iyer", "Cohen", "Ali", "Wong", "Park"]
MERCHANTS = [
    ("M-01", "Brewline Coffee Co.", "coffee"), ("M-02", "Pixel Forge Electronics", "electronics"),
    ("M-03", "Northleaf Books", "books"), ("M-04", "Atlas Outdoor Gear", "outdoor"),
    ("M-05", "Lumen Home Studio", "home"), ("M-06", "Kinetic Sportswear", "apparel"),
    ("M-07", "GiftCard Galaxy", "gift_cards"), ("M-08", "Verde Plant Shop", "plants"),
    ("M-09", "Orbit Gaming Store", "gaming"), ("M-10", "Saffron Kitchen Supply", "kitchen"),
]
FEATURES = ["is_account", "is_device", "is_ip", "is_merchant", "account_age", "order_count",
            "avg_amount", "max_amount", "velocity", "device_count", "ip_count", "geo_mismatch",
            "kyc_verified", "disposable_email", "degree", "is_payment_id", "payment_id_count",
            "xfer_out", "xfer_in", "xfer_amount", "xfer_partners"]

# Money-flow ring patterns: accounts also move money to each other
# (P2P transfers / marketplace payouts). Each member looks ordinary on its own row; the shape gives it away.
FLOW_PATTERNS = {
    "cycle": "layering: money goes round a closed loop A -> B -> ... -> A",
    "fanout": "bust-out: one hub pays out to many fresh mule accounts in a short window",
    "collusion": "fake demand: a cluster of buyers all pay a cluster of colluding sellers",
}

# Payment identifiers, linked to accounts the way devices and IPs are. Node ids never contain raw PII:
# synthetic ones are counters, real ones (mirrored from Razorpay payments) are salted hashes.
PAY_KINDS = {"UPI": "UPI ID", "CARD": "card", "CT": "phone/email"}

# Hybrid risk: how much the known-fraud-neighbour share can add on top of the GAT. The real-data tests
# (docs/REAL_DATA.md) found a "share of neighbours that are known fraud" feature the strongest single graph
# signal for new accounts; it reads only labels the model trained on plus investigator-frozen accounts.
NEIGHBOUR_WEIGHT = 0.8
# The skip connection (A @ Z + Z) helped the GAT on all three real fraud graphs, but on this twin it adds nothing
# to unseen-attack interception (84.9% vs 85.2%) and blocks one good ₹14,975 order in every run, so the app keeps
# it off. Flip to True to compare; the drill and rules-vs-graph numbers in the README are with it off.
GAT_SKIP = False

DEFAULT_POLICY = {"review_threshold": 0.5, "block_threshold": 0.8, "max_auto_amount": 1000.0 * K,
                  "review_new_device_over": 300.0 * K, "review_spend_multiple": 0.0}


@dataclass
class Account:
    id: str
    name: str
    country: str
    age_days: int
    email: str
    kyc: bool
    fraud: bool = False
    segment: str = "legit"
    status: str = "active"
    devices: set = field(default_factory=set)
    ips: set = field(default_factory=set)
    pay_ids: set = field(default_factory=set)


class Twin:
    def __init__(self, seed: int = 11):
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.clock = 0.0  # simulated minutes
        self.accounts: dict[str, Account] = {}
        self.devices: set[str] = set()
        self.ips: dict[str, str] = {}  # ip -> country
        self.pay_ids: set[str] = set()  # UPI-…, CARD-…, CT-… nodes
        # payment identifiers draw from their own RNG so adding them leaves the rest of the seeded world unchanged
        self.prng = random.Random(seed * 7919 + 1)
        self.xrng = random.Random(seed * 104729 + 3)  # money-flow world: its own RNG, like payment ids
        self.transfers: list[dict] = []  # {"src", "dst", "amount", "ts", "pattern"}
        self.xfer_nb: dict[str, set] = {}  # account -> accounts it has sent money to or received from
        self.merchants = {m[0]: {"id": m[0], "name": m[1], "category": m[2]} for m in MERCHANTS}
        self.orders: list[dict] = []
        self.events: list[dict] = []
        self.policy = dict(DEFAULT_POLICY)
        self.model: GAT | None = None
        self.metrics: dict = {}
        self._probs: dict[str, float] = {}
        self.known_labelled: set[str] = set()  # accounts whose label the model trained on
        self.known_fraud: set[str] = set()  # ...of which fraud
        self._dirty = True
        self._n = {"A": 0, "D": 0, "IP": 0, "O": 0, "UPI": 0, "CARD": 0, "CT": 0}
        self._populate()

    # ----------------------------------------------------------- id helpers
    def _next(self, kind):
        self._n[kind] = self._n.get(kind, 0) + 1
        return f"{kind}-{self._n[kind]:03d}" if kind != "O" else f"ORD-{self._n[kind]:05d}"

    def new_device(self):
        d = self._next("D")
        self.devices.add(d)
        return d

    def new_ip(self, country):
        ip = self._next("IP")
        self.ips[ip] = country
        return ip

    def new_pay_id(self, kind):
        """A synthetic payment identifier node: kind is UPI, CARD or CT (contact)."""
        pid = self._next(kind)
        self.pay_ids.add(pid)
        return pid

    def give_pay_ids(self, acc, upi=0.85, card=0.6, contact=1.0):
        r = self.prng
        if r.random() < upi:
            acc.pay_ids.add(self.new_pay_id("UPI"))
        if r.random() < card:
            acc.pay_ids.add(self.new_pay_id("CARD"))
        if r.random() < contact:
            acc.pay_ids.add(self.new_pay_id("CT"))

    def link_pay_ids(self, account, ids, source="razorpay"):
        """Mirror payment identifiers (already hashed node ids) onto an account, e.g. from a captured payment."""
        with self.lock:
            acc = self.accounts[account]
            new = [i for i in ids if i and i.split("-")[0] in PAY_KINDS and i not in acc.pay_ids]
            for i in new:
                self.pay_ids.add(i)
                acc.pay_ids.add(i)
            if new:
                self._dirty = True
                self._event("razorpay", f"Mirrored {len(new)} payment identifier(s) from {source} onto {account} "
                                        f"({', '.join(PAY_KINDS[i.split('-')[0]] for i in new)}; hashed, no raw PII)")
            return new

    def engine_risk(self, account):
        """Account-level risk as the shipped engine sees it: the model's score combined with the known-fraud share."""
        g = float(self.risk(account))
        return round(1 - (1 - g) * (1 - NEIGHBOUR_WEIGHT * self.known_fraud_signal(account)["share"]), 3)

    def link_payer(self, account, links, source="razorpay"):
        """Mirror a captured payment's hashed identifiers (payment ids, device, IP) onto the account, then re-score
        it with the live model, so the very next order from a shared UPI ID, card, device or IP is linked."""
        with self.lock:
            before = self.engine_risk(account)
            new = self.link_pay_ids(account, links.get("pay_ids") or [], source=source)
            acc = self.accounts[account]
            if links.get("device") and links["device"] not in acc.devices:
                self.devices.add(links["device"])
                acc.devices.add(links["device"])
                new.append(links["device"])
            if links.get("ip") and links["ip"] not in acc.ips:
                self.ips.setdefault(links["ip"], acc.country)
                acc.ips.add(links["ip"])
                new.append(links["ip"])
            self._dirty = True
            after = self.engine_risk(account)
            shared = [s for s in self.shared_identifiers(account) if s["id"] in new]
            if any(s["flagged"] for s in shared):
                self._event("razorpay", f"{account}'s payment links it to "
                            + ", ".join(f"{s['flagged']} flagged account(s) via a {s['label']}" for s in shared if s["flagged"])
                            + f"; risk {before:.2f} -> {after:.2f}", level="warn")
            return {"linked": new, "risk_before": before, "risk_after": after, "shared": shared}

    def new_account(self, *, country=None, age_days=None, fraud=False, segment="legit",
                    disposable=False, kyc=None, name=None):
        r = self.rng
        aid = self._next("A")
        name = name or f"{r.choice(FIRST)} {r.choice(LAST)}"
        dom = r.choice(DISPOSABLE) if disposable else r.choice(EMAIL_DOMAINS)
        acc = Account(
            id=aid, name=name, country=country or r.choice(COUNTRIES),
            age_days=age_days if age_days is not None else int(r.expovariate(1 / 500)) + 3,
            email=f"{name.split()[0].lower()}.{aid[-3:]}@{dom}",
            kyc=(r.random() < 0.85) if kyc is None else kyc, fraud=fraud, segment=segment,
        )
        self.accounts[aid] = acc
        self._dirty = True
        return acc

    # ------------------------------------------------------- initial world
    # ------------------------------------------------------ policy version
    @property
    def policy(self):
        return self._policy

    @policy.setter
    def policy(self, value):
        self._policy = dict(value)
        self._policy_applied_at = time.strftime("%Y-%m-%d %H:%M:%S")

    def policy_version(self):
        """Short content hash of the live decision policy plus when it was applied (for receipts)."""
        h = hashlib.sha256(json.dumps(self._policy, sort_keys=True).encode()).hexdigest()[:8]
        return {"hash": h, "applied_at": getattr(self, "_policy_applied_at", None), "policy": dict(self._policy)}

    def _populate(self):
        r = self.rng
        cafe_ips = [self.new_ip("US"), self.new_ip("GB")]  # shared public wifi: hard negatives
        households = []
        for _ in range(170):
            if households and r.random() < 0.2:  # family members share a device and IP
                fam = r.choice(households)
                acc = self.new_account(country=fam.country)
                acc.devices |= set(sorted(fam.devices)[:1])
                acc.ips |= set(fam.ips)
                fam_cards = sorted(i for i in fam.pay_ids if i.startswith("CARD-"))
                if fam_cards and self.prng.random() < 0.4:  # a shared family card: a hard negative
                    acc.pay_ids.add(fam_cards[0])
                self.give_pay_ids(acc, card=0.3)
            else:
                acc = self.new_account()
                acc.devices.add(self.new_device())
                if r.random() < 0.3:
                    acc.devices.add(self.new_device())
                acc.ips.add(self.new_ip(acc.country))
                self.give_pay_ids(acc)
                households.append(acc)
            if r.random() < 0.12:
                acc.ips.add(r.choice(cafe_ips))
            for _ in range(r.randint(1, 6)):
                self._hist_order(acc, r.choice(sorted(acc.devices)), r.choice(sorted(acc.ips)))
        for ring in range(5):
            self.spawn_ring(size=r.randint(5, 8), label=f"ring-{ring + 1}", history=True)
        for _ in range(10):  # low-and-slow synthetic identities: look clean alone
            acc = self.new_account(age_days=r.randint(60, 300), fraud=True, segment="synthetic", kyc=True)
            acc.devices.add(self.new_device())
            acc.ips.add(self.new_ip(acc.country))
            self.give_pay_ids(acc, upi=1.0)
            for _ in range(r.randint(2, 4)):
                self._hist_order(acc, min(acc.devices), min(acc.ips),
                                 amount=r.uniform(80 * K, 400 * K), fraud=True)
        self._populate_flows()
        self.orders.sort(key=lambda o: o["ts"])

    # ---------------------------------------------------------- money flows
    def add_transfer(self, src, dst, amount, ts=None, pattern="none"):
        if src == dst:
            return
        self.transfers.append({"src": src, "dst": dst, "amount": round(float(amount), 2),
                               "ts": self.clock if ts is None else ts, "pattern": pattern})
        self.xfer_nb.setdefault(src, set()).add(dst)
        self.xfer_nb.setdefault(dst, set()).add(src)
        self._dirty = True

    def _flow_amount(self, r):
        return round(r.lognormvariate(7.6, 0.8), 2)  # about ₹2,000, the same for rings and ordinary accounts

    def _ring_member(self, label, r, device_pool=None, share=0.3):
        """An ordinary-looking ring member: aged, KYC'd, normal email, own phone and IP, normal payment ids."""
        acc = self.new_account(age_days=r.randint(60, 1200), fraud=True, segment=label, kyc=r.random() < 0.85)
        acc.devices.add(device_pool if device_pool and r.random() < share else self.new_device())
        acc.ips.add(self.new_ip(acc.country))
        self.give_pay_ids(acc)
        for _ in range(r.randint(1, 3)):
            self._hist_order(acc, min(acc.devices), min(acc.ips), amount=r.uniform(20 * K, 60 * K), fraud=True)
        return acc

    def spawn_flow_ring(self, pattern, size=8, label=None, history=True, hub=None, sellers=None):
        """Inject one money-flow ring. Returns its member accounts (and, for collusion, buyers then sellers)."""
        r = self.rng
        label = label or pattern
        pool = self.new_device()
        t0 = -r.uniform(0, 60 * 24 * 20) if history else self.clock
        if pattern == "cycle":
            members = [self._ring_member(label, r, pool) for _ in range(size)]
            for i in range(size):
                self.add_transfer(members[i].id, members[(i + 1) % size].id, self._flow_amount(r), t0 + i * 3, pattern)
        elif pattern == "fanout":
            h = hub or self._ring_member(label, r, pool)
            mules = [self._ring_member(label, r, pool) for _ in range(size)]
            for m in mules:
                self.add_transfer(h.id, m.id, self._flow_amount(r), t0 + r.uniform(0, 30), pattern)
            members = ([h] if hub is None else []) + mules
        elif pattern == "collusion":
            nb = max(2, size // 2)
            buyers = [self._ring_member(label, r, pool, 0.2) for _ in range(nb)]
            sell = sellers or [self._ring_member(label, r, pool, 0.2) for _ in range(size - nb)]
            for b in buyers:
                for s_ in sell:
                    if r.random() < 0.8:
                        self.add_transfer(b.id, s_.id, self._flow_amount(r), t0 + r.uniform(0, 40), pattern)
            members = buyers + ([] if sellers else sell)
        else:
            raise ValueError(f"unknown flow pattern {pattern}")
        return members

    def _populate_flows(self):
        """Ordinary transfers between legit accounts, plus cycle, fan-out and collusion rings."""
        saved, self.rng = self.rng, self.xrng  # keep the rest of the seeded world exactly as it was
        try:
            r = self.rng
            legit = [a for a in self.accounts.values() if not a.fraud]
            others = [a for a in self.accounts.values() if a.fraud]  # device rings and synthetic identities
            # many people never use P2P transfers at all; the rest send money to a friend or two. Fraud accounts of
            # the older kinds transfer at the same rate, so "has no transfers" is not itself a fraud signal.
            for a in legit + others:
                for _ in range(r.choice([0, 0, 0, 0, 1, 1, 2])):
                    self.add_transfer(a.id, r.choice(legit + others).id, self._flow_amount(r), -r.uniform(0, 60 * 24 * 30))
            # honest hard negatives: flatmates paying one person for rent (fan-in) and friends passing money along
            pool = [a for a in legit if a.age_days > 60]
            for g in range(10):
                group = r.sample(pool, r.randint(3, 6))
                for j, a in enumerate(group[1:]):
                    dst = group[0] if g % 2 == 0 else group[j]  # even groups: rent to one payer; odd: a chain
                    self.add_transfer(a.id, dst.id, self._flow_amount(r), -r.uniform(0, 60 * 24 * 30), "honest-group")
            for i in range(2):
                self.spawn_flow_ring("cycle", size=r.randint(6, 8), label=f"cycle-{i + 1}")
                self.spawn_flow_ring("fanout", size=r.randint(6, 8), label=f"fanout-{i + 1}")
            self.spawn_flow_ring("collusion", size=8, label="collusion-1")
        finally:
            self.rng = saved

    def spawn_ring(self, size=6, label="ring", history=False, shared_devices=2, shared_ips=2,
                   country=None, age_range=(1, 40), amount_range=(150 * K, 900 * K), disposable_rate=0.6):
        r = self.rng
        devs = [self.new_device() for _ in range(shared_devices)]
        ipc = country or r.choice(RISKY_COUNTRIES + ["US"])
        ips = [self.new_ip(ipc) for _ in range(shared_ips)]
        pr = self.prng
        upis = [self.new_pay_id("UPI") for _ in range(pr.randint(1, 2))]
        cards = [self.new_pay_id("CARD")]
        contacts = [self.new_pay_id("CT")]
        members = []
        for _ in range(size):
            acc = self.new_account(country=r.choice(COUNTRIES), age_days=r.randint(*age_range), fraud=True,
                                   segment=label, disposable=r.random() < disposable_rate, kyc=r.random() < 0.3)
            acc.devices.update(r.sample(devs, k=min(len(devs), r.randint(1, 2))))
            acc.ips.update(r.sample(ips, k=min(len(ips), r.randint(1, 2))))
            if pr.random() < 0.8:
                acc.pay_ids.add(pr.choice(upis))
            if pr.random() < 0.5:
                acc.pay_ids.add(cards[0])
            if pr.random() < 0.4:
                acc.pay_ids.add(contacts[0])
            members.append(acc)
            if history:
                for _ in range(r.randint(2, 5)):
                    self._hist_order(acc, r.choice(sorted(acc.devices)), r.choice(sorted(acc.ips)),
                                     amount=r.uniform(*amount_range), fraud=True,
                                     merchant=r.choice(["M-02", "M-07", "M-09", "M-02"]))
        self.last_ring_pay_ids = upis + cards + contacts
        return members, devs, ips

    def _hist_order(self, acc, device, ip, amount=None, fraud=False, merchant=None):
        r = self.rng
        amount = amount or round(r.lognormvariate(3.6, 0.7) * K, 2)
        self.orders.append({
            "id": self._next("O"), "account": acc.id, "merchant": merchant or r.choice(list(self.merchants)),
            "amount": round(min(amount, 4000 * K), 2), "ts": -r.uniform(0, 60 * 24 * 30), "device": device, "ip": ip,
            "ip_country": self.ips.get(ip, acc.country), "status": "captured", "risk": None, "decision": "approve",
            "fraud": fraud, "source": "history", "pay_order_id": None, "factors": [],
        })

    # --------------------------------------------------------------- graph
    def _account_orders(self):
        by = {}
        for o in self.orders:
            if o["status"] != "blocked":
                by.setdefault(o["account"], []).append(o)
        return by

    def build_graph(self, extra=None):
        """Return node ids, feature matrix X and adjacency mask.

        extra: optional dict(account, device, ip, merchant, amount) for a tentative
        order that is scored without being committed.
        """
        accs = list(self.accounts.values())
        # a payment identifier only adds information when two or more accounts share it, so single-holder ones stay
        # out of the dense graph (they still count in the account's payment_id_count feature)
        holders = {}
        for a in accs:
            for pid in a.pay_ids:
                holders[pid] = holders.get(pid, 0) + 1
        extra_ids = set((extra or {}).get("pay_ids") or [])
        if extra:
            for pid in extra_ids - self.accounts[extra["account"]].pay_ids:
                holders[pid] = holders.get(pid, 0) + 1
        shared_pay = sorted(pid for pid, c in holders.items() if c >= 2)
        nodes = [a.id for a in accs] + sorted(self.devices) + sorted(self.ips) + shared_pay + list(self.merchants)
        idx = {n: i for i, n in enumerate(nodes)}
        N = len(nodes)
        mask = np.eye(N, dtype=bool)
        by = self._account_orders()
        if extra:
            by = dict(by)
            by[extra["account"]] = by.get(extra["account"], []) + [
                {"amount": extra["amount"], "ts": self.clock, "merchant": extra["merchant"],
                 "ip_country": self.ips.get(extra["ip"], "US")}]

        def link(a, b):
            i, j = idx[a], idx[b]
            mask[i, j] = mask[j, i] = True

        for a in accs:
            devs, ips = set(a.devices), set(a.ips)
            if extra and extra["account"] == a.id:
                devs.add(extra["device"])
                ips.add(extra["ip"])
            for d in devs:
                link(a.id, d)
            for ip in ips:
                link(a.id, ip)
            for pid in a.pay_ids | (extra_ids if extra and extra["account"] == a.id else set()):
                if pid in idx:
                    link(a.id, pid)
            for o in by.get(a.id, []):
                link(a.id, o["merchant"])
            for b in self.xfer_nb.get(a.id, ()):  # money moved between the two accounts
                if b in idx:
                    link(a.id, b)

        X = np.zeros((N, len(FEATURES)))
        xstat = {}
        for t in self.transfers:
            o_ = xstat.get(t["src"], (0, 0, 0.0))
            xstat[t["src"]] = (o_[0] + 1, o_[1], o_[2] + t["amount"])
            i_ = xstat.get(t["dst"], (0, 0, 0.0))
            xstat[t["dst"]] = (i_[0], i_[1] + 1, i_[2] + t["amount"])
        X[:, 0] = [1 if n.startswith("A-") else 0 for n in nodes]
        for i, a in enumerate(accs):
            os_ = by.get(a.id, [])
            amts = [o["amount"] for o in os_] or [0]
            recent = sum(1 for o in os_ if o["ts"] > self.clock - 60)
            mism = np.mean([o["ip_country"] != a.country for o in os_]) if os_ else 0
            n_dev = len(a.devices) + (1 if extra and extra["account"] == a.id and extra["device"] not in a.devices else 0)
            X[i, 4:14] = [math.log1p(a.age_days) / math.log1p(2000), math.log1p(len(os_)) / 3,
                          math.log1p(np.mean(amts) / K) / 8, math.log1p(max(amts) / K) / 8, min(recent / 5, 1),
                          n_dev / 4, len(a.ips) / 4, mism, float(a.kyc),
                          float(a.email.split("@")[1] in DISPOSABLE)]
            X[i, 16] = len(a.pay_ids) / 4
            xo, xi, xa = xstat.get(a.id, (0, 0, 0.0))
            X[i, 17:21] = [min(xo / 5, 2), min(xi / 5, 2), math.log1p(xa / K) / 8, len(self.xfer_nb.get(a.id, ())) / 6]
        deg = mask.sum(axis=1) - 1
        X[:, 14] = np.log1p(deg) / 5
        nA = len(accs)
        for i in range(nA, N):
            n = nodes[i]
            X[i, 1] = n.startswith("D-")
            X[i, 2] = n.startswith("IP-")
            X[i, 3] = n.startswith("M-")
            X[i, 15] = n.split("-")[0] in PAY_KINDS
            if not X[i, 3]:  # devices / IPs summarise the accounts behind them
                nb = np.where(mask[i, :nA])[0]
                if len(nb):
                    X[i, 4:14] = X[nb, 4:14].mean(axis=0)
        return nodes, idx, X, mask

    # --------------------------------------------------------------- model
    def train(self, epochs=150):
        with self.lock:
            nodes, idx, X, mask = self.build_graph()
            accs = [n for n in nodes if n.startswith("A-")]
            y = np.zeros(len(nodes), dtype=int)
            for a in accs:
                y[idx[a]] = int(self.accounts[a].fraud)
            rr = np.random.default_rng(5)
            pos = rr.permutation([idx[a] for a in accs if self.accounts[a].fraud])
            neg = rr.permutation([idx[a] for a in accs if not self.accounts[a].fraud])

            def split(arr):
                a, b = int(len(arr) * 0.6), int(len(arr) * 0.8)
                return arr[:a], arr[a:b], arr[b:]

            (ptr, pva, pte), (ntr, nva, nte) = split(pos), split(neg)
            tr, va, te = np.r_[ptr, ntr], np.r_[pva, nva], np.r_[pte, nte]
            self._split = {"X": X, "y": y, "tr": tr, "va": va, "te": te, "nodes": nodes}
            self.known_labelled = {nodes[i] for i in tr}
            self.known_fraud = {nodes[i] for i in tr if y[i] == 1}
            kind = os.getenv("TWIN_MODEL", "gat").strip().lower()
            if kind == "graphsage":  # GraphSAGE in NumPy (app/sage.py)
                from .sage import SageMaskModel
                self.model = SageMaskModel(len(FEATURES), hidden=64, seed=7)
            else:
                self.model = GAT(len(FEATURES), hidden=8, heads=4, skip=GAT_SKIP)
            hist = self.model.fit(X, mask, y, tr, va, epochs=epochs)
            P = self.model.predict_proba(X, mask)
            te_pred = P[te] >= 0.5
            tp = int(np.sum(te_pred & (y[te] == 1)))
            self.metrics = {
                "test_auc": round(auc(y[te], P[te]), 4),
                "precision": round(tp / max(int(te_pred.sum()), 1), 3),
                "recall": round(tp / max(int((y[te] == 1).sum()), 1), 3),
                "train_nodes": int(len(tr)), "test_nodes": int(len(te)), "history": hist,
                "graph": {"nodes": len(nodes), "edges": int((mask.sum() - len(nodes)) / 2)},
                "heads": self.model.n_heads, "features": FEATURES,
                "model": getattr(self.model, "kind", "gat"),
            }
            self._dirty = True
            self.refresh()
            by = self._account_orders()
            for o in self.orders:  # back-fill scores on mirrored history so what-if has traffic to replay
                if o.get("risk") is None:
                    others = [x["amount"] for x in by.get(o["account"], []) if x is not o]
                    o["spend_ratio"] = round(o["amount"] / ((np.mean(others) if others else 60.0 * K) + 20 * K), 2)
                    o["risk"] = round(self._probs.get(o["account"], 0) * 0.95, 3)
                    o["decision"] = self.decide(o["risk"], o["amount"], False)
            return self.metrics

    def refresh(self):
        with self.lock:
            if not self._dirty or self.model is None:
                return
            nodes, idx, X, mask = self.build_graph()
            P = self.model.predict_proba(X, mask)
            self._probs = {n: float(P[i]) for i, n in enumerate(nodes)}
            # devices and IPs inherit the strongest risk among their accounts
            nA = sum(1 for n in nodes if n.startswith("A-"))
            for i in range(nA, len(nodes)):
                nb = np.where(mask[i, :nA])[0]
                if len(nb) and not nodes[i].startswith("M-"):
                    self._probs[nodes[i]] = float(np.max(P[nb]))
            self._graph_cache = (nodes, idx, mask)
            self._dirty = False

    def risk(self, node):
        self.refresh()
        return self._probs.get(node, 0.0)

    def explain(self, node_id, top=6):
        """Which neighbours did the GAT attend to when scoring this node?"""
        with self.lock:
            self.refresh()
            nodes, idx, X, mask = self.build_graph()
            i = idx[node_id]
            if hasattr(self.model, "attn1"):
                self.model.forward(X, mask)
                a1 = np.mean([a[i] for a in self.model.attn1], axis=0)
                a2 = self.model.attn2[i]
                att = 0.5 * a1 + 0.5 * a2
                per_head = [a[i] for a in self.model.attn1]
            else:  # GraphSAGE weighs neighbours equally (mean aggregator): rank them by their own risk instead
                deg = max(int(mask[i].sum()) - 1, 1)
                att = np.array([(1.0 / deg) * (0.5 + self._probs.get(n, 0)) if mask[i, j] and j != i else 0.0
                                for j, n in enumerate(nodes)])
                per_head = []
            nb = [j for j in np.argsort(-att) if mask[i, j] and j != i][:top]
            out = []
            for j in nb:
                nid = nodes[j]
                shared = int(mask[j, : len(self.accounts)].sum()) - (1 if nid.startswith("A-") else 0)
                out.append({"node": nid, "type": _ntype(nid), "attention": round(float(att[j]), 4),
                            "head_attention": [round(float(h[j]), 3) for h in per_head],
                            "risk": round(self._probs.get(nid, 0), 3), "accounts_linked": shared})
            feats = {f: round(float(X[i, k]), 3) for k, f in enumerate(FEATURES) if k >= 4}
            return {"node": node_id, "risk": round(self._probs.get(node_id, 0), 3),
                    "self_attention": round(float(att[i]), 4), "top_neighbors": out, "features": feats}

    # ------------------------------------------------------------- orders
    def score_order(self, account, merchant, amount, device, ip):
        """Score a tentative order: GAT on the would-be graph + transaction signals."""
        with self.lock:
            acc = self.accounts[account]
            nodes, idx, X, mask = self.build_graph(
                extra={"account": account, "device": device, "ip": ip, "merchant": merchant, "amount": amount})
            P = self.model.predict_proba(X, mask)
            g_now = float(P[idx[account]])
            before = self.risk(account)
            shared_ids = self.shared_identifiers(account, extra_device=device)
            dev_others = [a for a in self.accounts.values() if device in a.devices and a.id != account]
            dev_risk = max((self._probs.get(a.id, 0) for a in dev_others), default=0.0)
            hist = [o for o in self.orders if o["account"] == account and o["status"] != "blocked"]
            avg = np.mean([o["amount"] for o in hist]) if hist else 60.0 * K
            velocity = sum(1 for o in hist if o["ts"] > self.clock - 60)
            new_device = device not in acc.devices
            geo = self.ips.get(ip, acc.country) != acc.country
            factors = []
            t = 0.0
            if new_device:
                t += 0.25
                factors.append("new device for this account")
            if geo:
                t += 0.25
                factors.append(f"IP country {self.ips.get(ip)} differs from account country {acc.country}")
            ratio = amount / (avg + 20 * K)
            if ratio > 2:
                t += 0.2 * min(ratio / 6, 1)
                factors.append(f"amount is {ratio:.1f}x this buyer's usual spend")
            if velocity >= 3:
                t += 0.15 * min(velocity / 6, 1)
                factors.append(f"{velocity} orders in the last hour")
            if acc.age_days < 7:
                t += 0.15
                factors.append(f"account is only {acc.age_days} days old")
            if acc.status == "frozen":
                t = 1.0
                factors.append("account is frozen by an investigator")
            g = max(g_now, 0.9 * dev_risk)
            nbs = self.known_fraud_signal(account, extra_ids=(device, ip))
            g = 1 - (1 - g) * (1 - NEIGHBOUR_WEIGHT * nbs["share"])
            if nbs["known_fraud"]:
                factors.append(f"{nbs['known_fraud']} of {nbs['labelled']} labelled linked accounts are known fraud")
            if dev_risk > 0.5:
                factors.append(f"device shared with {len(dev_others)} account(s), highest GAT risk {dev_risk:.2f}")
            for s_ in shared_ids:
                if s_["kind"] != "device" and s_["flagged"]:
                    factors.append(f"shares {s_['label']} with {s_['flagged']} flagged account(s)")
            mf = self.money_flow_links(account)
            if mf["flagged"]:
                factors.append(f"moved money with {mf['flagged']} flagged account(s)")
            if g_now - before > 0.1:
                factors.append(f"GAT risk jumps {before:.2f} -> {g_now:.2f} once this order's links are added")
            elif g_now > 0.5:
                factors.append(f"GAT flags this account's neighbourhood ({g_now:.2f})")
            risk = 1 - (1 - g) * (1 - min(t, 1) * 0.75)
            return {"risk": round(float(risk), 3), "spend_ratio": round(float(ratio), 2), "gat_risk": round(g_now, 3), "gat_before": round(before, 3),
                    "device_risk": round(dev_risk, 3), "neighbour_signal": nbs, "graph_risk": round(float(g), 3),
                    "txn_score": round(min(t, 1), 3), "factors": factors}

    def known_fraud_signal(self, account, extra_ids=()):
        """Share of this account's linked accounts (shared device, IP or payment identifier) that are known fraud.

        "Known" = labelled in the GAT's training split, or frozen by an investigator. Held-out and new accounts'
        labels are never read, so the signal is fair on unseen accounts. Smoothed: fraud / (labelled + 1)."""
        acc = self.accounts[account]
        ids = acc.devices | acc.ips | acc.pay_ids | {i for i in extra_ids if i}
        # money-transfer partners are left out on purpose: honest people pay random others, and including them
        # lowered held-out AUC (0.976 -> 0.967). The GAT still sees transfers as graph edges.
        nb = [b for b in self.accounts.values() if b.id != account and (b.devices | b.ips | b.pay_ids) & ids]
        labelled = [b for b in nb if b.id in self.known_labelled or b.status == "frozen"]
        fraud = [b for b in labelled if b.id in self.known_fraud or b.status == "frozen"]
        return {"share": round(len(fraud) / (len(labelled) + 1), 3), "known_fraud": len(fraud),
                "labelled": len(labelled), "linked": len(nb)}

    def shared_identifiers(self, account, extra_device=None):
        """Devices and payment identifiers this account shares, and how many of the sharers are flagged.

        Flagged = known fraud, frozen, or model risk >= 0.5 (is_flagged). IPs are left out: shared wifi is noisy."""
        acc = self.accounts[account]
        out = []
        ids = sorted(acc.devices | ({extra_device} if extra_device else set())) + sorted(acc.pay_ids)
        for i in ids:
            kind = "device" if i.startswith("D-") else i.split("-")[0]
            others = [b for b in self.accounts.values() if b.id != account and (i in b.devices or i in b.pay_ids)]
            if not others:
                continue
            flagged = [b for b in others if self.is_flagged(b.id)]
            out.append({"id": i, "kind": _ntype(i),
                        "label": "device" if kind == "device" else PAY_KINDS[kind], "accounts": len(others),
                        "flagged": len(flagged)})
        return out

    def is_flagged(self, account):
        """Known fraud (training labels), frozen by an investigator, or scored >= 0.5. Labels and freezes do not move
        when a clean account attaches itself to a ring and dilutes the model's score, so they are checked first."""
        return (account in self.known_fraud or self.accounts[account].status == "frozen"
                or self._probs.get(account, 0) >= 0.5)

    def money_flow_links(self, account):
        """Accounts this one moved money with, and how many of them are flagged."""
        partners = sorted(self.xfer_nb.get(account, ()))
        flagged = [b for b in partners if self.is_flagged(b)]
        return {"partners": len(partners), "flagged": len(flagged)}

    def decide(self, risk, amount, new_device, policy=None, spend_ratio=0.0):
        p = {**DEFAULT_POLICY, **(policy or self.policy)}
        if risk >= p["block_threshold"]:
            return "block"
        if risk >= p["review_threshold"] or amount > p["max_auto_amount"] or (
                new_device and amount > p["review_new_device_over"]) or (
                p["review_spend_multiple"] and spend_ratio >= p["review_spend_multiple"]):
            return "review"
        return "approve"

    def place_order(self, account, merchant, amount, device=None, ip=None, source="simulated",
                    fraud=None, pay_order_id=None, items=None):
        with self.lock:
            acc = self.accounts[account]
            device = device or min(acc.devices, default=None) or self.new_device()
            ip = ip or min(acc.ips, default=None) or self.new_ip(acc.country)
            if device not in self.devices:
                self.devices.add(device)
            if ip not in self.ips:
                self.ips[ip] = acc.country
            s = self.score_order(account, merchant, amount, device, ip)
            new_device = device not in acc.devices
            decision = self.decide(s["risk"], amount, new_device, spend_ratio=s["spend_ratio"])
            order = {
                "id": self._next("O"), "account": account, "merchant": merchant, "amount": round(amount, 2),
                "ts": self.clock, "device": device, "ip": ip, "ip_country": self.ips[ip],
                "status": {"approve": "approved", "review": "review", "block": "blocked"}[decision],
                "risk": s["risk"], "gat_risk": s["gat_risk"], "txn_score": s["txn_score"], "decision": decision,
                "fraud": acc.fraud if fraud is None else fraud, "source": source,
                "pay_order_id": pay_order_id, "factors": s["factors"], "new_device": new_device, "spend_ratio": s["spend_ratio"],
                "items": items or [], "new_device_flag": new_device,
            }
            self.orders.append(order)
            if decision != "block":
                acc.devices.add(device)
                acc.ips.add(ip)
            self._dirty = True
            self._event("order", f"{order['id']} {acc.name} {rs(amount)} at {self.merchants[merchant]['name']}"
                                 f" -> {decision.upper()} (risk {s['risk']:.2f})", order=order["id"],
                        level={"approve": "ok", "review": "warn", "block": "bad"}[decision])
            return order

    def _event(self, kind, text, level="info", **kw):
        self._seq = getattr(self, "_seq", 0) + 1
        ev = {"seq": self._seq, "ts": round(self.clock, 1), "kind": kind, "text": text, "level": level, **kw}
        self.events.append(ev)
        self.events = self.events[-300:]
        if getattr(self, "on_event", None):
            self.on_event(ev)

    # --------------------------------------------------------- simulation
    def tick(self, n=6, fraud_rate=0.15):
        """Advance the twin by a few minutes of synthetic live traffic."""
        r = self.rng
        out = []
        with self.lock:
            self.clock += r.uniform(3, 8)
            legit = [a for a in self.accounts.values() if not a.fraud and a.status == "active"]
            bad = [a for a in self.accounts.values() if a.fraud and a.status == "active"]
            for _ in range(n):
                if bad and r.random() < fraud_rate:
                    a = r.choice(bad)
                    amt = r.uniform(120 * K, 900 * K)
                    m = r.choice(["M-02", "M-07", "M-09"])
                else:
                    a = r.choice(legit)
                    amt = round(r.lognormvariate(3.6, 0.6) * K, 2)
                    m = r.choice(list(self.merchants))
                    roll = r.random()
                    if roll < 0.06:  # real customers make the odd big purchase
                        amt = round(amt * r.uniform(3, 8), 2)
                    elif roll < 0.10:  # and buy from a new phone
                        out.append(self.place_order(a.id, m, round(amt * r.uniform(1, 6), 2), self.new_device(),
                                                    r.choice(sorted(a.ips))))
                        continue
                out.append(self.place_order(a.id, m, amt, r.choice(sorted(a.devices)), r.choice(sorted(a.ips))))
        return out

    def what_if(self, policy, last_n=400, extra_orders=None):
        """Replay recent scored orders (plus red-team attack orders) under a proposed policy."""
        scored = [o for o in self.orders if o.get("risk") is not None][-last_n:] + list(extra_orders or [])

        def run(p):
            res = {"approved": 0, "review": 0, "blocked": 0, "fraud_caught_inr": 0.0, "fraud_missed_inr": 0.0,
                   "good_blocked_inr": 0.0, "good_reviewed": 0, "fraud_total_inr": 0.0,
                   "good_blocked": 0, "good_total": 0, "fraud_blocked_inr": 0.0, "fraud_reviewed_inr": 0.0}
            for o in scored:
                d = self.decide(o["risk"], o["amount"], o.get("new_device", False), p, o.get("spend_ratio", 0))
                res[{"approve": "approved", "review": "review", "block": "blocked"}[d]] += 1
                if o["fraud"]:
                    res["fraud_total_inr"] += o["amount"]
                    if d == "approve":
                        res["fraud_missed_inr"] += o["amount"]
                    else:  # "caught" = intercepted: blocked or sent to review
                        res["fraud_caught_inr"] += o["amount"]
                        res["fraud_blocked_inr" if d == "block" else "fraud_reviewed_inr"] += o["amount"]
                else:
                    res["good_total"] += 1
                    if d == "block":
                        res["good_blocked_inr"] += o["amount"]
                        res["good_blocked"] += 1
                    if d == "review":
                        res["good_reviewed"] += 1
            for k in list(res):
                if k.endswith("inr"):
                    res[k] = round(res[k], 2)
            res["catch_rate"] = round(res["fraud_caught_inr"] / max(res["fraud_total_inr"], 1), 3)
            res["orders_flagged"] = res["review"] + res["blocked"]
            res["fraud_stopped_per_100_flagged_inr"] = per_100(res["fraud_caught_inr"], res["orders_flagged"])
            return res

        merged = {**self.policy, **policy}
        return {"orders_replayed": len(scored), "attack_orders": len(extra_orders or []), "current": run(self.policy), "proposed": run(merged),
                "current_policy": self.policy, "proposed_policy": merged}

    # ------------------------------------------------- rules vs graph
    def compare_engines(self, extra_orders=None, hardened_policy=None, last_n=400, review_cost=4.0 * K):
        """Replay the same orders through (a) rules only, (b) GAT + rules, (c) GAT + rules under a hardened policy.

        Only orders scored live (with a stored rules score) are used, so every engine sees identical inputs.
        "Intercepted" = blocked or sent to review; blocked and reviewed fraud rupees are reported separately.
        """
        recent = [o for o in self.orders if o.get("txn_score") is not None][-last_n:]
        orders = recent + [o for o in (extra_orders or []) if o.get("txn_score") is not None]
        engines = {
            "rules_only": (lambda o: o["txn_score"], dict(DEFAULT_POLICY)),  # rules at full 0-1 scale: the fairest baseline
            "gat_only": (lambda o: o.get("gat_risk") or 0.0, dict(DEFAULT_POLICY)),  # the GAT's own score, no rules
            "gat_rules": (lambda o: o["risk"], dict(DEFAULT_POLICY)),
        }
        if hardened_policy:
            engines["gat_rules_hardened"] = (lambda o: o["risk"], {**DEFAULT_POLICY, **hardened_policy})

        def kind(o):
            return o.get("attack") or ("live fraud traffic" if o["fraud"] else None)

        out = {"orders": len(orders), "attack_orders": len(orders) - len(recent), "review_cost_inr": review_cost,
               "engines": {}, "by_attack": {}}
        for name, (score, pol) in engines.items():
            m = {"fraud_total_inr": 0.0, "fraud_blocked_inr": 0.0, "fraud_reviewed_inr": 0.0, "fraud_missed_inr": 0.0,
                 "good_total": 0, "good_reviewed": 0, "good_blocked": 0, "good_blocked_inr": 0.0, "orders_flagged": 0}
            per = {}
            for o in orders:
                d = self.decide(score(o), o["amount"], o.get("new_device", False), pol, o.get("spend_ratio", 0))
                m["orders_flagged"] += d != "approve"
                if o["fraud"]:
                    m["fraud_total_inr"] += o["amount"]
                    key = {"block": "fraud_blocked_inr", "review": "fraud_reviewed_inr", "approve": "fraud_missed_inr"}[d]
                    m[key] += o["amount"]
                    k = kind(o)
                    pk = per.setdefault(k, {"total_inr": 0.0, "intercepted_inr": 0.0})
                    pk["total_inr"] += o["amount"]
                    if d != "approve":
                        pk["intercepted_inr"] += o["amount"]
                else:
                    m["good_total"] += 1
                    if d == "review":
                        m["good_reviewed"] += 1
                    elif d == "block":
                        m["good_blocked"] += 1
                        m["good_blocked_inr"] += o["amount"]
            m = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()}
            m["fraud_intercepted_inr"] = round(m["fraud_blocked_inr"] + m["fraud_reviewed_inr"], 2)
            m["intercept_rate"] = round(m["fraud_intercepted_inr"] / max(m["fraud_total_inr"], 1), 3)
            m["false_positive_cost_inr"] = round(m["good_blocked_inr"] + review_cost * m["good_reviewed"], 2)
            m["fraud_stopped_per_100_flagged_inr"] = per_100(m["fraud_intercepted_inr"], m["orders_flagged"])
            out["engines"][name] = m
            for k, v in per.items():
                out["by_attack"].setdefault(k, {"total_inr": round(v["total_inr"], 2)})[name] = round(
                    v["intercepted_inr"] / max(v["total_inr"], 1e-9), 3)
        # the honest headline: red-team attack orders only (fresh accounts the GAT never saw a label for)
        unseen = {}
        for name, (score, pol) in engines.items():
            tot = icp = 0.0
            for o in orders:
                if o.get("attack") and o["fraud"]:
                    tot += o["amount"]
                    if self.decide(score(o), o["amount"], o.get("new_device", False), pol,
                                   o.get("spend_ratio", 0)) != "approve":
                        icp += o["amount"]
            unseen[name] = {"total_inr": round(tot, 2), "intercepted_inr": round(icp, 2),
                            "intercept_rate": round(icp / max(tot, 1e-9), 3)}
        out["unseen_attacks"] = unseen
        out["account_auc"] = self.account_auc()
        return out

    def account_auc(self):
        """Held-out account AUC: the same node features with no graph (logistic regression) vs the GAT."""
        sp = getattr(self, "_split", None)
        if not sp or self.model is None:
            return None
        X, y, tr, te = sp["X"], sp["y"], sp["tr"], sp["te"]
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
        Z = (X - mu) / sd
        w, b = np.zeros(X.shape[1]), 0.0
        for _ in range(800):  # plain logistic regression, L2, gradient descent
            p = 1 / (1 + np.exp(-(Z[tr] @ w + b)))
            g = p - y[tr]
            w -= 0.1 * (Z[tr].T @ g / len(tr) + 1e-3 * w)
            b -= 0.1 * g.mean()
        rules_p = 1 / (1 + np.exp(-(Z[te] @ w + b)))
        out = {"test_accounts": int(len(te)), "features_only_no_graph": round(float(auc(y[te], rules_p)), 4),
               "gat": self.metrics.get("test_auc")}
        nodes = sp.get("nodes")
        if nodes:
            g = np.array([self._probs.get(nodes[i], 0.0) for i in te])
            share = np.array([self.known_fraud_signal(nodes[i])["share"] for i in te])
            out["gat_plus_known_fraud_neighbours"] = round(float(auc(y[te], 1 - (1 - g) * (1 - NEIGHBOUR_WEIGHT * share))), 4)
        return out

    # ------------------------------------------------------ investigation
    def linked_accounts(self, account, hops=2):
        acc = self.accounts[account]
        seen, frontier, via = {account}, [acc], {}
        for _ in range(hops):
            nxt = []
            for a in frontier:
                for b in self.accounts.values():
                    if b.id in seen:
                        continue
                    shared = (a.devices & b.devices) | (a.ips & b.ips) | (a.pay_ids & b.pay_ids)
                    if b.id in self.xfer_nb.get(a.id, ()):
                        shared = shared | {"money transfer"}
                    if shared:
                        seen.add(b.id)
                        via[b.id] = sorted(shared)
                        nxt.append(b)
            frontier = nxt
        return [{"account": b, "name": self.accounts[b].name, "shared": via[b], "risk": round(self.risk(b), 3),
                 "status": self.accounts[b].status, "age_days": self.accounts[b].age_days} for b in via]

    def account_view(self, account):
        a = self.accounts[account]
        orders = [o for o in self.orders if o["account"] == account]
        return {"id": a.id, "name": a.name, "country": a.country, "age_days": a.age_days, "email": _mask_email(a.email),
                "kyc_verified": a.kyc, "status": a.status, "devices": sorted(a.devices), "ips": sorted(a.ips),
                "payment_ids": sorted(a.pay_ids), "shared_identifiers": self.shared_identifiers(account),
                "risk": round(self.risk(account), 3), "order_count": len(orders),
                "total_spend": round(sum(o["amount"] for o in orders), 2),
                "recent_orders": [_public(o) for o in orders[-8:]]}

    def freeze(self, account, reason=""):
        with self.lock:
            self.accounts[account].status = "frozen"
            self._event("action", f"{account} restricted (merchant-side, simulated): {reason}", level="bad")

    def flagged(self, k=10):
        self.refresh()
        accs = [a for a in self.accounts.values() if a.status == "active"]
        accs.sort(key=lambda a: -self._probs.get(a.id, 0))
        return [{"account": a.id, "name": a.name, "risk": round(self._probs.get(a.id, 0), 3),
                 "age_days": a.age_days, "devices": len(a.devices)} for a in accs[:k]]

    # -------------------------------------------------------------- views
    def stats(self):
        self.refresh()
        live = [o for o in self.orders if o["source"] != "history"]
        acc_risk = [self._probs.get(a, 0) for a in self.accounts]
        return {
            "accounts": len(self.accounts), "devices": len(self.devices), "ips": len(self.ips),
            "payment_ids": len(self.pay_ids),
            "merchants": len(self.merchants), "orders": len(self.orders), "live_orders": len(live),
            "approved": sum(o["decision"] == "approve" for o in live),
            "review": sum(o["decision"] == "review" for o in live),
            "blocked": sum(o["decision"] == "block" for o in live),
            "blocked_inr": round(sum(o["amount"] for o in live if o["decision"] == "block"), 2),
            "high_risk_accounts": int(sum(p >= 0.5 for p in acc_risk)),
            "frozen": sum(a.status == "frozen" for a in self.accounts.values()),
            "clock_min": round(self.clock, 1), "model": {k: v for k, v in self.metrics.items() if k != "history"},
            "policy": self.policy,
        }

    def graph_view(self, focus=None, max_accounts=140):
        self.refresh()
        if focus and focus in self.accounts:
            ids = {focus} | {x["account"] for x in self.linked_accounts(focus, hops=2)}
        else:
            ranked = sorted(self.accounts.values(), key=lambda a: -self._probs.get(a.id, 0))
            top = ranked[:60]
            rest = self.rng.sample(ranked[60:], k=min(max_accounts - 60, len(ranked) - 60))
            ids = {a.id for a in top + rest}
        nodes, edges, seen = [], [], set()
        for aid in ids:
            a = self.accounts[aid]
            nodes.append({"id": aid, "label": a.name.split()[0], "type": "account",
                          "risk": round(self._probs.get(aid, 0), 3), "status": a.status})
            shared_pay = {p for p in a.pay_ids if sum(p in b.pay_ids for b in self.accounts.values()) >= 2}
            for d in a.devices | a.ips | shared_pay:
                if d not in seen:
                    seen.add(d)
                    nodes.append({"id": d, "label": d, "type": _ntype(d), "risk": round(self._probs.get(d, 0), 3)})
                edges.append({"from": aid, "to": d})
        pairs = set()
        for t in self.transfers:  # money moved between two shown accounts (one edge per pair)
            key = tuple(sorted((t["src"], t["dst"])))
            if t["src"] in ids and t["dst"] in ids and key not in pairs:
                pairs.add(key)
                edges.append({"from": t["src"], "to": t["dst"], "kind": "money"})
        return {"nodes": nodes, "edges": edges}

    def fork(self):
        with self.lock:
            # Never touch the live twin's lock or hook while copying: other request threads may be
            # using them. The memo makes deepcopy give the fork a fresh lock and no event hook.
            memo = {id(self.lock): threading.RLock()}
            hook = getattr(self, "on_event", None)
            if hook is not None:
                memo[id(hook)] = None
            twin = copy.deepcopy(self, memo)
            twin.on_event = None
            return twin


def _mask_email(e):
    """a.b.123@gmail.com -> a***@gmail.com: enough to see the provider (disposable or not), not the address."""
    local, _, dom = str(e).partition("@")
    return f"{local[:1]}***@{dom}" if dom else "***"


def per_100(fraud_stopped_inr, orders_flagged):
    """₹ of fraud stopped per 100 orders flagged (blocked or sent to review): what a review team's effort buys."""
    return round(100 * fraud_stopped_inr / orders_flagged, 2) if orders_flagged else 0.0


def _ntype(n):
    p = n.split("-")[0]
    return {"A": "account", "D": "device", "IP": "ip", "UPI": "upi", "CARD": "card", "CT": "contact"}.get(p, "merchant")


def _public(o):
    return {k: o.get(k) for k in ("id", "account", "merchant", "amount", "status", "decision", "risk", "source",
                                  "pay_order_id", "device", "ip", "ip_country", "factors", "ts")}
