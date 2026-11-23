"""Sentinel Twin API server (FastAPI). Run: uvicorn app.main:app --reload"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from . import integrations  # noqa: E402
from . import channel3  # noqa: E402
from .agents import LANGS, CaptureRefused, Team  # noqa: E402
from .drill import defense_drill  # noqa: E402
from .money import K, rs  # noqa: E402
from .razorpay import Razorpay, RazorpayError, payer_links  # noqa: E402
from .redteam import ATTACKS, run_attack  # noqa: E402
from .scenario import new_account_joins_ring  # noqa: E402
from . import guard  # noqa: E402
from .compliance import RULES, ComplianceError  # noqa: E402
from .twin import Twin, _public  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"

def _seeded_twin():
    """The seeded, trained twin. If a prebuilt copy exists (TWIN_PREBUILT, written at build time by
    scripts/prebuild_twin.py with the same seed and epochs) it is loaded in seconds instead of retrained."""
    pre = os.getenv("TWIN_PREBUILT", "").strip()
    if pre and Path(pre).exists():
        try:
            import pickle
            with open(pre, "rb") as f:
                t = pickle.load(f)
            if getattr(t, "prebuilt_for", None) == (int(os.getenv("TWIN_SEED", "3")), int(os.getenv("GAT_EPOCHS", "120"))):
                import threading as _th
                t.lock = _th.RLock()
                return t
        except Exception as e:  # noqa: BLE001 - a stale or broken file: train as usual
            print("prebuilt twin not used:", type(e).__name__)
    t = Twin(seed=int(os.getenv("TWIN_SEED", "3")))
    t.train(epochs=int(os.getenv("GAT_EPOCHS", "120")))
    return t


twin = _seeded_twin()
twin.on_event = integrations.publish
twin.seed_orders = len(twin.orders)
razorpay = Razorpay()
def _db_url():
    """DATABASE_URL (Postgres in production), else a SQLite file next to the app so payments survive a restart."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    d = Path(__file__).resolve().parent.parent / "data"
    d.mkdir(exist_ok=True)
    return f"sqlite:///{d / 'sentinel.db'}"


from .store import Store  # noqa: E402

store = Store(_db_url())
team = Team(twin, razorpay, store=store)
team.reconcile()  # startup: resolve captures that were in flight when the last process stopped
if getattr(twin, "prebuilt_for", None) and twin.audit is not None:  # a loaded model is stored like a trained one
    try:
        twin.audit.save_model(twin.model_version, twin.model_versions[twin.model_version])
    except Exception as e:  # noqa: BLE001 - replay then reports the model as missing rather than crashing
        print("model artifact not stored:", type(e).__name__)
# The fraud-ring scenario runs on a copy of the twin as it was seeded and trained, so its numbers are reproducible
# (the live twin keeps growing under simulated traffic and is retrained as it grows).
SCENARIO_BASE = {"t": twin.fork(durable=True)}


def require_admin(x_admin_token: str | None = Header(default=None)):
    """If ADMIN_TOKEN is set (public deploys), admin actions need the X-Admin-Token header. Unset = open (local)."""
    tok = os.getenv("ADMIN_TOKEN", "").strip()
    if tok and not hmac.compare_digest((x_admin_token or "").encode(), tok.encode()):
        raise HTTPException(401, "admin token required: send the X-Admin-Token header")


ADMIN = [Depends(require_admin)]


def rate(bucket, per_minute):
    """Per-client sliding-window rate limit (RATE_LIMIT=0 disables it). Behind Render's proxy the client is the first
    X-Forwarded-For address."""
    def dep(request: Request):
        if os.getenv("RATE_LIMIT", "1") == "0":
            return
        fwd = request.headers.get("x-forwarded-for", "")
        client = fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")
        if not guard.LIMITER.allow(client, bucket, per_minute):
            raise HTTPException(429, f"too many requests to {bucket}: limit {per_minute} per minute, try again shortly")
    return [Depends(dep)]
