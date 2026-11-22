"""What the customer is told about money, by payment state. One place, so no screen over-promises.

Razorpay facts these rely on (razorpay.com/docs/payments/payments/capture-settings/ and .../refunds/auto-refunds):
  * an authorized payment is not settled to the merchant, but the customer's bank may already show it as a hold or a
    debit;
  * a payment that is not captured within the capture window is refunded automatically. Our orders ask for a manual
    capture window of 7,200 minutes (5 days); on accounts that ignore per-order settings the Dashboard default
    applies (3 days by default);
  * auto-refunds and normal refunds reach the customer in 5-7 working days.

"Nothing was charged" is only ever said when no payment was attempted.
"""

CAPTURE_WINDOW = "within 5 days (3 days on accounts using Razorpay's default capture setting)"
REFUND_TIME = "5-7 working days"

NO_PAYMENT = "No payment was started, so nothing was charged."
NOT_CAPTURED = ("Not captured: your bank may show a temporary hold or debit for this amount. Razorpay reverses it "
                f"automatically: an uncaptured payment is refunded once the capture window ends ({CAPTURE_WINDOW}), "
                f"and the money reaches your account in {REFUND_TIME}.")
NOT_CAPTURED_SHORT = (f"not captured; any hold or debit at the bank is reversed by Razorpay automatically "
                      f"(auto-refund after the capture window, then {REFUND_TIME})")
CAPTURED = "Captured: the payment is complete and the merchant will receive it."
REFUND_INITIATED = f"Refund initiated: Razorpay normal refunds reach the customer's account in {REFUND_TIME}."
CONFIRMING = "Payment status is being confirmed with Razorpay. You will not be charged twice."
CHECKOUT_CLOSED = ("Razorpay Checkout was closed before the payment finished, so nothing was captured. If your bank "
                   f"still shows a debit, Razorpay reverses it automatically ({REFUND_TIME} after the capture window).")

# customer-facing message per durable payment state (store.payments.pay_state)
BY_STATE = {
    "created": NO_PAYMENT,
    "cancelled": NO_PAYMENT,
    "failed": "The payment failed at the bank or Razorpay, so it was not captured. " + NOT_CAPTURED.split(": ", 1)[1],
    "authorized": "Authorized, not yet captured: your bank may show a hold for this amount.",
    "not_captured": NOT_CAPTURED,
    "capture_requested": CONFIRMING,
    "unconfirmed": CONFIRMING,
    "captured": CAPTURED,
    "refunded": REFUND_INITIATED,
}


def for_state(state):
    return BY_STATE.get(state, CONFIRMING)
