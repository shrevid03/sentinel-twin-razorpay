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
from .graph_rf import graph_features, vector
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
SEQ_DIMS = 8


def engine_name():
    """Order-risk engine: "gat" (default) or "rf_graph" (random forest on explicit graph features).

    Chosen by two measurements (README, "Which model decides"): on a chronological holdout of the order stream the
    forest matched the forest + GAT and is ~15-30x faster, but on the drill's fresh red-team attacks the GAT engine
    caught more fraud (86.7% vs 77.4%) and blocked no good orders (₹0 vs ₹29,701). Safety wins the default."""
    e = os.getenv("TWIN_ENGINE", "").strip().lower()
    return e if e in ("rf_graph", "gat") else "gat"  # when TWIN_SEQ=1: sequence risk + the first SEQ_DIMS dims of the transformer's behaviour embedding

DEFAULT_POLICY = {"review_threshold": 0.5, "block_threshold": 0.8, "max_auto_amount": 1000.0 * K,
                  "review_new_device_over": 300.0 * K, "review_spend_multiple": 0.0}


class TrackedSet(set):
    """An account's links (devices, IPs, payment ids). Every addition is recorded with a global evidence sequence
    number, the simulated clock and its provenance, so any past decision can be replayed on the graph as it was."""

    def __init__(self, twin=None, owner=None):
        super().__init__()
        self._twin, self._owner = twin, owner

    def add(self, x):
        if x not in self:
            set.add(self, x)
            if self._twin is not None:
                self._twin._note_link(self._owner, x)

    def update(self, *its):
        for it in its:
            for x in it:
                self.add(x)

    def __ior__(self, other):
        self.update(other)
        return self

    def __reduce__(self):  # deepcopy / fork: rebuild without re-recording, then restore twin + owner
        return (_rebuild_tracked, (list(self),), self.__dict__)