_reset_lock = threading.Lock()
auto = {"on": os.getenv("AUTO_TRAFFIC", "1") == "1", "n": 3,
        # optional cap on simulated live orders per process (small free servers: memory grows with the twin)
        "max": int(os.getenv("AUTO_TRAFFIC_MAX", "0") or 0)}

app = FastAPI(title="Sentinel Twin API", version="1.0.0",
              description="Digital twin + GAT risk engine + agent team on Razorpay (Orders, Checkout, webhooks).")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _traffic():
    ticks = 0
    while True:
        time.sleep(5)
        ticks += 1
        if ticks % 12 == 0:  # about once a minute: resolve in-flight captures, release abandoned reservations
            try:
                team.reconcile()
                twin.flush_events()  # keep the durable graph event log current between decisions
            except Exception as e:  # noqa: BLE001 - keep the loop alive; the next pass retries
                print("reconcile error", type(e).__name__)
        if auto["on"] and not (auto["max"] and len(twin.orders) - getattr(twin, "seed_orders", 0) >= auto["max"]):
            try:
                twin.tick(auto["n"], fraud_rate=0.12)
            except Exception as e:  # keep the simulator alive
                print("tick error", e)
            if twin.needs_retrain():  # keep the GAT current as the twin grows (about every 1,000 live orders)
                try:
                    m = twin.train(epochs=int(os.getenv("GAT_EPOCHS", "120")))
                    twin._event("model", f"GAT retrained on the grown twin ({m['graph']['nodes']} nodes): test AUC {m['test_auc']}", level="ok")
                except Exception as e:  # noqa: BLE001 - keep the loop alive
                    print("retrain error", type(e).__name__)


threading.Thread(target=_traffic, daemon=True).start()


@app.get("/", include_in_schema=False)
def landing():
    return FileResponse(STATIC / "landing.html")


