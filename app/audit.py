"""Durable auditability: everything needed to replay a historical risk decision survives a restart.

Stored next to the payment tables (same database: Postgres via DATABASE_URL, SQLite otherwise):
  * audit_epochs      one row per live twin (each process start or demo reset) and per scenario copy, with its parent
                      and the evidence sequence it was copied at;
  * graph_events      append-only graph event log: every account, link (device / IP / payment id), order, transfer,
                      training label and freeze, each with its evidence sequence number, simulated time and provenance;
  * risk_decisions    the full decision record: inputs, evidence with timing, feature snapshot, score, outcome,
                      model version and policy (version hash plus the policy itself);
  * model_artifacts   the exact model used, pickled and keyed by its version hash (plus a sha256 of the bytes);
  * policy_versions   every decision policy seen, by hash;
  * receipts          decision receipts (upserted as they change);
  * ledger_rows       the hash-chained compliance ledger, so the chain continues across restarts;
  * audit_counters    decision ids that are unique across restarts.

Replay after a restart never re-creates the world from the seed: it rebuilds the graph as it was at decision time
from graph_events (Twin.from_events) and scores it with the stored model artifact. A missing artifact makes the
decision "not replayable", never silently re-scored with today's model.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import time
import uuid

from sqlalchemy import Column, Float, Integer, LargeBinary, MetaData, String, Table, Text, insert, select, update

meta = MetaData()
epochs = Table("audit_epochs", meta,
               Column("epoch", String(40), primary_key=True),
               Column("parent", String(40)),
               Column("parent_seq", Integer),
               Column("note", String(120)),
               Column("created_at", Float, nullable=False))
graph_events = Table("graph_events", meta,
                     Column("epoch", String(40), primary_key=True),
                     Column("kind", String(16), primary_key=True),
                     Column("key", String(120), primary_key=True),
                     Column("seq", Integer, nullable=False, index=True),
                     Column("payload", Text, nullable=False))
risk_decisions = Table("risk_decisions", meta,
                       Column("id", String(40), primary_key=True),
                       Column("epoch", String(40), nullable=False),
                       Column("point", String(16), nullable=False),
                       Column("seq", Integer, nullable=False),
                       Column("model_version", String(80)),
                       Column("policy_version", String(16)),
                       Column("record", Text, nullable=False),
                       Column("created_at", Float, nullable=False))
model_artifacts = Table("model_artifacts", meta,
                        Column("version", String(80), primary_key=True),
                        Column("sha256", String(64), nullable=False),
                        Column("blob", LargeBinary, nullable=False),
                        Column("created_at", Float, nullable=False))
policy_versions = Table("policy_versions", meta,
                        Column("hash", String(16), primary_key=True),
                        Column("policy", Text, nullable=False),
                        Column("first_seen", Float, nullable=False))
receipts = Table("receipts", meta,
                 Column("id", String(40), primary_key=True),
                 Column("record", Text, nullable=False),
                 Column("updated_at", Float, nullable=False))
ledger_rows = Table("ledger_rows", meta,
                    Column("seq", Integer, primary_key=True, autoincrement=False),
                    Column("record", Text, nullable=False))
counters = Table("audit_counters", meta,
                 Column("name", String(40), primary_key=True),
                 Column("value", Integer, nullable=False))


def _dumps(x):
    return json.dumps(x, sort_keys=True, default=str)


class AuditStore:
    def __init__(self, store):
        self.store = store
        meta.create_all(store.engine)
        self.dialect = store.engine.dialect.name

    # ------------------------------------------------------------ plumbing
    def _insert_ignore(self, table, rows):
        if not rows:
            return
        if self.dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as ins
        else:
            from sqlalchemy.dialects.sqlite import insert as ins
        with self.store.tx() as c:
            for i in range(0, len(rows), 500):
                c.execute(ins(table).on_conflict_do_nothing(), rows[i:i + 500])

    def _upsert(self, table, key, row):
        if self.dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as ins
        else:
            from sqlalchemy.dialects.sqlite import insert as ins
        stmt = ins(table).values(**row)
        stmt = stmt.on_conflict_do_update(index_elements=[key], set_={k: v for k, v in row.items() if k != key})
        with self.store.tx() as c:
            c.execute(stmt)

    def next(self, name):
        """An id counter that is unique across restarts and processes (row-locked increment)."""
        self._insert_ignore(counters, [{"name": name, "value": 0}])
        with self.store.tx() as c:
            c.execute(update(counters).where(counters.c.name == name).values(value=counters.c.value + 1))
            return c.execute(select(counters.c.value).where(counters.c.name == name)).scalar_one()

    # ------------------------------------------------------------- epochs
    def new_epoch(self, parent=None, parent_seq=None, note=""):
        e = "EP-" + uuid.uuid4().hex[:12]
        with self.store.tx() as c:
            c.execute(insert(epochs).values(epoch=e, parent=parent, parent_seq=parent_seq, note=note[:120],
                                            created_at=time.time()))
        return e

    def epoch_chain(self, epoch):
        """[(epoch, max_seq_or_None), ...] from the given epoch back to its root."""
        out, cap = [], None
        with self.store.tx() as c:
            while epoch:
                row = c.execute(select(epochs).where(epochs.c.epoch == epoch)).mappings().first()
                if row is None:
                    break
                out.append((epoch, cap))
                ps = row["parent_seq"] or 0
                cap = ps if cap is None else min(cap, ps)
                epoch = row["parent"]
        return out

    # ------------------------------------------------------------- events
    def append_events(self, epoch, events):
        self._insert_ignore(graph_events, [{"epoch": epoch, "kind": k, "key": str(key)[:120], "seq": int(seq),
                                            "payload": _dumps(p)} for k, key, seq, p in events])

    def events(self, epoch, upto=None):
        """The graph event log of an epoch and its ancestors, as of evidence sequence `upto`, in sequence order."""
        rows = []
        with self.store.tx() as c:
            for ep, cap in self.epoch_chain(epoch):
                lim = upto if cap is None else (cap if upto is None else min(cap, upto))
                q = select(graph_events).where(graph_events.c.epoch == ep)
                if lim is not None:
                    q = q.where(graph_events.c.seq <= lim)
                rows += [dict(r) for r in c.execute(q).mappings()]
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: (r["seq"], r["kind"], r["key"])):
            k = (r["kind"], r["key"])
            if k not in seen:
                seen.add(k)
                out.append((r["kind"], r["key"], r["seq"], json.loads(r["payload"])))
        return out

    # ---------------------------------------------------------- artifacts
    def save_model(self, version, artifact):
        blob = pickle.dumps(artifact)
        self._insert_ignore(model_artifacts, [{"version": version, "sha256": hashlib.sha256(blob).hexdigest(),
                                               "blob": blob, "created_at": time.time()}])

    def load_model(self, version):
        with self.store.tx() as c:
            row = c.execute(select(model_artifacts).where(model_artifacts.c.version == version)).mappings().first()
        if row is None:
            return None
        if hashlib.sha256(row["blob"]).hexdigest() != row["sha256"]:
            raise ValueError(f"model artifact {version} failed its integrity check")
        return pickle.loads(row["blob"])  # our own artifact, written by this app; integrity-checked above

    def has_model(self, version):
        with self.store.tx() as c:
            return c.execute(select(model_artifacts.c.version).where(model_artifacts.c.version == version)).first() \
                is not None

    def delete_model(self, version):  # tests only: simulate a lost artifact
        with self.store.tx() as c:
            c.execute(model_artifacts.delete().where(model_artifacts.c.version == version))

    # ---------------------------------------------------------- decisions
    def save_decision(self, epoch, rec):
        self._insert_ignore(policy_versions, [{"hash": rec["policy_version"], "policy": _dumps(rec["policy"]),
                                               "first_seen": time.time()}])
        self._insert_ignore(risk_decisions, [{"id": rec["id"], "epoch": epoch, "point": rec["point"], "seq": rec["seq"],
                                              "model_version": rec["model_version"],
                                              "policy_version": rec["policy_version"], "record": _dumps(rec),
                                              "created_at": time.time()}])

    def decision(self, decision_id):
        if "\x00" in str(decision_id):  # Postgres text cannot hold NUL: such an id can never exist
            return None
        with self.store.tx() as c:
            row = c.execute(select(risk_decisions).where(risk_decisions.c.id == decision_id)).mappings().first()
        if row is None:
            return None
        return {**json.loads(row["record"]), "epoch": row["epoch"]}

    def policy(self, h):
        with self.store.tx() as c:
            row = c.execute(select(policy_versions).where(policy_versions.c.hash == h)).mappings().first()
        return json.loads(row["policy"]) if row else None

    # ----------------------------------------------------------- receipts
    def save_receipt(self, rc):
        self._upsert(receipts, "id", {"id": rc["id"], "record": _dumps(rc), "updated_at": time.time()})

    def receipt(self, rid):
        if "\x00" in str(rid):  # Postgres text cannot hold NUL: such an id can never exist
            return None
        with self.store.tx() as c:
            row = c.execute(select(receipts).where(receipts.c.id == rid)).mappings().first()
        return json.loads(row["record"]) if row else None

    def receipts(self, limit=500):
        with self.store.tx() as c:
            rows = c.execute(select(receipts).order_by(receipts.c.updated_at).limit(limit)).mappings().all()
        return [json.loads(r["record"]) for r in rows]

    # ------------------------------------------------------------- ledger
    def ledger_append(self, build):
        """Append one ledger record atomically: lock the chain, read its head, build the record on top, insert.
        build(seq, prev_hash) -> record. Several processes or Team objects can share one chain safely."""
        from sqlalchemy import func, text
        self._insert_ignore(counters, [{"name": "ledger_lock", "value": 0}])
        with self.store.tx() as c:
            if self.dialect == "postgresql":
                c.execute(text("SELECT pg_advisory_xact_lock(7311)"))
            else:  # SQLite: a write takes the database lock for the rest of the transaction
                c.execute(update(counters).where(counters.c.name == "ledger_lock").values(value=counters.c.value + 1))
            last = c.execute(select(func.max(ledger_rows.c.seq))).scalar()
            prev = "genesis"
            if last:
                prev = json.loads(c.execute(select(ledger_rows.c.record).where(ledger_rows.c.seq == last))
                                  .scalar_one())["hash"]
            rec = build((last or 0) + 1, prev)
            c.execute(insert(ledger_rows).values(seq=rec["seq"], record=_dumps(rec)))
        return rec

    def ledger_rows(self):
        with self.store.tx() as c:
            rows = c.execute(select(ledger_rows).order_by(ledger_rows.c.seq)).mappings().all()
        return [json.loads(r["record"]) for r in rows]

    def counts(self):
        from sqlalchemy import func
        with self.store.tx() as c:
            return {t.name: c.execute(select(func.count()).select_from(t)).scalar_one()
                    for t in (risk_decisions, model_artifacts, receipts, ledger_rows, epochs, graph_events)}
