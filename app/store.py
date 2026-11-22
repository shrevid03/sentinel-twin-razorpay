"""Durable payment and mandate state (SQLAlchemy Core): Postgres in production via DATABASE_URL, SQLite otherwise.

Design rules, so a crash or a duplicate/out-of-order event can never charge twice or lose a capture:
  * money is integer paise, never floats;
  * risk state (approved / review / blocked) and payment state are separate columns;
  * payment state only moves FORWARD through an explicit transition table, applied with a conditional UPDATE
    (WHERE pay_state IN allowed_from), so a stale or repeated event is a no-op;
  * budget is reserved atomically before a checkout opens (UPDATE ... WHERE remaining >= amount), converted to spent
    exactly once on capture, released exactly once on failure or expiry, and credited back once on refund;
  * "capture_requested" is written BEFORE Razorpay is asked to capture, so a crash leaves a trail the reconciler
    can resolve by asking Razorpay what actually happened;
  * webhook deliveries are deduplicated by event id, and the business action is idempotent anyway (by state).
"""
from __future__ import annotations

import os
import threading
import time
import uuid

from sqlalchemy import (BigInteger, Column, Float, Integer, MetaData, String, Table, Text, and_, create_engine, insert,
                        select, update)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

meta = MetaData()
mandates = Table("mandates", meta,
                 Column("buyer", String(40), primary_key=True),
                 Column("per_order_limit", BigInteger, nullable=False),
                 Column("monthly_budget", BigInteger, nullable=False),
                 Column("spent", BigInteger, nullable=False, default=0),
                 Column("reserved", BigInteger, nullable=False, default=0),
                 Column("blocked_categories", Text, nullable=False, default="gift_cards"),
                 Column("currency", String(8), nullable=False, default="INR"))
reservations = Table("reservations", meta,
                     Column("id", String(40), primary_key=True),
                     Column("buyer", String(40), nullable=False),
                     Column("amount", BigInteger, nullable=False),
                     Column("status", String(16), nullable=False),  # active / converted / released
                     Column("credited", Integer, nullable=False, default=0),  # refund credited back once
                     Column("created_at", Float, nullable=False),
                     Column("expires_at", Float, nullable=False))
payments = Table("payments", meta,
                 Column("order_id", String(60), primary_key=True),
                 Column("buyer", String(40), nullable=False),
                 Column("amount", BigInteger, nullable=False),
                 Column("currency", String(8), nullable=False),
                 Column("risk_state", String(16), nullable=False),
                 Column("pay_state", String(24), nullable=False),
                 Column("payment_id", String(60)),
                 Column("reservation_id", String(40)),
                 Column("receipt_id", String(40)),
                 Column("twin_order", String(40)),
                 Column("merchant", String(16)),
                 Column("updated_at", Float, nullable=False))
payment_events = Table("payment_events", meta,
                       Column("id", Integer, primary_key=True, autoincrement=True),
                       Column("order_id", String(60), nullable=False),
                       Column("from_state", String(24)),
                       Column("to_state", String(24), nullable=False),
                       Column("source", String(80), nullable=False),
                       Column("at", Float, nullable=False))
webhook_events = Table("webhook_events", meta,
                       Column("event_id", String(120), primary_key=True),
                       Column("event_type", String(80)),
                       Column("received_at", Float, nullable=False))

# Forward-only payment states. A transition is applied only when the current state is in ALLOWED[target].
ALLOWED = {
    "authorized": {"created"},
    "capture_requested": {"authorized", "unconfirmed"},
    "unconfirmed": {"capture_requested"},
    "captured": {"created", "authorized", "capture_requested", "unconfirmed"},  # webhooks may arrive early
    "refunded": {"created", "authorized", "capture_requested", "unconfirmed", "captured"},
    "not_captured": {"created", "authorized", "capture_requested", "unconfirmed"},
    "failed": {"created", "authorized"},
    "cancelled": {"created", "authorized"},
}
RANK = {"created": 0, "authorized": 1, "capture_requested": 2, "unconfirmed": 2, "captured": 3, "refunded": 4,
        "not_captured": 3, "failed": 3, "cancelled": 3}
RESERVATION_TTL = 30 * 60  # an abandoned checkout releases its budget after 30 minutes


def paise(inr):
    return int(round(float(inr) * 100))


def inr(p):
    return round(p / 100.0, 2)


