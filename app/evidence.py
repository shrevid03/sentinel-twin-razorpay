"""Deterministic evidence investigator.

The split of responsibilities:
    model (GAT)      detects risk and triages: auto-clear / review / flag
    this module      gathers readable graph evidence with NetworkX, every claim with record pointers and a source
    LLM (copilot)    only summarises and answers questions over that fixed evidence; it never decides
    human            makes the final decision, with a reason (compliance_rules.yaml)

The evidence is independent graph evidence, not an attribution of the GAT's internal reasoning (the GAT's own
attention is shown separately as "Why is it risky?"). A step with no evidence is dropped, so nothing is shown that
cannot be pointed at.
"""
from __future__ import annotations

import time

import networkx as nx
import numpy as np

import re

from . import compliance, guard
from .money import rs

BAND_LOW, BAND_HIGH = 0.35, 0.75  # triage bands on the account's risk: auto-clear < 0.35 <= review < 0.75 <= flag
PATTERNS = ("cycle", "fanout", "collusion", "shared_identifiers", "unclear")


def _flow_graph(twin):
    G = nx.DiGraph()
    G.add_nodes_from(twin.accounts)
    for i, t in enumerate(twin.transfers):
        if G.has_edge(t["src"], t["dst"]):
            G[t["src"]][t["dst"]]["ids"].append(f"T-{i + 1:05d}")
            G[t["src"]][t["dst"]]["amount"] += t["amount"]
        else:
            G.add_edge(t["src"], t["dst"], ids=[f"T-{i + 1:05d}"], amount=t["amount"])
    return G


def _short_cycle(G, node, max_len=12):
    """The shortest closed money loop through node (BFS back to itself), or None."""
    try:
        path = nx.shortest_path(G, node, node) if G.has_edge(node, node) else None
    except nx.NetworkXNoPath:
        path = None
    best = None
    for succ in G.successors(node):
        try:
            p = nx.shortest_path(G, succ, node)
        except nx.NetworkXNoPath:
            continue
        if len(p) <= max_len and (best is None or len(p) < len(best)):
            best = p
    return [node] + best if best else path


def evidence_pack(twin, account):
    """Readable, reproducible evidence for one account. Same twin state in, same pack out."""
    twin.refresh()
    acc = twin.accounts[account]
    G = _flow_graph(twin)
    steps, patterns = [], []
    risk = round(float(twin.risk(account)), 4)

    # shared devices and payment identifiers (record pointers: node ids and the accounts behind them)
    for s in twin.shared_identifiers(account):
        if s["accounts"] >= 2 or s["flagged"]:
            holders = sorted(b.id for b in twin.accounts.values()
                             if b.id != account and (s["id"] in b.devices or s["id"] in b.pay_ids))
            steps.append({"claim": f"Shares a {s['label']} with {s['accounts']} other account(s), "
                                   f"{s['flagged']} of them flagged.",
                          "evidence": [s["id"], *holders[:8]], "source": "twin graph (identifier links)"})
            patterns.append("shared_identifiers")

    # closed money loop through this account (layering)
    cyc = _short_cycle(G, account)
    if cyc and 3 <= len(cyc) <= 13:
        hops = list(zip(cyc[:-1], cyc[1:]))
        ids = [G[u][v]["ids"][0] for u, v in hops]
        steps.append({"claim": f"Sits on a closed money loop of {len(hops)} transfers (layering signature).",
                      "evidence": [" -> ".join(cyc), *ids], "source": "transfers ledger"})
        patterns.append("cycle")

    # fan-out hub: this account is one, or received from one
    hubs = [account] if G.out_degree(account) >= 6 else []
    hubs += [h for h in G.predecessors(account) if G.out_degree(h) >= 6]
    for h in hubs[:1]:
        tg = sorted(G.successors(h))
        window = [t["ts"] for t in twin.transfers if t["src"] == h]
        span = (max(window) - min(window)) / 60 if window else 0
        steps.append({"claim": f"{'Is' if h == account else 'Received money from'} a hub that paid {len(tg)} "
                               f"accounts within {span:.1f} hours (bust-out / mule signature).",
                      "evidence": [f"hub={h}", *tg[:8]], "source": "transfers ledger"})
        patterns.append("fanout")

    # collusion: a dense block with no triangles around the account (buyers x sellers)
    ego = nx.ego_graph(G.to_undirected(), account, radius=2)
    if ego.number_of_nodes() >= 6:
        tri = sum(nx.triangles(ego).values())
        dens = nx.density(ego)
        # a real buyer x seller block has many 4-cycles: at least 3 more edges than a tree would have, and two or
        # more accounts on each side with 2+ links inside it (a hub-and-spoke fan-out or a sparse tree does not)
        rich = ego.number_of_edges() >= ego.number_of_nodes() + 2
        two_sided = False
        if tri == 0 and rich and nx.is_bipartite(ego):
            left, right = nx.bipartite.sets(ego)
            two_sided = all(sum(ego.degree(n) >= 2 for n in side) >= 2 for side in (left, right))
        if tri == 0 and dens > 0.2 and rich and two_sided:
            steps.append({"claim": f"Embedded in a dense two-sided block ({ego.number_of_nodes()} accounts, "
                                   f"density {dens:.2f}, no triangles): buyer-seller collusion signature.",
                          "evidence": sorted(ego.nodes)[:10], "source": "transfers ledger (radius-2 ego network)"})
            patterns.append("collusion")

    # known-fraud neighbours (training labels and investigator-frozen accounts only)
    kf = twin.known_fraud_signal(account)
    if kf["known_fraud"]:
        steps.append({"claim": f"{kf['known_fraud']} of {kf['labelled']} labelled linked accounts are known fraud.",
                      "evidence": [f"share={kf['share']}", f"linked={kf['linked']}"],
                      "source": "known-fraud labels (training split + frozen accounts)"})

    # orders (amount pattern) and the account itself
    orders = [o for o in twin.orders if o["account"] == account]
    if orders:
        big = max(orders, key=lambda o: o["amount"])
        steps.append({"claim": f"{len(orders)} order{'s' if len(orders) != 1 else ''}, largest {rs(big['amount'])} at {twin.merchants[big['merchant']]['name']}.",
                      "evidence": [big["id"], f"account_age={acc.age_days}d", f"kyc={acc.kyc}"], "source": "orders"})
    steps.append({"claim": f"GAT risk score {risk:.2f}.", "evidence": [f"node={account}", f"score={risk:.4f}"],
                  "source": "GAT (graph attention network)"})

    action = "flag" if risk >= BAND_HIGH else "review" if risk >= BAND_LOW else "auto_clear"
    action, nj = compliance.triage_with_natural_justice(action)
    if nj:
        steps.append(nj)
    steps = [s for s in steps if s.get("evidence")]  # no evidence, no step
    pattern = next((p for p in ("cycle", "fanout", "collusion") if p in patterns),
                   "shared_identifiers" if "shared_identifiers" in patterns else "unclear")
    return {"account": account, "risk_score": risk, "recommendation": action, "pattern": pattern,
            "reasoning_steps": steps}


