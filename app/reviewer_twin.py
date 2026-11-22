"""Reviewer twin: a digital twin of the human who works the review queue.

Every human approve / reject on the queue (held agent checkouts and investigator proposals) is recorded with the
case as it looked at decision time: risk score, how many signals fired, the known-fraud-neighbour share, the
rupee amount, the action type, account age and linked flagged accounts. Only numbers and synthetic ids are
stored, never names, emails, phone numbers or payment identifiers.

A small logistic regression learns P(human approves) from those decisions and explains each prediction with
its largest feature contributions. Cold start: it is seeded with clearly labelled synthetic decisions drawn from
a written reviewer policy (REVIEWER_POLICY), and always reports "trained on N real + M seeded".

Shadow mode by default: it only predicts. Agreement is measured honestly: each prediction is made and stored
*before* the human decides, and the model is refit only afterwards.

Autopilot is OFF by default and in the demo. Only a named admin with a written reason can switch it on (recorded
in the ledger); releases are logged as "reviewer twin under policy enabled by <admin>". It may only release held checkouts (reversible: releasing creates
a Razorpay order that still needs the buyer to pay, and an undo cancels it) under a rupee cap and at
confidence >= 0.95. It never refunds, freezes or captures, and every autopilot decision is logged and undoable.
"""
from __future__ import annotations

import math
import random
import threading
import time

import numpy as np

from .money import rs

FEATURES = [  # held checkouts and proposed actions get separate terms: a high risk argues *against* releasing a hold
    ("risk_hold", "risk score"),                    # but *for* approving a restriction or refund
    ("share_hold", "known-fraud-neighbour share"),
    ("linked_hold", "linked flagged accounts"),
    ("amount_hold", "₹ amount"),
    ("age_hold", "account age"),
    ("signals_hold", "signals fired"),
    ("risk_action", "risk score"),
    ("share_action", "known-fraud-neighbour share"),
    ("linked_action", "linked flagged accounts"),
    ("signals_action", "evidence items"),
    ("is_hold", "held checkout"),
    ("is_refund", "refund proposal"),
    ("is_freeze", "restriction proposal"),
]
BASE = {"risk": "risk", "share": "neighbour_share", "linked": "linked_flagged", "amount": "amount",
        "age": "account_age", "signals": "signals"}
NAMES = [f for f, _ in FEATURES]
LABEL = dict(FEATURES)

REVIEWER_POLICY = (
    "Seed policy (synthetic, written down so the cold-start model is explainable): release a held checkout when "
    "risk is under 0.6, no linked account is known fraud and the amount is at most ₹10,000; otherwise reject it. "
    "Approve a restriction when risk is at least 0.7 and the account shares identifiers with known fraud or with "
    "two or more flagged accounts. Approve a refund when risk is at least 0.75."
)

AUTOPILOT_DEFAULTS = {"enabled": False, "max_inr": 5000.0, "min_confidence": 0.95, "enabled_by": None, "reason": None}
REVERSIBLE = {"hold"}  # the only kind autopilot may touch; refunds, freezes and captures are never automated


def case_features(twin, kind, item, account=None):
    """Numeric case features at decision time. kind: 'hold' | 'action'. No PII leaves this function."""
    a_type = "hold" if kind == "hold" else item.get("type")
    account = account or item.get("account")
    amount = 0.0
    if kind == "hold":
        risk, signals, amount = float(item.get("risk") or 0), len(item.get("factors") or []), float(item.get("total") or 0)
    else:
        target = item.get("target")
        signals = len(item.get("evidence") or [])
        if a_type == "refund":
            o = next((x for x in twin.orders if x["id"] == target), None)
            account = account or (o or {}).get("account")
            amount = float((o or {}).get("amount") or 0)
            risk = float((o or {}).get("risk") or 0)
        else:
            account = account or target
            risk = float(twin.risk(account)) if account in twin.accounts else 0.0
            amount = sum(o["amount"] for o in twin.orders if o["account"] == account)
    acc = twin.accounts.get(account)
    share, age, flagged = 0.0, 365, 0
    if acc is not None:
        share = twin.known_fraud_signal(account)["share"]
        age = acc.age_days
        flagged = sum(s["flagged"] for s in twin.shared_identifiers(account))
    return {"risk": round(risk, 4), "signals": signals, "neighbour_share": round(share, 4),
            "amount": round(math.log1p(amount / 1000), 4), "account_age": round(math.log1p(age) / math.log1p(2000), 4),
            "linked_flagged": round(math.log1p(flagged), 4), "is_hold": float(a_type == "hold"),
            "is_refund": float(a_type == "refund"), "is_freeze": float(a_type == "freeze"),
            "_amount_inr": round(amount, 2)}