def _rebuild_tracked(items):
    t = TrackedSet()
    set.update(t, items)
    return t


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
    def __init__(self, seed: int = 11, populate: bool = True):
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.clock = 0.0  # simulated minutes
        self.accounts: dict[str, Account] = {}
        self.devices: set[str] = set()
        self.ips: dict[str, str] = {}  # ip -> country
        self.pay_ids: set[str] = set()  # UPI-…, CARD-…, CT-… nodes
        # payment identifiers draw from their own RNG so adding them leaves the rest of the seeded world unchanged
        self.prng = random.Random(seed * 7919 + 1)
        self.xrng = random.Random(seed * 104729 + 3)
        # evidence timing: a monotonic sequence over every link, order, transfer, label and freeze
        self._eseq = 0
        self._prov = "seed fixture"
        self.link_meta: dict[tuple, dict] = {}  # (account, node) -> {"seq", "at", "prov"}
        self.label_meta: dict[str, dict] = {}  # account -> when its training label became known
        self.decisions: dict[str, dict] = {}  # replayable risk decisions
        self.model_versions: dict[str, object] = {}  # model version hash -> frozen copy of the model
        self.ledger = None  # money-flow world: its own RNG, like payment ids
        self.transfers: list[dict] = []  # {"src", "dst", "amount", "ts", "pattern"}
        self.xfer_nb: dict[str, set] = {}  # account -> accounts it has sent money to or received from
        self.merchants = {m[0]: {"id": m[0], "name": m[1], "category": m[2]} for m in MERCHANTS}
        self.orders: list[dict] = []
        self.events: list[dict] = []
        self.policy = dict(DEFAULT_POLICY)
        self.model: GAT | None = None
        self.seq_model = None  # transaction-sequence transformer (TWIN_SEQ=1), off by default
        self.features = list(FEATURES)
        self.metrics: dict = {}
        self._probs: dict[str, float] = {}
        self.known_labelled: set[str] = set()  # accounts whose label the model trained on
        self.known_fraud: set[str] = set()  # ...of which fraud
        self._dirty = True
        self._n = {"A": 0, "D": 0, "IP": 0, "O": 0, "UPI": 0, "CARD": 0, "CT": 0}
        self.label_events: list[dict] = []  # each training run's labels, with their evidence sequence
        self.audit = None  # AuditStore: durable decisions, graph events, model artifacts (attach_audit)
        self.epoch = None
        self._flushed_seq = 0
        if populate:
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
        with self.lock, self.provenance(source):
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

    def timeline(self, account):
        """The account's recent events as the sequence transformer reads them, with the [CLS] attention per event
        when the transformer is on (TWIN_SEQ=1)."""
        from .seqformer import account_events, encode
        toks = account_events(self, account)
        out = {"account": account, "sequence_model": self.seq_model is not None, "risk": None,
               "events": [{"event": t["label"], "inr": t["inr"], "new_device": t["new_device"] == 2,
                           "new_place": t["new_place"] == 2, "attention": None} for t in toks]}
        if self.seq_model is not None and toks:
            ids, m = encode([toks])
            out["risk"] = round(float(self.seq_model.predict(ids, m)[0]), 3)
            att = self.seq_model.attention_from_cls()[0][:len(toks)]
            for e, a in zip(out["events"], att):
                e["attention"] = round(float(a), 3)
        return out

    def engine_risk(self, account):
        """Account-level risk as the shipped engine sees it: the model's score combined with the known-fraud share."""
        g = float(self.risk(account))
        return round(1 - (1 - g) * (1 - NEIGHBOUR_WEIGHT * self.known_fraud_signal(account)["share"]), 3)

    def link_payer(self, account, links, source="razorpay"):
        """Mirror a captured payment's hashed identifiers (payment ids, device, IP) onto the account, then re-score
        it with the live model, so the very next order from a shared UPI ID, card, device or IP is linked."""
        with self.lock, self.provenance(source):
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

    # ------------------------------------------------------ evidence timing
    def _bump(self):
        self._eseq += 1
        return self._eseq

    def _note_link(self, owner, item):
        self.link_meta[(owner, item)] = {"seq": self._bump(), "at": round(self.clock, 2), "prov": self._prov}

    class _Prov:
        def __init__(self, twin, prov):
            self.twin, self.prov, self.old = twin, prov, None

        def __enter__(self):
            self.old, self.twin._prov = self.twin._prov, self.prov

        def __exit__(self, *a):
            self.twin._prov = self.old

    def provenance(self, prov):
        """with twin.provenance("payment pay_X"): links added inside are recorded with that source."""
        return Twin._Prov(self, prov)

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
        acc.devices, acc.ips, acc.pay_ids = TrackedSet(self, aid), TrackedSet(self, aid), TrackedSet(self, aid)
        acc.created_seq, acc.created_at, acc.created_prov = self._bump(), round(self.clock, 2), self._prov
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
        self._prov = "seed fixture"
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
        self._prov = "live"

    # ---------------------------------------------------------- money flows
    def add_transfer(self, src, dst, amount, ts=None, pattern="none"):
        if src == dst:
            return
        self.transfers.append({"src": src, "dst": dst, "amount": round(float(amount), 2),
                               "ts": self.clock if ts is None else ts, "pattern": pattern, "seq": self._bump(),
                               "prov": self._prov})
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
            "fraud": fraud, "source": "history", "pay_order_id": None, "factors": [], "seq": self._bump(),
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

        X = np.zeros((N, len(self.features)))
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
        if self.seq_model is not None:  # behaviour over time, from the sequence transformer
            from .seqformer import all_account_events, encode
            toks = all_account_events(self, extra)
            ids, smask = encode([toks[a.id] for a in accs])
            risk = self.seq_model.predict(ids, smask)
            emb = self.seq_model._c["cls"][:, :SEQ_DIMS]
            nf = len(FEATURES)
            X[:len(accs), nf] = risk
            X[:len(accs), nf + 1:nf + 1 + SEQ_DIMS] = np.tanh(emb)
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
            self.trained_on_orders = len(self.orders)  # live traffic drifts the features; see needs_retrain()
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
            lseq = self._bump()
            self.label_events.append({"seq": lseq, "at": round(self.clock, 2), "prov": "training label",
                                      "labelled": sorted(self.known_labelled), "fraud": sorted(self.known_fraud)})
            for a in self.known_fraud:
                self.label_meta.setdefault(a, {"seq": lseq, "at": round(self.clock, 2), "prov": "training label"})
            if os.getenv("TWIN_SEQ", "0") == "1" and self.seq_model is None:
                # train the sequence transformer on the same training accounts, then rebuild the graph with its output
                from .seqformer import SeqFormer, all_account_events, encode
                toks = all_account_events(self)
                tr_acc = [nodes[i] for i in tr]
                ids, smask = encode([toks[a] for a in tr_acc])
                self.seq_model = SeqFormer(seed=7).fit(ids, smask, np.array([int(self.accounts[a].fraud) for a in tr_acc]),
                                                       epochs=150)
                self.features = list(FEATURES) + ["seq_risk"] + [f"seq_emb_{k}" for k in range(SEQ_DIMS)]
                nodes, idx, X, mask = self.build_graph()
                # out-of-fold sequence features for the GAT's training rows: two transformers, each trained on half the
                # training accounts, score the other half, so the GAT never learns from in-sample sequence scores
                rr2 = np.random.default_rng(11)
                halves = np.array_split(rr2.permutation(len(tr_acc)), 2)
                nf = len(FEATURES)
                for k in (0, 1):
                    fit_i, score_i = halves[1 - k], halves[k]
                    sub = SeqFormer(seed=7).fit(ids[fit_i], smask[fit_i],
                                                np.array([int(self.accounts[tr_acc[j]].fraud) for j in fit_i]), epochs=150)
                    rk = sub.predict(ids[score_i], smask[score_i])
                    em = np.tanh(sub._c["cls"][:, :SEQ_DIMS])
                    rows = [idx[tr_acc[j]] for j in score_i]
                    X[rows, nf] = rk
                    X[rows, nf + 1:nf + 1 + SEQ_DIMS] = em
                self._split["X"] = X
            kind = os.getenv("TWIN_MODEL", "gat").strip().lower()
            if kind == "graphsage":  # GraphSAGE in NumPy (app/sage.py)
                from .sage import SageMaskModel
                self.model = SageMaskModel(len(self.features), hidden=64, seed=7)
            else:
                self.model = GAT(len(self.features), hidden=8, heads=4, skip=GAT_SKIP)
            hist = self.model.fit(X, mask, y, tr, va, epochs=epochs)
            self.order_model = None
            if engine_name() == "rf_graph":
                self._train_order_model()
            self.model_version = self._model_hash(self.model) + (
                "+" + self.order_model.version if self.order_model is not None else "")
            self.model_versions[self.model_version] = {"gat": copy.deepcopy(self.model),
                                                       "order_model": copy.deepcopy(self.order_model),
                                                       "seq_model": copy.deepcopy(self.seq_model),
                                                       "features": list(self.features)}
            if self.audit is not None:
                self.audit.save_model(self.model_version, self.model_versions[self.model_version])
            P = self.model.predict_proba(X, mask)
            te_pred = P[te] >= 0.5
            tp = int(np.sum(te_pred & (y[te] == 1)))
            self.metrics = {
                "test_auc": round(auc(y[te], P[te]), 4),
                "precision": round(tp / max(int(te_pred.sum()), 1), 3),
                "recall": round(tp / max(int((y[te] == 1).sum()), 1), 3),
                "train_nodes": int(len(tr)), "test_nodes": int(len(te)), "history": hist,
                "graph": {"nodes": len(nodes), "edges": int((mask.sum() - len(nodes)) / 2)},
                "heads": self.model.n_heads, "features": self.features, "sequence_model": self.seq_model is not None,
                "model": getattr(self.model, "kind", "gat"), "engine": engine_name(),
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

    def needs_retrain(self, every=1000):
        """True once enough live orders have arrived since the last training that the account features have drifted
        (order counts, averages and velocity keep growing under simulated traffic, and a stale model then scores
        every account near zero)."""
        return len(self.orders) - getattr(self, "trained_on_orders", 0) >= every

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
            feats = {f: round(float(X[i, k]), 3) for k, f in enumerate(self.features) if k >= 4}
            return {"node": node_id, "risk": round(self._probs.get(node_id, 0), 3),
                    "self_attention": round(float(att[i]), 4), "top_neighbors": out, "features": feats}

    # ------------------------------------------------------------- orders
    def score_order(self, account, merchant, amount, device, ip):
        """Score a tentative order: GAT on the would-be graph + transaction signals."""
        with self.lock:
            acc = self.accounts[account]
            nodes, idx, X, mask = self.build_graph(
                extra={"account": account, "device": device, "ip": ip, "merchant": merchant, "amount": amount})
            feat_row = [round(float(v), 4) for v in X[idx[account]]]
            engine = "rf_graph" if getattr(self, "order_model", None) is not None else "gat"
            if engine == "gat":  # the GAT forward pass is the expensive part (about 300 ms on this twin)
                P = self.model.predict_proba(X, mask)
                g_now = float(P[idx[account]])
                before = self.risk(account)
            else:
                g_now, before = 0.0, None
            shared_ids = self.shared_identifiers(account, extra_device=device)
            dev_others = [a for a in self.accounts.values() if device in a.devices and a.id != account]
            dev_risk = max((self._probs.get(a.id, 0) for a in dev_others), default=0.0)
            t, factors, ratio, new_device = self._rules(acc, amount, device, ip)
            if engine == "rf_graph":
                g_now = self.order_model.predict(vector(feat_row[4:4 + 17], graph_features(self, account, device, ip),
                                                        [min(t, 1), ratio, float(new_device)]))
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
            if engine == "gat" and g_now - before > 0.1:
                factors.append(f"GAT risk jumps {before:.2f} -> {g_now:.2f} once this order's links are added")
            elif g_now > 0.5:
                factors.append(f"{'graph model' if engine == 'rf_graph' else 'GAT'} flags this order ({g_now:.2f})")
            risk = 1 - (1 - g) * (1 - min(t, 1) * 0.75)
            return {"risk": round(float(risk), 3), "spend_ratio": round(float(ratio), 2), "gat_risk": round(g_now, 3),
                    "model_risk": round(g_now, 3), "engine": engine,
                    "gat_before": None if before is None else round(before, 3),
                    "device_risk": round(dev_risk, 3), "neighbour_signal": nbs, "graph_risk": round(float(g), 3),
                    "features": feat_row,
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

    # ------------------------------------------------- replayable decisions
    @staticmethod
    def _model_hash(model):
        h = hashlib.sha256()
        net = getattr(model, "net", model)
        params = net.params() if hasattr(net, "params") else []
        for p_ in params:
            h.update(np.ascontiguousarray(p_, dtype=np.float64).tobytes())
        return "m-" + h.hexdigest()[:12]

    def _evidence(self, account, device, ip):
        """Every input the decision could see, with when it became available and where it came from."""
        acc = self.accounts[account]
        out = [{"item": account, "kind": "account", "seq": getattr(acc, "created_seq", 0),
                "available_at": getattr(acc, "created_at", 0), "provenance": getattr(acc, "created_prov", "seed fixture")}]
        for node in sorted(acc.devices | acc.ips | acc.pay_ids):
            m = self.link_meta.get((account, node), {"seq": 0, "at": 0, "prov": "seed fixture"})
            out.append({"item": node, "kind": _ntype(node), "seq": m["seq"], "available_at": m["at"],
                        "provenance": m["prov"]})
        for node, kind in ((device, "device"), (ip, "ip")):
            if node and node not in (acc.devices | acc.ips):
                out.append({"item": node, "kind": kind, "seq": self._eseq, "available_at": round(self.clock, 2),
                            "provenance": "this request (checkout session)"})
        ids = acc.devices | acc.ips | acc.pay_ids | {device, ip}
        for b in self.accounts.values():
            if b.id != account and (b.devices | b.ips | b.pay_ids) & ids and b.id in self.label_meta:
                lm = self.label_meta[b.id]
                if b.id in self.known_fraud or b.status == "frozen":
                    out.append({"item": b.id, "kind": "known-fraud label", "seq": lm["seq"], "available_at": lm["at"],
                                "provenance": lm["prov"]})
        for t in self.transfers:
            if account in (t["src"], t["dst"]):
                out.append({"item": f"{t['src']}->{t['dst']}", "kind": "transfer", "seq": t.get("seq", 0),
                            "available_at": round(t["ts"], 2), "provenance": t.get("prov", "seed fixture")})
        return out

    def record_decision(self, point, account, merchant, amount, device, ip, policy=None, new_device=False):
        """Score, decide and store a replayable risk decision (decision point: pre_checkout or pre_capture)."""
        with self.lock:
            pol = {**DEFAULT_POLICY, **(policy or self.policy)}
            s_ = self.score_order(account, merchant, amount, device, ip)
            decision = self.decide(s_["risk"], amount, new_device, pol, s_["spend_ratio"])
            n = self.audit.next("decision") if self.audit is not None else len(self.decisions) + 1
            did = f"{getattr(self, 'decision_prefix', 'DEC-')}{n:05d}"  # unique across restarts when durable
            rec = {"id": did, "point": point, "decided_at": round(self.clock, 2), "seq": self._eseq,
                   "inputs": {"account": account, "merchant": merchant, "amount": round(float(amount), 2),
                              "device": device, "ip": ip, "new_device": bool(new_device),
                              "ip_country": self.ips.get(ip)},
                   "evidence": self._evidence(account, device, ip), "model_version": getattr(self, "model_version", None),
                   "policy_version": hashlib.sha256(json.dumps(pol, sort_keys=True).encode()).hexdigest()[:8],
                   "policy": pol, "features": s_["features"], "score": s_["risk"], "gat_risk": s_["gat_risk"],
                   "txn_score": s_["txn_score"], "factors": s_["factors"], "outcome": decision}
            self.decisions[did] = rec
            if self.audit is not None:  # graph events up to this decision, then the decision itself
                self.flush_events()
                rec["epoch"] = self.epoch
                self.audit.save_decision(self.epoch, rec)
            if self.ledger is not None:
                self.ledger.append({"type": "risk_decision", "case_id": did, "by": "model", "point": point,
                                    "account": account, "decided_at": rec["decided_at"], "seq": rec["seq"],
                                    "model_version": rec["model_version"], "policy_version": rec["policy_version"],
                                    "score": rec["score"], "outcome": decision, "evidence_items": len(rec["evidence"])})
            return rec, s_

    def as_of(self, seq, at=None):
        """A copy of the twin with everything that became known after evidence sequence `seq` removed."""
        with self.lock:
            t = self.fork()
        t.accounts = {k: a for k, a in t.accounts.items() if getattr(a, "created_seq", 0) <= seq}
        for a in t.accounts.values():
            for attr in ("devices", "ips", "pay_ids"):
                cur = getattr(a, attr)
                keep = [x for x in cur if t.link_meta.get((a.id, x), {"seq": 0})["seq"] <= seq]
                fresh = TrackedSet(t, a.id)
                set.update(fresh, keep)
                setattr(a, attr, fresh)
            if a.status == "frozen" and getattr(a, "frozen_seq", 0) > seq:
                a.status = "active"
        t.orders = [o for o in t.orders if o.get("seq", 0) <= seq and o["account"] in t.accounts]
        t.transfers = [x for x in t.transfers if x.get("seq", 0) <= seq and x["src"] in t.accounts and x["dst"] in t.accounts]
        t.xfer_nb = {}
        for x in t.transfers:
            t.xfer_nb.setdefault(x["src"], set()).add(x["dst"])
            t.xfer_nb.setdefault(x["dst"], set()).add(x["src"])
        t.known_fraud = {a for a in t.known_fraud if t.label_meta.get(a, {"seq": 0})["seq"] <= seq}
        t.label_meta = {a: m for a, m in t.label_meta.items() if m["seq"] <= seq}
        t.link_meta = {k: m for k, m in t.link_meta.items() if m["seq"] <= seq}
        if at is not None:
            t.clock = at
        t._eseq = seq
        t._dirty = True
        return t

    def replay(self, decision_id, durable=None):
        """Re-run a stored decision on the graph as it was at decision time, with the stored model and policy.

        With a durable audit store (the default when one is attached) the graph is rebuilt from the persisted graph
        event log and scored with the persisted model artifact, so this works the same after a restart."""
        if durable is None:
            durable = self.audit is not None
        if durable and self.audit is not None:
            out = self.replay_durable(decision_id)
            if out is not None:
                return out
        if decision_id not in self.decisions:  # made on a scenario's copy of the twin: replay it there
            for prefix, fork in getattr(self, "replay_forks", {}).items():
                if decision_id.startswith(prefix):
                    return fork.replay(decision_id)
        rec = self.decisions[decision_id]
        cur = getattr(self, "model_version", None)
        model = self.model_versions.get(rec["model_version"])
        if model is None:
            return {"decision": decision_id, "replayable": False, "identical": False,
                    "reason": f"model version {rec['model_version']} is not available (current {cur})"}
        t = self.as_of(rec["seq"], rec["decided_at"])
        t.model = copy.deepcopy(model["gat"])
        t.order_model = copy.deepcopy(model["order_model"])
        t.model_version = rec["model_version"]
        i = rec["inputs"]
        s_ = t.score_order(i["account"], i["merchant"], i["amount"], i["device"], i["ip"])
        outcome = t.decide(s_["risk"], i["amount"], i["new_device"], rec["policy"], s_["spend_ratio"])
        ev_now = t._evidence(i["account"], i["device"], i["ip"])
        same_ev = [(e["item"], e["seq"]) for e in ev_now] == [(e["item"], e["seq"]) for e in rec["evidence"]]
        later = [e for e in self._evidence(i["account"], i["device"], i["ip"]) if e["seq"] > rec["seq"]]
        return {"decision": decision_id, "replayable": True, "point": rec["point"], "decided_at": rec["decided_at"],
                "model_version": rec["model_version"], "current_model_version": cur,
                "policy_version": rec["policy_version"], "stored": {"score": rec["score"], "outcome": rec["outcome"]},
                "replayed": {"score": s_["risk"], "outcome": outcome},
                "identical": abs(s_["risk"] - rec["score"]) < 1e-9 and outcome == rec["outcome"] and same_ev
                and s_["features"] == rec["features"],
                "evidence_identical": same_ev, "features_identical": s_["features"] == rec["features"],
                "evidence_added_since": [{k: e[k] for k in ("item", "kind", "available_at", "provenance")} for e in later]}

    def _rules(self, acc, amount, device, ip):
        """The rules engine: new device, foreign IP, spend jump, velocity, account age, frozen."""
        hist = [o for o in self.orders if o["account"] == acc.id and o["status"] != "blocked"]
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
        return t, factors, ratio, new_device

    def _train_order_model(self):
        """Train the default engine on the seeded order history, with features as the twin knows them now."""
        from .graph_rf import OrderModel
        nodes, idx, X, _ = self.build_graph()
        rows, ys = [], []
        for o in self.orders:
            if o["source"] != "history" or o["account"] not in idx:
                continue
            acc = self.accounts[o["account"]]
            t, _, ratio, nd = self._rules(acc, o["amount"], o["device"], o["ip"])
            rows.append(vector([float(v) for v in X[idx[o["account"]]][4:4 + 17]],
                               graph_features(self, o["account"], o["device"], o["ip"]), [min(t, 1), ratio, float(nd)]))
            ys.append(int(bool(o["fraud"])))
        self.order_model = OrderModel(seed=7).fit(rows, ys)

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
            order["seq"] = self._bump()
            self.orders.append(order)
            if decision != "block":
                with self.provenance(f"order {order['id']}"):
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
            self.accounts[account].frozen_seq = self._bump()
            self.label_meta[account] = {"seq": self.accounts[account].frozen_seq, "at": round(self.clock, 2),
                                        "prov": f"investigator freeze: {reason[:80]}"}
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

    def fork(self, durable=False):
        """A private copy. durable=True: its decisions are persisted too, in a child epoch of this twin's event log
        (the copy's events start after the evidence sequence it was copied at)."""
        if durable and self.audit is not None:
            self.flush_events()
        with self.lock:
            # Never touch the live twin's lock or hook while copying: other request threads may be
            # using them. The memo makes deepcopy give the fork a fresh lock and no event hook.
            memo = {id(self.lock): threading.RLock()}
            hook = getattr(self, "on_event", None)
            if hook is not None:
                memo[id(hook)] = None
            if getattr(self, "ledger", None) is not None:  # a fork never writes to the live ledger
                memo[id(self.ledger)] = None
            if "replay_forks" in self.__dict__:  # nor carries other scenarios' copies
                memo[id(self.replay_forks)] = {}
            if self.audit is not None:  # the database handle is shared, never copied
                memo[id(self.audit)] = self.audit if durable else None
            twin = copy.deepcopy(self, memo)
            twin.on_event = None
            if durable and self.audit is not None:
                twin.epoch = self.audit.new_epoch(self.epoch, self._eseq, "scenario copy")
                twin._flushed_seq = self._eseq
            else:
                twin.audit, twin.epoch = None, None
            return twin

    # ------------------------------------------------- durable audit trail
    def attach_audit(self, audit, note="live twin"):
        """Persist this twin's graph events, model artifacts and decisions from now on (see app/audit.py)."""
        with self.lock:
            self.audit = audit
            self.epoch = audit.new_epoch(note=note)
            self._flushed_seq = 0
            for ver, art in self.model_versions.items():
                audit.save_model(ver, art)
            self.flush_events()
        return self.epoch

    def _graph_delta(self, after):
        ev = []
        for a in self.accounts.values():
            cs = getattr(a, "created_seq", 0)
            if cs > after:
                ev.append(("account", a.id, cs, {"id": a.id, "name": a.name, "country": a.country,
                                                 "age_days": a.age_days, "email": a.email, "kyc": a.kyc,
                                                 "fraud": a.fraud, "segment": a.segment,
                                                 "at": getattr(a, "created_at", 0),
                                                 "prov": getattr(a, "created_prov", "seed fixture")}))
            fs = getattr(a, "frozen_seq", 0)
            if fs > after:
                lm = self.label_meta.get(a.id, {})
                ev.append(("freeze", f"{a.id}@{fs}", fs, {"account": a.id, "at": lm.get("at", 0),
                                                         "prov": lm.get("prov", "freeze")}))
        for (acc, node), m in self.link_meta.items():
            if m["seq"] > after:
                ev.append(("link", f"{acc}|{node}", m["seq"], {"account": acc, "node": node, "at": m["at"],
                                                              "prov": m["prov"], "country": self.ips.get(node)}))
        ev += [("order", o["id"], o["seq"], o) for o in self.orders if o.get("seq", 0) > after]
        ev += [("transfer", str(x["seq"]), x["seq"], x) for x in self.transfers if x.get("seq", 0) > after]
        ev += [("labels", f"labels@{le['seq']}", le["seq"], le) for le in self.label_events if le["seq"] > after]
        return ev

    def flush_events(self):
        """Append every graph event since the last flush to the durable event log."""
        if self.audit is None:
            return 0
        with self.lock:
            upto = self._eseq
            ev = self._graph_delta(self._flushed_seq)
            self.audit.append_events(self.epoch, ev)
            self._flushed_seq = upto
            return len(ev)

    @classmethod
    def from_events(cls, events, seq=None, at=None):
        """Rebuild the graph from the persisted event log alone (no seed): accounts, links, orders, transfers,
        labels and freezes, in evidence-sequence order."""
        t = cls(populate=False)
        for kind, _key, s_, p in events:
            if kind == "account":
                a = Account(id=p["id"], name=p["name"], country=p["country"], age_days=p["age_days"],
                            email=p["email"], kyc=p["kyc"], fraud=p["fraud"], segment=p["segment"])
                a.devices, a.ips, a.pay_ids = TrackedSet(), TrackedSet(), TrackedSet()
                for ts_ in (a.devices, a.ips, a.pay_ids):
                    ts_._twin, ts_._owner = t, a.id
                a.created_seq, a.created_at, a.created_prov = s_, p["at"], p["prov"]
                t.accounts[a.id] = a
            elif kind == "link":
                a, node = t.accounts.get(p["account"]), p["node"]
                if a is None:
                    continue
                if node.startswith("D-"):
                    set.add(a.devices, node)
                    t.devices.add(node)
                elif node.startswith("IP-"):
                    set.add(a.ips, node)
                    t.ips[node] = p.get("country") or a.country
                else:
                    set.add(a.pay_ids, node)
                    t.pay_ids.add(node)
                t.link_meta[(a.id, node)] = {"seq": s_, "at": p["at"], "prov": p["prov"]}
            elif kind == "order":
                t.orders.append(dict(p))
                if p.get("device"):
                    t.devices.add(p["device"])
                if p.get("ip"):
                    t.ips.setdefault(p["ip"], p.get("ip_country"))
            elif kind == "transfer":
                t.transfers.append(dict(p))
                t.xfer_nb.setdefault(p["src"], set()).add(p["dst"])
                t.xfer_nb.setdefault(p["dst"], set()).add(p["src"])
            elif kind == "labels":
                t.known_labelled, t.known_fraud = set(p["labelled"]), set(p["fraud"])
                t.label_events.append(dict(p))
                for a in p["fraud"]:
                    t.label_meta.setdefault(a, {"seq": s_, "at": p["at"], "prov": p["prov"]})
            elif kind == "freeze" and p["account"] in t.accounts:
                t.accounts[p["account"]].status = "frozen"
                t.accounts[p["account"]].frozen_seq = s_
                t.label_meta[p["account"]] = {"seq": s_, "at": p["at"], "prov": p["prov"]}
        t._eseq = seq if seq is not None else max((e[2] for e in events), default=0)
        if at is not None:
            t.clock = at
        t._dirty = True
        return t

    def replay_durable(self, decision_id):
        """Replay from the database only: the stored decision record, the graph event log and the model artifact."""
        a = self.audit
        rec = a.decision(decision_id)
        if rec is None:
            return None
        cur = getattr(self, "model_version", None)
        base = {"decision": decision_id, "point": rec["point"], "decided_at": rec["decided_at"],
                "model_version": rec["model_version"], "current_model_version": cur,
                "policy_version": rec["policy_version"],
                "source": "persisted graph event log + stored model artifact (survives restarts)"}
        art = a.load_model(rec["model_version"])
        if art is None:
            return {**base, "replayable": False, "identical": False,
                    "reason": f"model artifact {rec['model_version']} is not in the artifact store, so this decision "
                              "cannot be replayed (it is never re-scored with a different model)"}
        self.flush_events()
        evs = a.events(rec["epoch"], upto=rec["seq"])
        t = Twin.from_events(evs, rec["seq"], rec["decided_at"])
        i = rec["inputs"]
        if i["account"] not in t.accounts:
            return {**base, "replayable": False, "identical": False,
                    "reason": "the decision's account is missing from the persisted event log"}
        if i.get("device"):
            t.devices.add(i["device"])
        if i.get("ip"):
            t.ips.setdefault(i["ip"], i.get("ip_country") or t.accounts[i["account"]].country)
        t.model, t.order_model = copy.deepcopy(art["gat"]), copy.deepcopy(art.get("order_model"))
        t.seq_model = copy.deepcopy(art.get("seq_model"))
        t.features = list(art.get("features") or FEATURES)
        t.model_version = rec["model_version"]
        s_ = t.score_order(i["account"], i["merchant"], i["amount"], i["device"], i["ip"])
        outcome = t.decide(s_["risk"], i["amount"], i["new_device"], rec["policy"], s_["spend_ratio"])
        ev_now = t._evidence(i["account"], i["device"], i["ip"])
        same_ev = [(e["item"], e["seq"]) for e in ev_now] == [(e["item"], e["seq"]) for e in rec["evidence"]]
        later_t = Twin.from_events(a.events(rec["epoch"]))
        later = []
        if i["account"] in later_t.accounts:
            later_t.devices.add(i["device"])
            later_t.ips.setdefault(i["ip"], i.get("ip_country"))
            later = [e for e in later_t._evidence(i["account"], i["device"], i["ip"])
                     if e["seq"] > rec["seq"] and e["provenance"] != "this request (checkout session)"]
        return {**base, "replayable": True, "events_replayed": len(evs),
                "stored": {"score": rec["score"], "outcome": rec["outcome"]},
                "replayed": {"score": s_["risk"], "outcome": outcome},
                "identical": abs(s_["risk"] - rec["score"]) < 1e-9 and outcome == rec["outcome"] and same_ev
                and s_["features"] == rec["features"],
                "evidence_identical": same_ev, "features_identical": s_["features"] == rec["features"],
                "evidence_added_since": [{k: e[k] for k in ("item", "kind", "available_at", "provenance")}
                                         for e in later]}


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
