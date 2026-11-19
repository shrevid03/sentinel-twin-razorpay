"""Compliance as config (RBI-grounded rules from compliance_rules.yaml) and the append-only, hash-chained ledger.

Every rule in compliance_rules.yaml names an `enforce` hook; each hook below is called from the code path it guards,
and loading fails if the file names a hook this module does not implement.

The ledger keeps the model's recommendation and the human's decision as SEPARATE records linked by case_id (the
accountability spine from Abuse-Ring Sentinel). Each record carries the hash of the previous one, so editing or
deleting any past record breaks verify(). It is written to a JSONL file (LEDGER_PATH) as well as kept in memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

import yaml

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "compliance_rules.yaml")
HOOKS = {"block_auto_classification", "append_only_ledger", "require_reason_and_reviewer", "mark_reportable"}
MACHINE_REVIEWERS = ("reviewer twin", "model", "gat", "graphsage", "system", "autopilot")


class ComplianceError(Exception):
    def __init__(self, rule_id, detail):
        super().__init__(f"{rule_id}: {detail}")
        self.rule_id, self.detail = rule_id, detail


def load_rules(path=RULES_PATH):
    with open(path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)["rules"]
    unknown = {r["enforce"] for r in rules} - HOOKS
    if unknown:
        raise ValueError(f"compliance_rules.yaml names unknown enforcement hooks: {sorted(unknown)}")
    return rules


RULES = load_rules()
BY_HOOK = {r["enforce"]: r for r in RULES}


def rule_id(hook):
    return BY_HOOK[hook]["id"] if hook in BY_HOOK else hook


def active(hook):
    return hook in BY_HOOK


# ------------------------------------------------------------------ hooks
def require_reason_and_reviewer(reviewer, reason):
    """RBI-ACC-03: a human decision needs a named reviewer and a reason."""
    if not active("require_reason_and_reviewer"):
        return
    if not (reviewer or "").strip():
        raise ComplianceError(rule_id("require_reason_and_reviewer"), "a decision needs the reviewer's identity")
    if not (reason or "").strip():
        raise ComplianceError(rule_id("require_reason_and_reviewer"), "a decision needs a reason")


def is_machine(reviewer):
    r = (reviewer or "").lower()
    return any(m in r for m in MACHINE_REVIEWERS)


def block_auto_classification(decision, reviewer):
    """RBI-NJ-01: only a named human may classify an account as fraud (or approve an adverse action)."""
    if active("block_auto_classification") and decision in ("confirm_fraud", "freeze") and is_machine(reviewer):
        raise ComplianceError(rule_id("block_auto_classification"),
                              "an account cannot be classified as fraud without a human's sign-off")


def triage_with_natural_justice(action):
    """RBI-NJ-01 for model output: a 'flag' recommendation is routed to human review, never applied."""
    if active("block_auto_classification") and action == "flag":
        return "review", {"claim": "The model cannot classify this account as fraud; a human must review and sign off.",
                          "evidence": [rule_id("block_auto_classification") + " (natural justice: opportunity to be heard)"],
                          "source": "compliance_rules.yaml"}
    return action, None


def mark_reportable(record):
    """RBI-RPT-04: a human-confirmed fraud case is marked reportable with its confirmation date."""
    if active("mark_reportable") and record.get("human_decision") == "confirm_fraud":
        record["reportable"] = True
        record["reportable_since"] = time.strftime("%Y-%m-%d")
        record["rule"] = rule_id("mark_reportable")
    return record


# ------------------------------------------------------------------ ledger
def _hash(rec):
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class Ledger:
    """RBI-AUD-02: append-only, hash-chained. There is deliberately no update or delete method."""

    def __init__(self, path=None):
        self.path = path if path is not None else os.getenv("LEDGER_PATH", "")
        self._records: list[dict] = []
        self._lock = threading.Lock()

    def append(self, rec):
        with self._lock:
            rec = {**rec, "seq": len(self._records) + 1, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "prev": self._records[-1]["hash"] if self._records else "genesis"}
            rec["hash"] = _hash(rec)
            self._records.append(rec)
            if self.path:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
            return dict(rec)

    def records(self, case_id=None, kind=None):
        return [dict(r) for r in self._records
                if (case_id is None or r.get("case_id") == case_id) and (kind is None or r.get("type") == kind)]

    def verify(self):
        prev = "genesis"
        for i, r in enumerate(self._records):
            if r["prev"] != prev or _hash(r) != r["hash"] or r["seq"] != i + 1:
                return {"ok": False, "broken_at": r.get("seq"), "records": len(self._records)}
            prev = r["hash"]
        return {"ok": True, "records": len(self._records), "head": prev[:16]}

    def metrics(self):
        recs = {r["case_id"]: r for r in self._records if r["type"] == "recommendation"}
        dec = [r for r in self._records if r["type"] == "decision" and r["case_id"] in recs]
        if not dec:
            return {"resolved": 0}

        def canon(x):
            return "fraud" if x in ("flag", "review", "confirm_fraud", "approve", "released") else "clear"

        overrides = sum(canon(recs[d["case_id"]]["recommendation"]) != canon(d["human_decision"]) for d in dec)
        return {"resolved": len(dec), "override_rate": round(overrides / len(dec), 3),
                "agreement_rate": round(1 - overrides / len(dec), 3),
                "reportable": sum(bool(d.get("reportable")) for d in dec)}
