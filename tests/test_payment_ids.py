"""Payment identifiers in the twin graph: UPI IDs, card fingerprints and hashed contacts link accounts like devices."""
import pytest

from app import agents
from app.razorpay import Razorpay, payer_ids
from app.twin import Twin

CART = [{"sku": "SKU-COF-02", "qty": 1}]


@pytest.fixture(scope="module")
def trained_twin():
    t = Twin(seed=3)
    t.train(40)
    return t


def test_fraud_rings_share_payment_identifiers():
    t = Twin(seed=3)
    for ring in (f"ring-{i}" for i in range(1, 6)):
        members = [a for a in t.accounts.values() if a.segment == ring]
        held = {}
        for a in members:
            for pid in a.pay_ids:
                held[pid] = held.get(pid, 0) + 1
        assert max(held.values()) >= 2, ring  # mule UPI IDs / cards are reused across the ring


def test_shared_identifiers_are_graph_nodes_and_singletons_are_not():
    t = Twin(seed=3)
    nodes, idx, X, mask = t.build_graph()
    pay_nodes = [n for n in nodes if n.split("-")[0] in ("UPI", "CARD", "CT")]
    assert pay_nodes
    for n in pay_nodes:
        assert sum(n in a.pay_ids for a in t.accounts.values()) >= 2
        assert X[idx[n], 15] == 1  # is_payment_id


def test_payer_ids_are_salted_hashes_without_raw_pii():
    p = {"vpa": "Asha.K@okhdfcbank", "contact": "+91 98765 43210", "email": "asha@example.com",
         "card": {"network": "Visa", "last4": "1111", "issuer": "HDFC"}}
    ids = payer_ids(p)
    assert [i.split("-")[0] for i in ids] == ["UPI", "CARD", "CT", "CT"]
    blob = " ".join(ids).lower()
    for raw in ("asha", "okhdfc", "98765", "43210", "1111", "hdfc", "visa", "example"):
        assert raw not in blob
    assert payer_ids(p) == ids  # stable: the same payer links across payments
    assert payer_ids({"email": "void@razorpay.com"}) == []  # Razorpay's placeholder email is ignored


def test_demo_capture_mirrors_hashed_payer_ids_onto_the_buyer(trained_twin):
    t = trained_twin.fork()
    team = agents.Team(t, Razorpay())
    t.policy = {**t.policy, "review_threshold": 1.01, "block_threshold": 1.01}
    before = set(t.accounts[team.buyer].pay_ids)
    out = team.guarded_checkout(CART)
    team.capture(out["pay_order_id"])
    added = t.accounts[team.buyer].pay_ids - before
    assert added and all(i.split("-")[0] in ("UPI", "CT") for i in added)
    assert not any("okaxis" in i or "9900000001" in i for i in added)
    assert any("payer identifier" in s["event"] for s in team.receipts[out["receipt_id"]]["timeline"])


def test_shared_upi_with_flagged_account_is_explained(trained_twin):
    t = trained_twin.fork()
    ring = next(a for a in t.accounts.values() if a.segment == "ring-1")
    upi = next(i for i in sorted(ring.pay_ids) if i.startswith(("UPI-", "CARD-")))
    t.freeze(ring.id, "test")
    acc = t.new_account(age_days=40, kyc=True)
    acc.devices.add(t.new_device())
    acc.ips.add(t.new_ip(acc.country))
    t.link_pay_ids(acc.id, [upi], source="test")
    assert any(l["account"] == ring.id for l in t.linked_accounts(acc.id, hops=1))
    s = t.score_order(acc.id, "M-02", 3000, next(iter(acc.devices)), next(iter(acc.ips)))
    assert any(f.startswith("shares ") and "flagged account" in f for f in s["factors"])


def test_known_fraud_signal_reads_only_training_labels_and_frozen_accounts(trained_twin):
    t = trained_twin.fork()
    held_out = [a for a in t.accounts.values() if a.fraud and a.id not in t.known_labelled]
    trained = [a for a in t.accounts.values() if a.id in t.known_fraud]
    assert held_out and trained

    def newcomer(partner):
        acc = t.new_account(age_days=40, kyc=True)
        acc.devices.add(t.new_device())
        acc.ips.add(t.new_ip(acc.country))
        acc.pay_ids.add(t.new_pay_id("UPI"))
        partner.pay_ids |= {next(iter(acc.pay_ids))}
        return acc

    # linked only to a held-out fraudster (through a fresh UPI ID): that label is never read
    assert t.known_fraud_signal(newcomer(held_out[0]).id) == {"share": 0.0, "known_fraud": 0, "labelled": 0, "linked": 1}
    acc = newcomer(trained[0])
    sig = t.known_fraud_signal(acc.id)
    assert sig["known_fraud"] >= 1 and sig["share"] > 0
    other = held_out[0]
    acc2 = newcomer(other)  # again linked only to the held-out fraudster
    before = t.known_fraud_signal(acc2.id)["known_fraud"]
    t.freeze(other.id, "investigator confirmed")
    assert t.known_fraud_signal(acc2.id)["known_fraud"] == before + 1  # a frozen account counts as known fraud


def test_twin_is_reproducible_across_hash_seeds():
    import subprocess
    import sys
    code = ("from app.twin import Twin; t = Twin(seed=3); t.train(20); "
            "print(sorted(t.accounts['A-005'].devices), round(t.risk('A-005'), 6), len(t.orders))")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={"PYTHONHASHSEED": h, "PATH": ""}).stdout for h in ("0", "1")}
    assert len(outs) == 1 and next(iter(outs)).strip()
