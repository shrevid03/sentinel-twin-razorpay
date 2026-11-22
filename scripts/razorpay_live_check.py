"""End-to-end check against the REAL Razorpay test-mode APIs (not the in-process demo gateway).

    .venv/bin/python scripts/razorpay_live_check.py

Needs RAZORPAY_KEY_ID=rzp_test_... and RAZORPAY_KEY_SECRET in .env. Refuses rzp_live_ keys outright (and ignores
RAZORPAY_ALLOW_LIVE), so it can never move real money. Nothing is printed from the keys.

What it does, each step with real Razorpay calls:
  1. create a Razorpay order (manual capture) through the agent's guarded checkout: mandate, risk decision, budget
     reservation;
  2. YOU pay once in real Razorpay Checkout, opened on a local page (http://127.0.0.1:<port>): choose UPI and enter
     success@razorpay (Razorpay's test UPI ID);
  3. verify the Checkout signature (HMAC) and that the payment belongs to the order;
  4. run the pre-capture risk check on the authorized payment;
  5. capture it with Razorpay, then SIMULATE A CRASH before anything is written to the database;
  6. "restart": a brand-new process state (new twin, new team, new database connection, same database);
  7. the reconciler asks Razorpay's real API what happened and records the capture exactly once;
  8. refund it through Razorpay's real Refunds API and record the refund;
  9. replay the pre-capture decision from the database after the restart.
A JSON report is written to data/razorpay_live_check.json (data/ is git-ignored).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["RAZORPAY_ALLOW_LIVE"] = "0"
os.environ.setdefault("AUTO_TRAFFIC", "0")

KEY = os.getenv("RAZORPAY_KEY_ID", "").strip()
if KEY.startswith("rzp_live_"):
    sys.exit("Refusing: RAZORPAY_KEY_ID is a LIVE key (rzp_live_). This check only runs with test keys.")
if not KEY.startswith("rzp_test_") or not os.getenv("RAZORPAY_KEY_SECRET", "").strip():
    sys.exit("Needs RAZORPAY_KEY_ID=rzp_test_... and RAZORPAY_KEY_SECRET in .env (Razorpay Dashboard, Test Mode, "
             "Account & Settings, API Keys).")

from app import payment_words as words  # noqa: E402
from app.agents import Team, WorkerCrash  # noqa: E402
from app.razorpay import Razorpay  # noqa: E402
from app.store import Store  # noqa: E402
from app.twin import Twin  # noqa: E402

CART = [{"sku": "SKU-COF-02", "qty": 1}]  # ₹2,699 pour-over set
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="rzp-live-check-"), "check.db")
report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "gateway": None, "steps": []}


def step(name, **kw):
    report["steps"].append({"step": name, **kw})
    print(f"  ✓ {name}" + (f": {json.dumps(kw, default=str)}" if kw else ""))


def process():
    t = Twin(seed=3)
    t.train(40)
    team = Team(t, Razorpay(), store=Store(DB))
    team.no_live = True
    return t, team


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Razorpay test checkout</title><body style="font-family:system-ui;padding:24px">
<h3>Sentinel Twin: real Razorpay test-mode checkout</h3>
<p>Order <code>%(order)s</code>, %(amount)s. Choose <b>UPI</b> and enter <b>success@razorpay</b>.</p>
<button id=pay style="padding:10px 18px">Pay with Razorpay (test mode)</button><p id=out></p>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
document.getElementById('pay').onclick = () => new Razorpay({key: %(key)s, order_id: %(order_js)s,
  name: 'Sentinel Twin test', description: 'Live-check order',
  handler: (r) => fetch('/done', {method: 'POST', body: JSON.stringify(r)}).then(() =>
    document.getElementById('out').textContent = 'Payment authorized. You can close this tab.'),
  modal: {ondismiss: () => document.getElementById('out').textContent = 'Closed before paying: nothing was captured.'}
}).open();
</script>"""