@app.get("/app", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz", include_in_schema=False)
def health():
    return {"ok": True}


# ------------------------------------------------------------------ config
@app.get("/api/config")
def config():
    # the key id is public by design (Razorpay Checkout needs it in the browser); the secret never leaves the server
    return {"pay_mode": razorpay.mode, "razorpay_key_id": razorpay.key_id if razorpay.live else "",
            "currency": razorpay.currency, "fx_rate": razorpay.fx, "live_key_refused": razorpay.refused_live_key,
            "brain": team.brain.label, "model": team.brain.model,
            "languages": LANGS, "integrations": {**integrations.status, "channel3": channel3.enabled()}, "auto_traffic": auto["on"],
            "buyer": team.buyer, "attacks": ATTACKS}


@app.get("/api/stats")
def stats():
    return twin.stats()


@app.get("/api/graph")
def graph(focus: str | None = None):
    return twin.graph_view(focus)


@app.get("/api/events")
def events(since: int = 0):
    return [e for e in twin.events if e["seq"] > since][-80:]


@app.get("/api/orders")
def orders(limit: int = 600):
    out = []
    for o in twin.orders[-limit:][::-1]:
        p = _public(o)
        p["buyer"] = twin.accounts[o["account"]].name
        p["merchant_name"] = twin.merchants[o["merchant"]]["name"]
        p["factors"] = "; ".join(o.get("factors") or [])
        out.append(p)
    return out


@app.get("/api/accounts")
def accounts():
    twin.refresh()
    by = {}
    for o in twin.orders:
        by.setdefault(o["account"], []).append(o["amount"])
    return [{"id": a.id, "name": a.name, "country": a.country, "age_days": a.age_days, "kyc": a.kyc,
             "email_domain": a.email.split("@")[1], "devices": len(a.devices), "ips": len(a.ips),
             "orders": len(by.get(a.id, [])), "spend": round(sum(by.get(a.id, [])), 2),
             "risk": round(twin.risk(a.id), 3), "status": a.status} for a in twin.accounts.values()]


@app.get("/api/account/{account_id}")
def account(account_id: str):
    if account_id not in twin.accounts:
        raise HTTPException(404, "unknown account")
    return {**twin.account_view(account_id), "linked": twin.linked_accounts(account_id)[:20]}


@app.get("/api/explain/{node_id}")
def explain(node_id: str):
    try:
        return twin.explain(node_id)
    except KeyError:
        raise HTTPException(404, "unknown node")


@app.get("/api/model")
def model():
    return twin.metrics


@app.post("/api/model/retrain", dependencies=ADMIN)
def retrain():
    m = twin.train(epochs=int(os.getenv("GAT_EPOCHS", "120")))
    twin._event("model", f"GAT retrained on {m['graph']['nodes']} nodes: test AUC {m['test_auc']}", level="ok")
    return m


class SimIn(BaseModel):
    n: int = 10
    fraud_rate: float = 0.15
    auto: bool | None = None


@app.post("/api/simulate")
def simulate(body: SimIn):
    if body.auto is not None:
        auto["on"] = body.auto
        return {"auto_traffic": auto["on"]}
    res = twin.tick(min(body.n, 100), body.fraud_rate)
    return {"orders": [_public(o) for o in res]}


# ------------------------------------------------------------------ agents
class ChatIn(BaseModel):
    text: str
    session: str = "default"
    lang: str = "en-IN"


@app.post("/api/chat", dependencies=rate("chat", 30))
def chat(body: ChatIn):
    try:
        team.last_live = []
        try:
            res = team.concierge(body.session, body.text, body.lang)
        except Exception:  # never show a raw error: retry once on the demo catalog only
            team.last_live, team.no_live = [], True
            try:
                res = team.concierge(body.session, body.text, body.lang)
            finally:
                team.no_live = False
        res["products"] = [{k: p.get(k) for k in ("sku", "name", "price", "merchant", "image", "retailer_url", "brand", "category")}
                           for p in getattr(team, "last_live", [])]
        return res
    except Exception as e:  # last resort: a friendly reply instead of a raw 500
        print("chat error:", type(e).__name__, e)
        return {"trace": [], "checkout": None, "case": None, "redteam": None, "products": [],
                "reply": "Sorry, I hit a snag talking to the shop. Please try that again."}


class InvIn(BaseModel):
    account: str | None = None
    lang: str = "en-IN"


@app.post("/api/investigate", dependencies=rate("investigate", 10))
def investigate(body: InvIn):
    trace = []
    case = team.investigate(body.account, trace, body.lang)
    return {"case": case, "trace": trace}


@app.get("/api/cases")
def cases():
    return team.cases[::-1]


class RTIn(BaseModel):
    focus: str | None = None
    lang: str = "en-IN"


@app.post("/api/redteam", dependencies=rate("redteam", 6))
def redteam(body: RTIn):
    trace = []
    run = team.red_team(trace, body.lang, body.focus)
    return {"run": run, "trace": trace}


class AttackIn(BaseModel):
    attack: str
    params: dict = {}


@app.post("/api/redteam/attack", dependencies=rate("attack", 20))
def attack(body: AttackIn):
    trace = []
    return {"result": run_attack(twin, body.attack, body.params, shopper_guard=lambda: team.injection_probe(trace)),
            "trace": trace}


class DrillIn(BaseModel):
    seed: int = 7
    apply: bool = False


LAST_DRILL: dict = {}
if getattr(twin, "prebuilt_drill", None):  # the seeded drill was run at build time (scripts/prebuild_twin.py)
    LAST_DRILL.update(twin.prebuilt_drill)
    team.last_attack_orders = [o for r in LAST_DRILL.get("probes", []) + LAST_DRILL.get("adapted", []) for o in r["orders"]]


@app.post("/api/drill", dependencies=rate("drill", 6))
def drill(body: DrillIn, x_admin_token: str | None = Header(default=None)):
    """One-click defense drill: attack, search for a safer policy, replay the same attack, optionally apply."""
    if body.apply:  # applying changes the live policy: an admin action
        require_admin(x_admin_token)
    res = defense_drill(twin, seed=body.seed, apply=body.apply)
    team.last_attack_orders = [o for r in res["probes"] + res["adapted"] for o in r["orders"]]
    LAST_DRILL.clear()
    LAST_DRILL.update(res)
    return res


@app.get("/api/drill/latest")
def drill_latest():
    """Compact summary of the most recent defense drill for the command-center strip (null if none yet)."""
    if not LAST_DRILL:
        return None
    d = LAST_DRILL
    attacks = d.get("probes", []) + d.get("adapted", [])
    search = next((l.get("text", "") for l in d.get("log", []) if l.get("step") == "search"), "")
    return {
        "attacks": len(attacks),
        "attempted_inr": round(sum(r.get("fraud_inr", 0) for r in attacks), 2),
        "search": search,
        "chosen": bool(d.get("chosen")),
        "before_policy": d.get("before_policy"),
        "after_policy": d.get("after_policy"),
        "summary": d.get("summary", {}),
        "source": d.get("source"),
    }


@app.post("/api/tour/checkout", dependencies=rate("tour", 10))
def tour_checkout():
    """The guided tour's fixed purchase, with no language model in the loop: the agent's normal checkout path
    (mandate checked in code, risk gate, order with manual capture) for one ₹2,699 coffee set."""
    out = team.guarded_checkout([{"sku": "SKU-COF-02", "qty": 1}], "guided tour")
    return {"result": out, "checkout": team.checkouts.get(out.get("pay_order_id")) if out.get("pay_order_id") else None,
            "receipt_id": out.get("receipt_id")}


@app.post("/api/scenario/new-account", dependencies=rate("heavy", 8))
def scenario_new_account():
    """A clean new account shares a UPI ID with a known ring, then the agent checks out (on a fork; nothing live)."""
    return new_account_joins_ring(twin, base=SCENARIO_BASE["t"])


# On a slow free server the scenario takes about a minute, so it can be run once in the background right after the
# server starts (SCENARIO_WARM=1); the guided tour then shows that real run at once, labelled as such. It is the same
# seeded run every time, its decisions replay normally, and "Run scenario" still repeats it live.
SCENARIO_CACHE: dict = {}


def _warm_scenario():
    try:
        r = new_account_joins_ring(twin, base=SCENARIO_BASE["t"])
        r["source"] = "run when this server started, on this seeded twin"
        SCENARIO_CACHE["r"] = r
    except Exception as e:  # noqa: BLE001 - the tour then runs it live
        print("scenario warm-up failed:", type(e).__name__)


if os.getenv("SCENARIO_WARM", "0") == "1":
    threading.Thread(target=_warm_scenario, daemon=True).start()


@app.get("/api/scenario/new-account/latest")
def scenario_latest():
    """The scenario run made when this server started (null until it is ready, or if warm-up is off)."""
    return SCENARIO_CACHE.get("r")


@app.get("/api/guard")
def guard_stats():
    """Prompt-injection guard counters: blocked inputs, scrubbed tool outputs, blocked replies, refused tool calls."""
    return guard.STATS.view()


@app.post("/api/redteam/chatbot-injection", dependencies=rate("model-redteam", 4))
def chatbot_injection(live: bool = False):
    """Throw the injection corpus at the agents on a throwaway copy and report what was blocked or got through.
    Uses the built-in demo brain (seconds); live=true runs it against the configured LLM (slower, uses quota)."""
    from .injection import run

    def factory():
        t = Team(twin.fork(), Razorpay())
        t.no_live = True
        if not live:
            t.brain.client, t.brain.provider = None, "demo"
        return t
    return run(factory)


@app.post("/api/model-redteam", dependencies=rate("model-redteam", 4))
def model_redteam():
    """Attack the detector itself (drop ring edges + cover edges), harden it, and run RED/BLUE rounds (on a copy)."""
    from .model_redteam import run_on_twin
    return run_on_twin(twin)


@app.get("/api/compare")
def compare_get():
    """Rules only vs GAT + rules vs GAT + rules hardened, on the latest drill's orders (runs a drill if none yet)."""
    if not LAST_DRILL:
        drill(DrillIn(), None)
    return LAST_DRILL["compare"]


@app.post("/api/compare", dependencies=rate("heavy", 8))
def compare_post(body: DrillIn):
    """Run a fresh drill (never applied) and return its rules-vs-graph comparison."""
    return drill(DrillIn(seed=body.seed, apply=False), None)["compare"]


@app.post("/api/reset", dependencies=ADMIN)
def reset():
    """Rebuild the demo from TWIN_SEED: fresh twin and GAT, default policy, empty mandate spend, holds,
    checkouts, receipts, proposed actions, audit trail, cases and red-team runs."""
    global twin, team
    with _reset_lock:
        t = _seeded_twin()  # the prebuilt copy when there is one (seconds, not minutes, on a small server)
        t.on_event = integrations.publish
        t.seed_orders = len(t.orders)
        razorpay.calls.clear()
        store.reset()
        twin, team = t, Team(t, razorpay, store=store, load_receipts=False)  # audit rows stay in the database
        SCENARIO_BASE["t"] = twin.fork(durable=True)
        SCENARIO_CACHE.clear()
        LAST_DRILL.clear()
        twin._event("system", "Demo reset: fresh twin, default policy, empty queues", level="info")
    return {"ok": True, "seed": int(os.getenv("TWIN_SEED", "3")), "policy": twin.policy}


@app.get("/api/redteam/runs")
def runs():
    return team.redteam_runs[::-1]


class PolicyIn(BaseModel):
    review_threshold: float = 0.5
    block_threshold: float = 0.8
    max_auto_amount: float = 1000 * K  # rupees
    review_new_device_over: float = 300 * K
    review_spend_multiple: float = 0


@app.post("/api/whatif")
def whatif(body: PolicyIn, include_redteam: bool = True):
    return twin.what_if(body.model_dump(), extra_orders=team.last_attack_orders if include_redteam else None)


@app.post("/api/policy", dependencies=ADMIN)
def set_policy(body: PolicyIn):
    twin.policy = body.model_dump()
    twin._event("policy", f"Policy updated: review>={body.review_threshold}, block>={body.block_threshold}, "
                          f"auto-approve <= {rs(body.max_auto_amount)}", level="warn")
    return twin.policy


class MandateIn(BaseModel):
    per_order_limit: float
    monthly_budget: float
    blocked_categories: list[str] = []


@app.get("/api/mandate")
def get_mandate():
    return team.mandate


@app.post("/api/mandate")
def set_mandate(body: MandateIn):
    return team.set_mandate(**body.model_dump())


# ---------------------------------------------------------------- razorpay
class CheckoutResult(BaseModel):
    """What Razorpay Checkout's success handler returns. Empty in demo mode (the server simulates Checkout)."""
    razorpay_payment_id: str | None = None
    razorpay_order_id: str | None = None
    razorpay_signature: str | None = None


@app.post("/api/razorpay/orders/{order_id}/capture")
def capture(order_id: str, body: CheckoutResult | None = None):
    body = body or CheckoutResult()
    if body.razorpay_order_id and body.razorpay_order_id != order_id:
        raise HTTPException(400, "razorpay_order_id does not match the order being captured")
    try:
        res = team.capture(order_id, body.razorpay_payment_id, body.razorpay_signature)
    except CaptureRefused as e:
        raise HTTPException(e.status, e.detail)
    except RazorpayError as e:
        status = 400 if "signature" in str(e) or "does not belong" in str(e) else 502
        raise HTTPException(status, str(e))
    return {"status": res.get("status"), "id": res.get("id"), "payment_id": res.get("payment_id"),
            "mode": razorpay.mode,
            "duplicate": bool(res.get("duplicate"))}


# ------------------------------------------------------------ human review
class ReviewIn(BaseModel):
    reviewer: str = "merchant"
    note: str = ""


@app.get("/api/actions")
def actions():
    """The review queue: investigator proposals, held agent checkouts, and the audit trail."""
    rt = team.reviewer_twin
    acts = [{**a, "twin": rt.predict("action", a)} if a["status"] == "pending" else a for a in team.actions.values()]
    holds = [{**h, "twin": rt.predict("hold", h)} if h["status"] == "pending" else h for h in team.holds.values()]
    return {"actions": acts[::-1], "holds": holds[::-1], "audit": team.audit[::-1],
            "reviewer_twin": {"label": rt.status()["label"], "agreement": rt.agreement(),
                              "mode": "autopilot" if rt.autopilot["enabled"] else "shadow"}}


class AutopilotIn(BaseModel):
    enabled: bool | None = None
    admin: str | None = Field(None, max_length=80)
    reason: str | None = Field(None, max_length=300)
    max_inr: float | None = None
    min_confidence: float | None = None


# ------------------------------------------------- evidence cases + compliance
class DecideIn(BaseModel):
    decision: str
    reason: str = ""
    reviewer: str = ""


class AskIn(BaseModel):
    question: str


@app.post("/api/evidence/{account_id}", dependencies=rate("evidence", 30))
def open_evidence_case(account_id: str):
    """Model triage + deterministic graph evidence (claim, evidence, source) + precedent. Recorded in the ledger."""
    return _review(team.casebook.open_case, account_id)


@app.get("/api/evidence")
def evidence_cases():
    cb = team.casebook
    return {"cases": [cb.public(c) for c in cb.cases.values()][::-1], "ledger": cb.ledger.verify(),
            "metrics": cb.ledger.metrics()}


@app.post("/api/evidence/{case_id}/decide", dependencies=ADMIN)
def decide_evidence_case(case_id: str, body: DecideIn):
    """The human's decision: confirm_fraud, clear or monitor. Reason and reviewer are required (RBI-ACC-03)."""
    return _review(team.casebook.decide, case_id, body.decision, body.reason, body.reviewer)


@app.post("/api/evidence/{case_id}/ask", dependencies=rate("chat", 30))
def ask_copilot(case_id: str, body: AskIn):
    """Grounded copilot: answers only from the case's verified evidence and never decides."""
    return _review(team.casebook.ask, case_id, body.question[:500])


@app.get("/api/timeline/{account_id}")
def timeline(account_id: str):
    """An account's recent events in order (and, with TWIN_SEQ=1, the sequence transformer's attention on each)."""
    return _review(twin.timeline, account_id)


@app.get("/api/decisions/{decision_id}")
def decision(decision_id: str):
    """A stored risk decision: decision point, time, evidence (with when and where each item came from), versions."""
    def get(d):
        if d in twin.decisions:
            return twin.decisions[d]
        for prefix, fork in getattr(twin, "replay_forks", {}).items():
            if d.startswith(prefix) and d in fork.decisions:
                return fork.decisions[d]
        rec = twin.audit.decision(d) if twin.audit is not None else None  # made before a restart
        if rec is None:
            raise KeyError(d)
        return rec
    return _review(get, decision_id)


@app.get("/api/audit/status")
def audit_status():
    """What survives a restart: stored decisions, graph events, model artifacts, receipts and ledger rows."""
    if twin.audit is None:
        return {"durable": False}
    twin.flush_events()
    return {"durable": True, "database": store.engine.dialect.name, "epoch": twin.epoch,
            "counts": twin.audit.counts(), "ledger": team.ledger.verify()}


@app.post("/api/decisions/{decision_id}/replay")
def replay_decision(decision_id: str):
    """Rebuild the graph as it was at decision time, re-run the stored model and policy, compare with what was stored."""
    return _review(twin.replay, decision_id)


@app.get("/api/ledger")
def ledger(limit: int = 200):
    """The append-only, hash-chained ledger: model recommendations and human decisions as separate records."""
    lg = team.ledger
    return {"verify": lg.verify(), "metrics": lg.metrics(), "records": lg.records()[-limit:][::-1], "rules": RULES}


@app.get("/api/reviewer-twin")
def reviewer_twin():
    """The reviewer twin: what it was trained on, agreement with the human, model weights, autopilot state."""
    return team.reviewer_twin.status()


@app.post("/api/reviewer-twin/replay", dependencies=ADMIN + rate("replay", 6))
def reviewer_twin_replay():
    """Demo: a simulated reviewer works 30 synthetic cases; the twin predicts each before it is decided."""
    return team.reviewer_twin.replay(30)


@app.post("/api/reviewer-twin/autopilot", dependencies=ADMIN)
def reviewer_twin_autopilot(body: AutopilotIn):
    """Off by default. Enabling needs a named admin and a reason (written to the ledger). It only ever releases held
    checkouts under the cap at confidence >= 0.95; it never captures, refunds, restricts or moves money."""
    try:
        return team.reviewer_twin.set_autopilot(body.enabled, body.max_inr, body.min_confidence,
                                                admin=body.admin, reason=body.reason)
    except PermissionError as e:
        raise HTTPException(403, str(e))


@app.post("/api/reviewer-twin/undo/{hold_id}", dependencies=ADMIN)
def reviewer_twin_undo(hold_id: str):
    return _review(team.reviewer_twin.undo, hold_id)


def _review(fn, item_id, *args):
    try:
        return fn(item_id, *args)
    except KeyError:
        raise HTTPException(404, f"unknown id {item_id}")
    except CaptureRefused as e:
        raise HTTPException(e.status, e.detail)
    except ComplianceError as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/actions/{action_id}/approve", dependencies=ADMIN)
def approve_action(action_id: str, body: ReviewIn):
    return _review(team.decide_action, action_id, True, body.reviewer, body.note)


@app.post("/api/actions/{action_id}/reject", dependencies=ADMIN)
def reject_action(action_id: str, body: ReviewIn):
    return _review(team.decide_action, action_id, False, body.reviewer, body.note)


@app.post("/api/holds/{hold_id}/release", dependencies=ADMIN)
def release_hold(hold_id: str, body: ReviewIn):
    return _review(team.decide_hold, hold_id, True, body.reviewer, body.note)


@app.post("/api/holds/{hold_id}/reject", dependencies=ADMIN)
def reject_hold(hold_id: str, body: ReviewIn):
    return _review(team.decide_hold, hold_id, False, body.reviewer, body.note)


@app.get("/api/receipts")
def receipts():
    return [team.receipt_view(r) for r in list(team.receipts.values())[::-1]]


@app.get("/api/receipts/{receipt_id}")
def receipt(receipt_id: str):
    """A decision receipt by receipt id, Razorpay order id or hold id."""
    r = team.receipts.get(receipt_id)
    if not r:
        co = team.checkouts.get(receipt_id) or team.holds.get(receipt_id)
        r = team.receipts.get((co or {}).get("receipt_id"))
    if not r and team.audit_store is not None:  # from before a demo reset: still in the database
        r = team.audit_store.receipt(receipt_id)
    if not r:
        raise HTTPException(404, "unknown receipt")
    return team.receipt_view(r)


@app.get("/api/razorpay/calls")
def razorpay_calls():
    return razorpay.calls[::-1]


@app.get("/api/checkouts")
def checkouts():
    return list(team.checkouts.values())[::-1]


@app.post("/api/razorpay/webhook")
async def webhook(request: Request, bg: BackgroundTasks):
    raw = await request.body()  # the signature covers the exact raw bytes, so verify before parsing
    verified = razorpay.verify_webhook(raw, request.headers.get("x-razorpay-signature"))
    try:
        event = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(event, dict) or not isinstance(event.get("payload", {}) or {}, dict):
        raise HTTPException(400, "webhook body must be a Razorpay event object")
    etype = str(event.get("event", "unknown"))[:80]
    payload = event.get("payload", {}) or {}
    ent = lambda k: ((payload.get(k) or {}).get("entity") or {})  # noqa: E731
    # Only a Razorpay-signed webhook may change state. ALLOW_UNVERIFIED_WEBHOOKS=1 is a local testing switch
    # that works only in demo mode (no Razorpay keys), so a live deployment can never be driven by forged events.
    trusted = verified or (not razorpay.live and os.getenv("ALLOW_UNVERIFIED_WEBHOOKS", "0") == "1")
    if not trusted:
        twin._event("razorpay", f"Webhook {etype} ignored: signature not verified", level="warn")
        return {"received": True, "verified": False, "processed": False}
    # 1) dedupe deliveries by event id, in the database (survives restarts). 2) The business action is idempotent
    # anyway: every effect is a forward-only state transition, so a duplicate, stale or out-of-order event is a no-op.
    eid = request.headers.get("x-razorpay-event-id") or hashlib.sha256(raw).hexdigest()
    if not store.first_delivery(eid, etype):
        return {"received": True, "verified": verified, "processed": False, "duplicate": True}
    twin._event("razorpay", f"Webhook {etype} ({'verified' if verified else 'unverified, demo testing'})",
                level="bad" if etype.startswith("payment.dispute") else "info")
    pay_ent, ref_ent = ent("payment"), ent("refund")
    oid = pay_ent.get("order_id")
    if not oid and (ref_ent.get("payment_id") or pay_ent.get("id")):
        row = store.payment_by_payment_id(ref_ent.get("payment_id") or pay_ent.get("id"))
        oid = row["order_id"] if row else None
    result = None
    if oid and store.payment(oid):
        if etype == "payment.authorized":
            result = store.transition(oid, "authorized", "webhook payment.authorized", pay_ent.get("id"))
        elif etype == "payment.captured":
            result = team._record_capture(oid, pay_ent.get("id"), "webhook payment.captured")
            o = next((x for x in twin.orders if x.get("pay_order_id") == oid), None)
            if o:  # mirror the payer's identifiers (hashed) onto the account behind the order
                twin.link_payer(o["account"], payer_links(pay_ent), source="webhook payment.captured")
        elif etype == "payment.failed":
            result = store.transition(oid, "failed", "webhook payment.failed")
        elif etype in ("refund.processed", "payment.refunded"):
            result = store.transition(oid, "refunded", f"webhook {etype}")
    if etype.startswith("payment.dispute.created"):
        # a chargeback is ground truth arriving from Razorpay: label the order and open an investigation
        pid = ent("dispute").get("payment_id") or pay_ent.get("id")
        for o in twin.orders:
            if pid and o.get("capture_id") == pid:
                o["fraud"] = True
                bg.add_task(team.investigate, o["account"])
    if etype in ("refund.processed", "payment.refunded"):
        pid = ref_ent.get("payment_id") or pay_ent.get("id")
        for o in twin.orders:
            if pid and o.get("capture_id") == pid:
                o["status"] = "refunded"
    if etype == "payment.failed":
        for o in twin.orders:
            if oid and o.get("pay_order_id") == oid and o["status"] == "awaiting_payment":
                o["status"] = "payment_failed"
    if result is not None:
        return {"received": True, "verified": verified, "processed": True,
                "state": result.get("state"), "applied": result.get("applied")}
    return {"received": True, "verified": verified, "processed": True}