class Store:
    def __init__(self, url=None):
        url = url or os.getenv("DATABASE_URL", "").strip() or "sqlite://"
        if url.startswith("postgres://"):  # Render hands out postgres://; SQLAlchemy wants postgresql+psycopg://
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.url = url
        if url == "sqlite://":  # private in-memory DB (tests, forks): one shared connection, guarded by a lock
            self.engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        elif url.startswith("sqlite"):
            self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        else:
            self.engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        self._mem_lock = threading.RLock() if url == "sqlite://" else None
        meta.create_all(self.engine)

    # ------------------------------------------------------------ plumbing
    def tx(self):
        """A transaction (BEGIN ... COMMIT). For the shared in-memory SQLite connection, also take a lock."""
        store = self

        class _Tx:
            def __enter__(self):
                if store._mem_lock:
                    store._mem_lock.acquire()
                self.cm = store.engine.begin()
                return self.cm.__enter__()

            def __exit__(self, *a):
                try:
                    return self.cm.__exit__(*a)
                finally:
                    if store._mem_lock:
                        store._mem_lock.release()
        return _Tx()

    def reset(self):
        with self.tx() as c:
            for t in (payment_events, payments, reservations, mandates, webhook_events):
                c.execute(t.delete())

    # ------------------------------------------------------------- mandate
    def ensure_mandate(self, buyer, per_order_limit, monthly_budget, blocked=("gift_cards",), currency="INR"):
        with self.tx() as c:
            if c.execute(select(mandates.c.buyer).where(mandates.c.buyer == buyer)).first() is None:
                c.execute(insert(mandates).values(buyer=buyer, per_order_limit=paise(per_order_limit),
                                                  monthly_budget=paise(monthly_budget), spent=0, reserved=0,
                                                  blocked_categories=",".join(blocked), currency=currency))

    def set_limits(self, buyer, per_order_limit=None, monthly_budget=None, blocked=None):
        vals = {}
        if per_order_limit is not None:
            vals["per_order_limit"] = paise(per_order_limit)
        if monthly_budget is not None:
            vals["monthly_budget"] = paise(monthly_budget)
        if blocked is not None:
            vals["blocked_categories"] = ",".join(blocked)
        if vals:
            with self.tx() as c:
                c.execute(update(mandates).where(mandates.c.buyer == buyer).values(**vals))

    def mandate(self, buyer):
        with self.tx() as c:
            m = c.execute(select(mandates).where(mandates.c.buyer == buyer)).mappings().first()
        if m is None:
            return None
        return {"per_order_limit": inr(m["per_order_limit"]), "monthly_budget": inr(m["monthly_budget"]),
                "spent": inr(m["spent"]), "reserved": inr(m["reserved"]),
                "remaining": inr(m["monthly_budget"] - m["spent"] - m["reserved"]),
                "blocked_categories": [x for x in m["blocked_categories"].split(",") if x], "currency": m["currency"]}

    # --------------------------------------------------------- reservations
    def reserve(self, buyer, amount_inr, ttl=RESERVATION_TTL):
        """Atomically hold budget for a checkout. Returns the reservation id, or None if it does not fit."""
        a = paise(amount_inr)
        with self.tx() as c:
            r = c.execute(update(mandates).where(and_(
                mandates.c.buyer == buyer, mandates.c.per_order_limit >= a,
                mandates.c.monthly_budget - mandates.c.spent - mandates.c.reserved >= a)).values(
                reserved=mandates.c.reserved + a))
            if r.rowcount != 1:
                return None
            rid = "RSV-" + uuid.uuid4().hex[:12].upper()
            now = time.time()
            c.execute(insert(reservations).values(id=rid, buyer=buyer, amount=a, status="active", credited=0,
                                                  created_at=now, expires_at=now + ttl))
            return rid

    def _release(self, c, rid):
        r = c.execute(update(reservations).where(and_(reservations.c.id == rid, reservations.c.status == "active"))
                      .values(status="released"))
        if r.rowcount == 1:
            row = c.execute(select(reservations).where(reservations.c.id == rid)).mappings().first()
            c.execute(update(mandates).where(mandates.c.buyer == row["buyer"])
                      .values(reserved=mandates.c.reserved - row["amount"]))
            return True
        return False

    def _convert(self, c, rid):
        r = c.execute(update(reservations).where(and_(reservations.c.id == rid, reservations.c.status == "active"))
                      .values(status="converted"))
        if r.rowcount == 1:
            row = c.execute(select(reservations).where(reservations.c.id == rid)).mappings().first()
            c.execute(update(mandates).where(mandates.c.buyer == row["buyer"]).values(
                reserved=mandates.c.reserved - row["amount"], spent=mandates.c.spent + row["amount"]))
            return True
        return False

    def _credit(self, c, rid):
        r = c.execute(update(reservations).where(and_(reservations.c.id == rid, reservations.c.status == "converted",
                                                      reservations.c.credited == 0)).values(credited=1))
        if r.rowcount == 1:
            row = c.execute(select(reservations).where(reservations.c.id == rid)).mappings().first()
            c.execute(update(mandates).where(mandates.c.buyer == row["buyer"])
                      .values(spent=mandates.c.spent - row["amount"]))
            return True
        return False

    def release(self, rid):
        with self.tx() as c:
            return self._release(c, rid)

    def expire_reservations(self, now=None):
        """Release budget held by checkouts that were opened but never paid (nothing captured or in flight)."""
        now = now or time.time()
        out = 0
        with self.tx() as c:
            rows = c.execute(select(reservations.c.id).where(and_(reservations.c.status == "active",
                                                                  reservations.c.expires_at < now))).all()
            for (rid,) in rows:
                p = c.execute(select(payments.c.pay_state).where(payments.c.reservation_id == rid)).first()
                if p is None:
                    out += self._release(c, rid)
                elif p[0] == "created":  # cancelling the never-paid checkout releases its reservation
                    r = self._transition(c, self._order_for(c, rid), "cancelled", "reservation expired")
                    out += r.get("money") == "released"
        return out

    def _order_for(self, c, rid):
        r = c.execute(select(payments.c.order_id).where(payments.c.reservation_id == rid)).first()
        return r[0] if r else None

    # ------------------------------------------------------------- payments
    def create_payment(self, order_id, buyer, amount_inr, risk_state, reservation_id, receipt_id=None,
                       twin_order=None, merchant=None, currency="INR"):
        with self.tx() as c:
            c.execute(insert(payments).values(order_id=order_id, buyer=buyer, amount=paise(amount_inr),
                                              currency=currency, risk_state=risk_state, pay_state="created",
                                              reservation_id=reservation_id, receipt_id=receipt_id,
                                              twin_order=twin_order, merchant=merchant, updated_at=time.time()))
            c.execute(insert(payment_events).values(order_id=order_id, from_state=None, to_state="created",
                                                    source="checkout opened", at=time.time()))

    def payment(self, order_id):
        if not isinstance(order_id, str) or "\x00" in order_id:  # Postgres text cannot hold NUL: never a real id
            return None
        with self.tx() as c:
            r = c.execute(select(payments).where(payments.c.order_id == order_id)).mappings().first()
        return dict(r) if r else None

    def payment_by_payment_id(self, payment_id):
        if not isinstance(payment_id, str) or "\x00" in payment_id:
            return None
        with self.tx() as c:
            r = c.execute(select(payments).where(payments.c.payment_id == payment_id)).mappings().first()
        return dict(r) if r else None

    def unresolved(self):
        """Payments whose outcome at Razorpay may be ahead of what we recorded."""
        with self.tx() as c:
            return [dict(r) for r in c.execute(select(payments).where(
                payments.c.pay_state.in_(["capture_requested", "unconfirmed", "authorized"]))).mappings()]

    def events(self, order_id):
        with self.tx() as c:
            return [dict(r) for r in c.execute(select(payment_events).where(payment_events.c.order_id == order_id)
                                                .order_by(payment_events.c.id)).mappings()]

    def _transition(self, c, order_id, to_state, source, payment_id=None):
        allowed = ALLOWED[to_state]
        vals = {"pay_state": to_state, "updated_at": time.time()}
        if payment_id:
            vals["payment_id"] = payment_id
        cur = c.execute(select(payments.c.pay_state, payments.c.reservation_id).where(
            payments.c.order_id == order_id)).first()
        if cur is None:
            return {"applied": False, "state": None, "reason": "unknown order"}
        r = c.execute(update(payments).where(and_(payments.c.order_id == order_id,
                                                  payments.c.pay_state.in_(sorted(allowed)))).values(**vals))
        if r.rowcount != 1:
            return {"applied": False, "state": cur[0]}
        c.execute(insert(payment_events).values(order_id=order_id, from_state=cur[0], to_state=to_state, source=source,
                                                at=time.time()))
        rid = cur[1]
        money = None
        if rid:
            if to_state == "captured":
                money = "debited" if self._convert(c, rid) else None
            elif to_state in ("not_captured", "failed", "cancelled"):
                money = "released" if self._release(c, rid) else None
            elif to_state == "refunded":
                if self._credit(c, rid):
                    money = "credited"
                elif self._release(c, rid):  # refunded before we ever saw the capture: nothing was spent
                    money = "released"
        return {"applied": True, "state": to_state, "from": cur[0], "money": money}

    def transition(self, order_id, to_state, source, payment_id=None):
        """Move a payment forward. Repeated, stale or out-of-order transitions are no-ops (applied=False)."""
        with self.tx() as c:
            return self._transition(c, order_id, to_state, source, payment_id)

    def set_risk_state(self, order_id, risk_state):
        with self.tx() as c:
            c.execute(update(payments).where(payments.c.order_id == order_id).values(risk_state=risk_state))

    # -------------------------------------------------------------- webhooks
    def first_delivery(self, event_id, event_type):
        """True the first time an event id is seen (survives restarts, unlike an in-memory set)."""
        event_id, event_type = str(event_id).replace("\x00", ""), str(event_type).replace("\x00", "")
        try:
            with self.tx() as c:
                c.execute(insert(webhook_events).values(event_id=event_id[:120], event_type=event_type[:80],
                                                        received_at=time.time()))
            return True
        except IntegrityError:
            return False