def wait_for_checkout(order_id, amount_inr):
    got = {}
    done = threading.Event()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = (PAGE % {"order": order_id, "amount": f"₹{amount_inr:,.2f}", "key": json.dumps(KEY),
                            "order_js": json.dumps(order_id)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            got.update(json.loads(self.rfile.read(n) or b"{}"))
            self.send_response(204)
            self.end_headers()
            done.set()

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"\n  → Open {url} and pay with UPI success@razorpay (waiting up to 10 minutes)\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    if not done.wait(600):
        srv.shutdown()
        sys.exit("No payment within 10 minutes. The order was never paid, so nothing was charged.")
    srv.shutdown()
    return got


def main():
    print("Sentinel Twin: real Razorpay test-mode check\n")
    t, team = process()
    assert team.pp.live and team.pp.mode == "test", "the Razorpay client is not in test mode"
    report["gateway"] = "Razorpay test-mode API (rzp_test_ key); NOT the demo gateway"
    step("gateway", mode=team.pp.mode)

    out = team.guarded_checkout(CART)
    if out.get("status") != "awaiting_buyer_approval":
        sys.exit(f"checkout did not create an order: {out.get('status')}")
    oid, rid = out["pay_order_id"], out["receipt_id"]
    order = team.pp.get_order(oid)
    step("1. Razorpay order created", order_id=oid, amount_paise=order.get("amount"), status=order.get("status"),
         pre_checkout_decision=team.receipts[rid]["risk"]["decision_id"])

    resp = wait_for_checkout(oid, out["total"])
    pid, sig = resp.get("razorpay_payment_id"), resp.get("razorpay_signature")
    if resp.get("razorpay_order_id") != oid or not pid:
        sys.exit("Checkout returned an unexpected response")
    step("2. paid in real Razorpay Checkout", payment_id=pid)

    ok = team.pp.verify_payment_signature(oid, pid, sig)
    step("3. Checkout signature verified (HMAC-SHA256 order_id|payment_id)", valid=ok)
    if not ok:
        sys.exit("signature verification failed")
    bad = team.pp.verify_payment_signature(oid, pid, "0" * 64)
    step("3b. a forged signature is rejected", valid=bad)

    team._fault = "crash_after_capture"
    try:
        team.capture(oid, pid, sig)
        sys.exit("expected the simulated crash")
    except WorkerCrash:
        pass
    pay = team.store.payment(oid)
    co = team.checkouts[oid]
    rzp = team.pp.get_payment(pid)
    step("4. pre-capture risk check", decision=co.get("pre_capture_decision"),
         outcome=t.decisions[co["pre_capture_decision"]]["outcome"])
    step("5. captured at Razorpay, then the worker crashed before the database write",
         razorpay_status=rzp.get("status"), our_db_state=pay["pay_state"], mandate_spent=team.mandate["spent"])
    del t, team  # the process is gone

    t2, team2 = process()  # restart: new twin, new team, new DB connection, same database
    before = team2.store.payment(oid)["pay_state"]
    rec = team2.reconcile()
    after = team2.store.payment(oid)
    m = team2.mandate
    step("6-7. restart + reconcile against Razorpay's real API", state_before=before, state_after=after["pay_state"],
         resolved=rec["resolved"], mandate_spent=m["spent"])
    again = team2.reconcile()
    step("7b. reconciling again changes nothing", spent=team2.mandate["spent"], resolved=again["resolved"])

    ref = team2.pp.refund_capture(pid, out["total"], "Sentinel Twin live check")
    team2.store.transition(oid, "refunded", f"refund {ref.get('id')}")
    rzp2 = team2.pp.get_payment(pid)
    step("8. refunded through Razorpay's Refunds API", refund_id=ref.get("id"), refund_status=ref.get("status"),
         razorpay_payment_status=rzp2.get("status"), amount_refunded=rzp2.get("amount_refunded"),
         our_db_state=team2.store.payment(oid)["pay_state"], mandate_spent=team2.mandate["spent"],
         customer_message=words.REFUND_INITIATED)

    view = team2.receipt_view(team2.receipts[rid])
    r = t2.replay(view["pre_capture"]["decision_id"]) if view.get("pre_capture") else None
    step("9. receipt and decision survive the restart", receipt=rid, payment_state=view["payment_status"]["state"],
         gateway_note=view["payment_status"]["gateway_note"],
         replay_identical=bool(r and r.get("identical")), replay_source=(r or {}).get("source"))
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["ok"] = (ok and not bad and after["pay_state"] == "captured" and m["spent"] == out["total"]
                    and rzp2.get("status") == "refunded" and bool(r and r.get("identical")))
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    path = os.path.join(ROOT, "data", "razorpay_live_check.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n{'PASSED' if report['ok'] else 'CHECK THE STEPS ABOVE'}; report written to {path}")


if __name__ == "__main__":
    main()