def _vec(f):
    h = float(f["is_hold"])
    out = []
    for n in NAMES:
        if n.startswith("is_"):
            out.append(float(f[n]))
        elif n.endswith("_hold"):
            out.append(h * float(f[BASE[n[:-5]]]))
        elif n.endswith("_action"):
            out.append((1 - h) * float(f[BASE[n[:-7]]]))
        else:
            out.append(float(f[n]))
    return np.array(out)


def policy_decision(f, strict=False):
    """The written seed policy (and a stricter variant used by the replay's simulated reviewer)."""
    amount = math.expm1(f["amount"]) * 1000
    age = math.expm1(f["account_age"] * math.log1p(2000))
    linked = math.expm1(f["linked_flagged"])
    if f["is_hold"]:
        ok = f["risk"] < 0.6 and f["neighbour_share"] == 0 and amount <= 10000
        if strict and amount > 6000 and age < 120:  # this reviewer is wary of big carts on young accounts
            ok = False
        return ok
    if f["is_freeze"]:
        return f["risk"] >= 0.7 and (f["neighbour_share"] > 0 or linked >= 2)
    return f["risk"] >= 0.75 or (strict and f["risk"] >= 0.6 and f["signals"] >= 3)


def synthetic_case(rng):
    """A plausible queue item's features, drawn from a seeded RNG (used for seeding and the replay demo)."""
    kind = rng.choices(["hold", "freeze", "refund"], weights=[5, 3, 2])[0]
    bad = rng.random() < 0.45
    risk = min(1.0, max(0.0, rng.gauss(0.78 if bad else 0.42, 0.14)))
    share = round(rng.uniform(0.3, 0.85), 3) if (bad and rng.random() < 0.6) else 0.0
    linked = rng.choice([2, 3, 5, 8]) if (bad and rng.random() < 0.6) else rng.choice([0, 0, 1])
    amount = rng.choice([1999, 2699, 4999, 7499, 9999, 12000]) if kind == "hold" else rng.uniform(1500, 60000)
    age = rng.randint(3, 60) if bad and rng.random() < 0.6 else rng.randint(30, 1500)
    return {"risk": round(risk, 4), "signals": rng.randint(2, 5) if bad else rng.randint(0, 3),
            "neighbour_share": share, "amount": round(math.log1p(amount / 1000), 4),
            "account_age": round(math.log1p(age) / math.log1p(2000), 4), "linked_flagged": round(math.log1p(linked), 4),
            "is_hold": float(kind == "hold"), "is_refund": float(kind == "refund"), "is_freeze": float(kind == "freeze"),
            "_amount_inr": round(amount, 2)}


