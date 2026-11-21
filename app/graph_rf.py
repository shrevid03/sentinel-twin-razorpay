"""An alternative order-risk engine: a random forest on explicit graph features (TWIN_ENGINE=rf_graph).

On a chronological holdout of the twin's order stream (scripts/model_trial.py), at the same manual-review volume,
this forest caught as much fraud as the same forest plus the GAT's score and decides in about 10-20 ms instead of
100-300 ms. On the drill's fresh red-team attacks, though, the GAT engine caught more and blocked no good orders, so
the GAT stays the default (see the README, "Which model decides").

Features per order, all computed from what the twin knows at that moment:
  * the account's own features (the same per-account features the GAT uses);
  * explicit graph features: linked accounts, known-fraud-neighbour share and counts, shared identifiers and how
    many of their holders are known fraud, neighbour aggregates (age, KYC, disposable email), money-transfer partners
    and how many are known fraud;
  * the transaction signals the rules use: rules score, spend ratio, new device.
"""
from __future__ import annotations

import hashlib
import pickle

import numpy as np

DISPOSABLE = ("mailinator.com", "tempmail.dev", "10minute.email")


def graph_features(t, account, device, ip):
    acc = t.accounts[account]
    ids = acc.devices | acc.ips | acc.pay_ids | {device, ip}
    linked = [b for b in t.accounts.values() if b.id != account and (b.devices | b.ips | b.pay_ids) & ids]
    kf = t.known_fraud_signal(account, extra_ids=(device, ip))
    shared = t.shared_identifiers(account, extra_device=device)
    known_shared = sum(sum(1 for b in t.accounts.values() if b.id != account and b.id in t.known_fraud
                           and (s["id"] in b.devices or s["id"] in b.pay_ids)) for s in shared)
    ages = [min(b.age_days, 2000) / 2000 for b in linked] or [0]
    kyc = [float(b.kyc) for b in linked] or [1]
    disp = [float(b.email.endswith(DISPOSABLE)) for b in linked] or [0]
    partners = t.xfer_nb.get(account, set())
    return [len(linked), float(np.log1p(len(linked))), kf["share"], kf["known_fraud"], kf["labelled"], len(shared),
            known_shared, float(np.mean(ages)), float(np.min(ages)), float(np.mean(kyc)), float(np.mean(disp)),
            len(partners), sum(1 for p in partners if p in t.known_fraud)]


def vector(acc_row, graph, txn):
    return list(acc_row) + list(graph) + list(txn)


class OrderModel:
    kind = "rf_graph"

    def __init__(self, seed=7):
        from sklearn.ensemble import RandomForestClassifier
        self.rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample",
                                         n_jobs=1, random_state=seed)
        self.version = None

    def fit(self, X, y):
        self.rf.fit(np.asarray(X), np.asarray(y).astype(int))
        self.version = "rf-" + hashlib.sha256(pickle.dumps(self.rf)).hexdigest()[:12]
        return self

    def predict(self, x):
        return float(self.rf.predict_proba(np.asarray([x]))[0, 1])