def embedding(twin, account):
    """The GAT's hidden representation of the account (for precedent search)."""
    nodes, idx, X, mask = twin.build_graph()
    if hasattr(twin.model, "embed"):  # GraphSAGE
        h = twin.model.embed(X, mask)[idx[account]]
    else:
        twin.model.forward(X, mask)
        h = np.maximum(twin.model._pre[idx[account]], 0)
    return [round(float(v), 4) for v in h]


def evidence_text(case):
    return "\n".join(f"- {s['claim']}  EVIDENCE[{', '.join(map(str, s['evidence']))}]  SOURCE[{s['source']}]"
                     for s in case["reasoning_steps"])


def deterministic_summary(case):
    lines = [f"- {s['claim']} [{', '.join(map(str, s['evidence'][:4]))}]" for s in case["reasoning_steps"]]
    return (f"Recommendation: {case['recommendation'].upper()} (pattern: {case['pattern']}, risk "
            f"{case['risk_score']:.2f}). A human decides.\n" + "\n".join(lines))


class CaseBook:
    """Opens evidence cases, records model recommendations and human decisions in the ledger, finds precedent."""

    def __init__(self, team):
        self.team = team
        self.ledger = compliance.Ledger()
        self.cases: dict[str, dict] = {}

    def open_case(self, account):
        tw = self.team.twin
        if account not in tw.accounts:
            raise KeyError(account)
        pack = evidence_pack(tw, account)
        cid = f"EV-{len(self.cases) + 1:04d}"
        case = {"id": cid, "opened": time.strftime("%Y-%m-%d %H:%M:%S"), **pack, "embedding": embedding(tw, account),
                "status": "open", "decision": None, "chat": []}
        case["precedent"] = self.precedent(case)
        self.cases[cid] = case
        self.ledger.append({"type": "recommendation", "case_id": cid, "account": account, "by": "model",
                            "model_score": pack["risk_score"], "recommendation": pack["recommendation"],
                            "pattern": pack["pattern"], "steps": len(pack["reasoning_steps"])})
        return self.public(case)

    def public(self, case):
        return {k: v for k, v in case.items() if k != "embedding"}

    def precedent(self, case, k=3):
        """The k most similar RESOLVED cases (cosine on the GAT embedding), with the human's decision and reason."""
        q = np.array(case["embedding"])
        out = []
        for c in self.cases.values():
            if c["decision"] is None or c["account"] == case["account"]:
                continue
            e = np.array(c["embedding"])
            sim = float(q @ e / (np.linalg.norm(q) * np.linalg.norm(e) + 1e-9))
            out.append({"case_id": c["id"], "similarity": round(sim, 3), "account": c["account"],
                        "pattern": c["pattern"], "human_decision": c["decision"]["decision"],
                        "reason": c["decision"]["reason"], "reviewer": c["decision"]["reviewer"]})
        return sorted(out, key=lambda x: -x["similarity"])[:k]

    def decide(self, case_id, decision, reason, reviewer):
        if decision not in ("confirm_fraud", "clear", "monitor"):
            raise ValueError("decision must be confirm_fraud, clear or monitor")
        compliance.require_reason_and_reviewer(reviewer, reason)
        compliance.block_auto_classification(decision, reviewer)
        case = self.cases[case_id]
        if case["decision"] is not None:
            return {**self.public(case), "duplicate": True}
        rec = compliance.mark_reportable({"type": "decision", "case_id": case_id, "account": case["account"],
                                          "by": "human", "human_decision": decision, "reason": reason,
                                          "reviewer": reviewer, "model_recommendation": case["recommendation"]})
        self.ledger.append(rec)
        case["decision"] = {"decision": decision, "reason": reason, "reviewer": reviewer,
                            "at": time.strftime("%Y-%m-%d %H:%M:%S"), "reportable": rec.get("reportable", False)}
        case["status"] = "resolved"
        if decision == "confirm_fraud":
            self.team.twin.freeze(case["account"], f"confirmed by {reviewer}: {reason}")
        return self.public(case)

    def ask(self, case_id, question):
        """Grounded copilot: answers ONLY from the case's verified evidence; never decides."""
        case = self.cases[case_id]
        brain = self.team.brain
        question = guard.normalize(question, guard.MAX_QUESTION)
        hits = guard.detect(question)
        if hits:
            guard.note("blocked_inputs", "case copilot", question, hits)
            return {"answer": guard.refusal(), "grounded_in": 0, "brain": "guard", "blocked": True}
        answer = None
        if brain.live:
            system = ("You are a fraud-analyst copilot helping a human reviewer with ONE case. Answer ONLY from the "
                      "verified evidence below. If the evidence does not cover the question, say you don't have that "
                      "information. Never invent accounts, amounts or facts, never recommend a final verdict: the "
                      "human decides. Amounts are Indian rupees. Be concise.\n\n"
                      f"CASE {case['id']} account {case['account']}\nRECOMMENDATION: {case['recommendation'].upper()} "
                      f"(pattern {case['pattern']}, risk {case['risk_score']})\nEVIDENCE:\n{evidence_text(case)}")
            try:
                answer = brain.run("copilot", system, {}, [{"role": "user", "content": question}], [], max_steps=1)
            except Exception:  # noqa: BLE001 - fall back to the deterministic answer
                answer = None
        if answer and not self._cites_only_case_ids(case, answer):
            guard.note("blocked_outputs", "case copilot", answer, ["cited_unknown_id"])
            answer = "I don't have that information in the verified evidence for this case."
        if not answer:
            answer = self._grounded_answer(case, question)
        case["chat"].append({"q": question, "a": answer, "brain": brain.label if brain.live else "deterministic"})
        return {"answer": answer, "grounded_in": len(case["reasoning_steps"]), "brain": case["chat"][-1]["brain"]}

    @staticmethod
    def _cites_only_case_ids(case, answer):
        known = case["account"] + " " + evidence_text(case) + " " + " ".join(p["case_id"] + " " + p["account"]
                                                                               for p in case.get("precedent", []))
        ids = re.findall(r"\b(?:A|D|IP|UPI|CARD|CT|T|ORD|EV|HOLD|ACT)-[0-9A-Fa-f]{3,}\b", answer)
        return all(i in known for i in ids)

    @staticmethod
    def _grounded_answer(case, question):
        q = question.lower()
        words = {w.strip("?.,!") for w in q.split() if len(w) > 3}
        hits = [s for s in case["reasoning_steps"]
                if words & {w.strip(".,():") for w in (s["claim"] + " " + s["source"]).lower().split()}]
        if any(w in q for w in ("summar", "overview", "why")) and not hits:
            return deterministic_summary(case)
        if not hits:
            return "I don't have that information in the verified evidence for this case."
        return " ".join(f"{s['claim']} (evidence: {', '.join(map(str, s['evidence'][:4]))}; source: {s['source']})"
                        for s in hits[:3])