class ReviewerTwin:
    def __init__(self, team, seeded=40, seed=17):
        self.team = team
        self.lock = threading.RLock()
        self.records: list[dict] = []  # every decision, real or seeded, oldest first
        self.autopilot = dict(AUTOPILOT_DEFAULTS)
        self.autopilot_log: list[dict] = []
        self.w = np.zeros(len(NAMES))
        self.b = 0.0
        self.mu = np.zeros(len(NAMES))
        self.sd = np.ones(len(NAMES))
        rng = random.Random(seed)
        for _ in range(seeded):
            f = synthetic_case(rng)
            self.records.append({"id": None, "kind": "seed", "source": "seeded", "features": f,
                                 "human_approved": bool(policy_decision(f)), "prediction": None,
                                 "at": time.strftime("%Y-%m-%d %H:%M:%S"), "reviewer": "seed policy"})
        self.fit()

    # ---------------------------------------------------------------- model
    def fit(self, epochs=600, lr=0.2, wd=0.02):
        """Class-weighted L2 logistic regression, full batch from zeros: deterministic for the same records."""
        with self.lock:
            X = np.array([_vec(r["features"]) for r in self.records])
            y = np.array([float(r["human_approved"]) for r in self.records])
            sw = np.array([1.0 if r["source"] == "seeded" else 2.0 for r in self.records])  # real decisions count double
            self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
            Z = (X - self.mu) / self.sd
            pos = max(y.sum(), 1.0)
            neg = max(len(y) - y.sum(), 1.0)
            cw = np.where(y == 1, len(y) / (2 * pos), len(y) / (2 * neg)) * sw
            w, b = np.zeros(Z.shape[1]), 0.0
            for _ in range(epochs):
                p = 1 / (1 + np.exp(-(Z @ w + b)))
                g = cw * (p - y)
                w -= lr * (Z.T @ g / len(y) + wd * w)
                b -= lr * g.mean()
            self.w, self.b = w, b

    def predict_features(self, f):
        z = (_vec(f) - self.mu) / self.sd
        contrib = self.w * z
        p = float(1 / (1 + math.exp(-(float(contrib.sum()) + self.b))))
        order = np.argsort(-np.abs(contrib))
        reasons = []
        for j in order[:5]:
            if len(reasons) == 3:
                break
            if abs(contrib[j]) < 0.05:
                continue
            name = NAMES[j]
            base = BASE.get(name.rsplit("_", 1)[0]) if not name.startswith("is_") else name
            if name.startswith("is_"):
                continue  # "it is a held checkout" is context, not a reason
            if name.endswith("_hold") != bool(f["is_hold"]) and not name.startswith("is_"):
                continue  # the term for the other case type is zero here
            direction = "towards approve" if contrib[j] > 0 else "towards reject"
            reasons.append(f"{LABEL[name]} {self._fmt(base, f)} ({direction})".replace("  ", " "))
        decision = "approve" if p >= 0.5 else "reject"
        return {"p_approve": round(p, 3), "decision": decision, "confidence": round(max(p, 1 - p), 3),
                "reasons": reasons}

    @staticmethod
    def _fmt(name, f):
        if name == "amount":
            return rs(f.get("_amount_inr", math.expm1(f["amount"]) * 1000))
        if name == "account_age":
            return f"{round(math.expm1(f['account_age'] * math.log1p(2000)))} days"
        if name == "linked_flagged":
            return str(round(math.expm1(f["linked_flagged"])))
        if name.startswith("is_"):
            return ""
        if name == "signals":
            return str(int(f["signals"]))
        return f"{f[name]:.2f}" if isinstance(f[name], float) else str(f[name])

    def predict(self, kind, item):
        f = case_features(self.team.twin, kind, item)
        return {**self.predict_features(f), "mode": "autopilot" if self.autopilot["enabled"] else "shadow"}

    # ------------------------------------------------------------ learning
    def observe(self, kind, item, approved, reviewer, source="real", features=None):
        """Record one human decision: predict FIRST (stored as the before-decision prediction), then refit."""
        with self.lock:
            f = features or case_features(self.team.twin, kind, item)
            pred = self.predict_features(f)
            rec = {"id": item.get("id"), "kind": kind if kind == "hold" else item.get("type", kind), "source": source,
                   "features": f, "human_approved": bool(approved), "prediction": pred, "reviewer": reviewer,
                   "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self.records.append(rec)
            self.fit()
            return rec

    def agreement(self, last_n=30, sources=("real", "replay")):
        rows = [r for r in self.records if r["source"] in sources and r["prediction"]][-last_n:]
        if not rows:
            return {"n": 0, "rate": None, "last_n": last_n}
        hits = sum((r["prediction"]["decision"] == "approve") == r["human_approved"] for r in rows)
        return {"n": len(rows), "rate": round(hits / len(rows), 3), "last_n": last_n}

    def status(self):
        counts = {s: sum(r["source"] == s for r in self.records) for s in ("real", "replay", "seeded")}
        trained = f"trained on {counts['real']} real + {counts['replay']} replayed + {counts['seeded']} seeded decisions"
        return {"trained_on": counts, "label": trained, "agreement": self.agreement(),
                "agreement_real_only": self.agreement(sources=("real",)),
                "weights": sorted(({"feature": LABEL[n] + ("" if n.startswith("is_") else
                                                           " · held checkouts" if n.endswith("_hold") else " · proposals"),
                                    "weight": round(float(w), 3)} for n, w in zip(NAMES, self.w)),
                                  key=lambda x: -abs(x["weight"])),
                "policy": REVIEWER_POLICY, "autopilot": dict(self.autopilot),
                "autopilot_log": self.autopilot_log[-20:],
                "recent": [{k: r[k] for k in ("id", "kind", "source", "human_approved", "prediction", "reviewer", "at")}
                           for r in self.records if r["source"] != "seeded"][-12:][::-1]}

    # --------------------------------------------------------------- replay
    def replay(self, n=30, seed=99):
        """Demo: a simulated reviewer (a slightly stricter version of the seed policy) works n synthetic cases.

        The twin predicts each one before the decision, then learns from it, so the agreement shown is honest.
        Recorded as source 'replay' (synthetic), never mixed up with real decisions."""
        rng = random.Random(seed)
        before = self.agreement(sources=("replay",))
        for i in range(n):
            f = synthetic_case(rng)
            approved = policy_decision(f, strict=True)
            if rng.random() < 0.05:  # humans are not perfectly consistent
                approved = not approved
            self.observe("hold" if f["is_hold"] else ("refund" if f["is_refund"] else "freeze"),
                         {"id": f"REPLAY-{seed}-{i + 1:02d}"}, approved, "simulated reviewer", source="replay",
                         features=f)
        rows = [r for r in self.records if r["source"] == "replay"][-n:]
        first, second = rows[: n // 2], rows[n // 2:]

        def rate(rs_):
            return round(sum((r["prediction"]["decision"] == "approve") == r["human_approved"] for r in rs_) / max(len(rs_), 1), 3)

        return {"replayed": n, "agreement": self.agreement(sources=("replay",), last_n=n),
                "agreement_first_half": rate(first), "agreement_second_half": rate(second),
                "agreement_before": before, "status": self.status()}

    # ------------------------------------------------------------ autopilot
    def set_autopilot(self, enabled=None, max_inr=None, min_confidence=None, admin=None, reason=None):
        """The authorization boundary. Autopilot is OFF unless a named admin switches it on with a written reason;
        that admin, the reason and the limits are written to the hash-chained ledger, and every release it makes is
        attributed to "reviewer twin under policy enabled by <admin>". It only ever releases holds (creating a
        Razorpay order the buyer must still pay); it never captures, refunds, restricts or moves money."""
        from .compliance import MACHINE_REVIEWERS
        admin, reason = (admin or "").strip(), (reason or "").strip()
        with self.lock:
            if enabled:
                words_ = set(admin.lower().replace("-", " ").split())
                if not admin or any(set(m.split()) <= words_ for m in MACHINE_REVIEWERS):
                    raise PermissionError("enabling autopilot needs a named human admin")
                if len(reason) < 8:
                    raise PermissionError("enabling autopilot needs a written reason (8+ characters)")
            if max_inr is not None:
                self.autopilot["max_inr"] = max(0.0, min(float(max_inr), 12500.0))  # never above the mandate limit
            if min_confidence is not None:
                self.autopilot["min_confidence"] = max(0.95, min(float(min_confidence), 1.0))  # never below 0.95
            if enabled is not None:
                self.autopilot["enabled"] = bool(enabled)
                self.autopilot.update(enabled_by=admin if enabled else None, reason=reason if enabled else None,
                                      changed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
                ledger = getattr(self.team, "ledger", None)
                if ledger is not None:
                    ledger.append({"type": "autopilot_policy", "by": admin or "admin", "enabled": bool(enabled),
                                   "reason": reason, "max_inr": self.autopilot["max_inr"],
                                   "min_confidence": self.autopilot["min_confidence"],
                                   "scope": "release held checkouts only; never captures, refunds, restricts or "
                                            "moves money"})
            return dict(self.autopilot)

    def reviewer_label(self):
        return f"reviewer twin under policy enabled by {self.autopilot.get('enabled_by') or 'unknown admin'}"

    def try_autopilot(self, kind, item):
        """Maybe decide one queue item. Returns the log entry, or None when the human must decide."""
        ap = self.autopilot
        if not ap["enabled"]:
            return None
        if kind not in REVERSIBLE:  # refunds, restrictions and captures always need a human
            return None
        pred = self.predict(kind, item)
        if pred["decision"] != "approve" or pred["confidence"] < ap["min_confidence"]:
            return None
        if float(item.get("total") or 0) > ap["max_inr"]:
            return None
        res = self.team.decide_hold(item["id"], True, self.reviewer_label(),
                                    f"auto-released at confidence {pred['confidence']:.2f}; undo available",
                                    learn=False)  # it never trains on its own decisions
        entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "hold_id": item["id"], "total": item.get("total"),
                 "confidence": pred["confidence"], "reasons": pred["reasons"], "pay_order_id": res.get("pay_order_id"),
                 "undone": False, "reviewer": self.reviewer_label(), "policy_reason": ap.get("reason")}
        self.autopilot_log.append(entry)
        return entry

    def undo(self, hold_id):
        entry = next((e for e in reversed(self.autopilot_log) if e["hold_id"] == hold_id), None)
        if entry is None:
            raise KeyError(hold_id)
        if entry["undone"]:
            return {**entry, "duplicate": True}
        out = self.team.cancel_released_hold(hold_id, "undo of reviewer-twin autopilot")
        entry["undone"] = True
        entry["undo"] = out
        return entry
