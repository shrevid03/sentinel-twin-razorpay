"""scripts/razorpay_live_check.py, run against a local fake of the Razorpay API (orders, payments, capture, refund).

This only checks the script's own logic offline. The real proof is running the script with rzp_test_ keys."""
import hashlib
import hmac
import importlib.util
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = "fake_secret_dryrun"
orders, pays = {}, {}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _send(self, code, obj):
        b=json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p=self.path.split("?")[0].split("/")
        if p[2]=="orders": return self._send(200, orders[p[3]])
        if p[2]=="payments": return self._send(200, pays[p[3]])
        self._send(404,{})
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0)); body=json.loads(self.rfile.read(n) or b"{}")
        p=self.path.split("/")
        if p[2]=="orders":
            oid="order_FAKE"+uuid.uuid4().hex[:8]; orders[oid]={"id":oid,"amount":body["amount"],"currency":body["currency"],"status":"created"}; return self._send(200,orders[oid])
        if p[2]=="payments" and p[4]=="capture":
            pays[p[3]]["status"]="captured"; return self._send(200,pays[p[3]])
        if p[2]=="payments" and p[4]=="refund":
            pays[p[3]]["status"]="refunded"; pays[p[3]]["amount_refunded"]=body.get("amount",pays[p[3]]["amount"]); return self._send(200,{"id":"rfnd_FAKE1","status":"processed"})
        self._send(404,{})


def test_live_check_script_logic(monkeypatch, tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_FAKEDRYRUN")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", SECRET)
    monkeypatch.setenv("RAZORPAY_API_BASE", f"http://127.0.0.1:{srv.server_port}")
    spec = importlib.util.spec_from_file_location("lc", os.path.join(ROOT, "scripts", "razorpay_live_check.py"))
    lc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lc)

    def fake_checkout(oid, amt):
        pid = "pay_FAKE" + uuid.uuid4().hex[:8]
        pays[pid] = {"id": pid, "order_id": oid, "amount": orders[oid]["amount"], "currency": "INR",
                     "status": "authorized", "method": "upi", "vpa": "success@razorpay"}
        sig = hmac.new(SECRET.encode(), f"{oid}|{pid}".encode(), hashlib.sha256).hexdigest()
        return {"razorpay_order_id": oid, "razorpay_payment_id": pid, "razorpay_signature": sig}
    lc.wait_for_checkout = fake_checkout
    lc.ROOT = str(tmp_path)
    lc.main()
    srv.shutdown()
    rep = json.load(open(tmp_path / "data" / "razorpay_live_check.json"))
    assert rep["ok"], rep
    steps = {s["step"].split(".")[0]: s for s in rep["steps"]}
    assert steps["5"]["our_db_state"] == "capture_requested" and steps["5"]["razorpay_status"] == "captured"
    assert steps["6-7"]["state_after"] == "captured" and steps["6-7"]["mandate_spent"] == 2699.0
