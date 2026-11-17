"""Razorpay client for test mode: Orders API, Standard Checkout signature check, manual capture, refunds, webhooks.

Flow: the agent creates a Razorpay order server-side (manual capture), the buyer pays in Razorpay Checkout,
Checkout hands back razorpay_payment_id + razorpay_order_id + razorpay_signature, the server verifies that
signature (HMAC-SHA256 of "order_id|payment_id" with the key secret), re-checks the buyer's mandate and only
then captures the authorized payment. Webhooks are trusted only when X-Razorpay-Signature matches
HMAC-SHA256(raw body, webhook secret).

If RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set the client runs in a clearly labelled demo mode that
returns the same shapes, so the app can be explored without keys. Live keys (rzp_live_) are refused unless
RAZORPAY_ALLOW_LIVE=1, so a demo deploy can never take real money by accident.

Amounts: everything in Sentinel Twin is priced in rupees (INR), so orders are charged in INR as-is
(RAZORPAY_FX_RATE defaults to 1). To charge another currency enabled on your account, set RAZORPAY_CURRENCY and
RAZORPAY_FX_RATE (units of that currency per rupee).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid

import httpx

API = "https://api.razorpay.com"


class RazorpayError(Exception):
    pass


def _sig(secret: str, message: bytes | str) -> str:
    if isinstance(message, str):
        message = message.encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class Razorpay:
    def __init__(self):
        self.key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
        self.key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
        self.webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
        self.base = os.getenv("RAZORPAY_API_BASE", API)
        self.currency = (os.getenv("RAZORPAY_CURRENCY", "INR").strip() or "INR").upper()
        self.fx = float(os.getenv("RAZORPAY_FX_RATE", "1") or 1)
        is_live_key = self.key_id.startswith("rzp_live_")
        allow_live = os.getenv("RAZORPAY_ALLOW_LIVE", "0") == "1"
        self.refused_live_key = is_live_key and not allow_live
        self.live = bool(self.key_id and self.key_secret) and not self.refused_live_key
        self._demo_orders: dict[str, dict] = {}
        self._demo_payments: dict[str, dict] = {}
        self.calls: list[dict] = []  # recent API calls, shown in the UI

    @property
    def mode(self):
        if not self.live:
            return "demo"
        return "live" if self.key_id.startswith("rzp_live_") else "test"

    # ------------------------------------------------------------ plumbing
    def to_minor(self, inr: float) -> int:
        """Rupee price -> smallest unit of the charge currency (paise for INR)."""
        return int(round(float(inr) * self.fx * 100))

    def _req(self, method, path, json=None):
        r = httpx.request(method, f"{self.base}{path}", json=json, auth=(self.key_id, self.key_secret), timeout=30)
        self._log(method, path, r.status_code)
        if r.status_code >= 400:
            raise RazorpayError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def _log(self, method, path, status):
        self.calls.append({"t": time.strftime("%H:%M:%S"), "method": method, "path": path, "status": status,
                           "mode": self.mode})
        self.calls = self.calls[-50:]

    # -------------------------------------------------------------- orders
    def create_order(self, items, reference, description="Sentinel Twin agent checkout"):
        total_inr = round(sum(i["price"] * i.get("qty", 1) for i in items), 2)
        body = {
            "amount": self.to_minor(total_inr),
            "currency": self.currency,
            "receipt": reference[:40],
            "notes": {"source": "sentinel-twin agent", "description": description[:200],
                      "total_inr": f"{total_inr:.2f}",
                      "items": "; ".join(f"{i.get('qty', 1)}x {i.get('sku') or i['name']}" for i in items)[:250]},
            # manual capture: the server re-checks the mandate before any money moves
            "payment": {"capture": "manual",
                        "capture_options": {"manual_expiry_period": 7200, "refund_speed": "normal"}},
        }
        if not self.live:
            oid = "order_DEMO" + uuid.uuid4().hex[:10].upper()
            order = {"id": oid, "entity": "order", "amount": body["amount"], "amount_paid": 0,
                     "currency": self.currency, "receipt": body["receipt"], "status": "created",
                     "notes": body["notes"], "created_at": int(time.time())}
            self._demo_orders[oid] = order
            self._log("POST", "/v1/orders", 200)
            return order
        try:
            return self._req("POST", "/v1/orders", body)
        except RazorpayError as e:
            # accounts without per-order capture settings reject the "payment" block: fall back to the
            # account's default capture setting (capture() still verifies and re-checks before capturing)
            if " 400:" not in str(e):
                raise
            body.pop("payment")
            return self._req("POST", "/v1/orders", body)

    def get_order(self, order_id):
        if not self.live:
            self._log("GET", f"/v1/orders/{order_id}", 200)
            o = self._demo_orders.get(order_id)
            if not o:
                raise RazorpayError(f"order {order_id} not found")
            return o
        return self._req("GET", f"/v1/orders/{order_id}")

    # ------------------------------------------------------------ payments
    def verify_payment_signature(self, order_id, payment_id, signature) -> bool:
        """Standard Checkout success handler check: HMAC-SHA256("order_id|payment_id", key_secret)."""
        if not (self.key_secret and order_id and payment_id and signature):
            return False
        return hmac.compare_digest(_sig(self.key_secret, f"{order_id}|{payment_id}"), str(signature))

    def demo_pay(self, order_id):
        """Demo mode stand-in for Razorpay Checkout: an authorized payment, as Checkout would return it."""
        o = self.get_order(order_id)
        pid = "pay_DEMO" + uuid.uuid4().hex[:10].upper()
        self._demo_payments[pid] = {"id": pid, "entity": "payment", "order_id": order_id, "amount": o["amount"],
                                    "currency": o["currency"], "status": "authorized", "method": "upi"}
        return {"razorpay_order_id": order_id, "razorpay_payment_id": pid, "razorpay_signature": "demo"}

    def capture_order(self, order_id, payment_id, signature):
        """Verify the Checkout signature, check the payment belongs to this order, then capture it once."""
        if self.live:
            if not self.verify_payment_signature(order_id, payment_id, signature):
                raise RazorpayError("payment signature verification failed")
            p = self._req("GET", f"/v1/payments/{payment_id}")
        else:
            p = self._demo_payments.get(payment_id)
            if not p:
                raise RazorpayError(f"payment {payment_id} not found")
            self._log("GET", f"/v1/payments/{payment_id}", 200)
        if p.get("order_id") != order_id:
            raise RazorpayError(f"payment {payment_id} does not belong to order {order_id}")
        if p.get("status") == "authorized":
            body = {"amount": p["amount"], "currency": p["currency"]}
            if self.live:
                p = self._req("POST", f"/v1/payments/{payment_id}/capture", body)
            else:
                p["status"] = "captured"
                self._log("POST", f"/v1/payments/{payment_id}/capture", 200)
        status = "COMPLETED" if p.get("status") == "captured" else str(p.get("status", "unknown")).upper()
        return {"id": order_id, "status": status, "payment_id": payment_id, "capture_id": payment_id,
                "amount": p.get("amount"), "currency": p.get("currency"), "method": p.get("method")}

    def refund_capture(self, payment_id, amount_inr=None, note="Refund issued by Sentinel Twin investigator"):
        body = {"notes": {"reason": note[:250]}, "speed": "normal"}
        if amount_inr:
            body["amount"] = self.to_minor(amount_inr)
        if not self.live:
            self._log("POST", f"/v1/payments/{payment_id}/refund", 200)
            return {"id": "rfnd_DEMO" + uuid.uuid4().hex[:10].upper(), "entity": "refund", "status": "processed",
                    "payment_id": payment_id, **body}
        return self._req("POST", f"/v1/payments/{payment_id}/refund", body)

    # ------------------------------------------------------------ webhooks
    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        """X-Razorpay-Signature must equal HMAC-SHA256(raw request body, webhook secret)."""
        if not (self.webhook_secret and signature):
            return False
        return hmac.compare_digest(_sig(self.webhook_secret, raw_body), signature)


def capture_id_of(res):
    return (res or {}).get("capture_id") or (res or {}).get("payment_id")
