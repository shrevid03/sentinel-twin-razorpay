"""Under simulated live traffic the twin's account features drift; the server retrains once enough new orders arrive
so the Investigator never shows every account at risk 0.00."""
from app.twin import Twin


def test_needs_retrain_after_enough_new_orders():
    t = Twin(seed=3)
    t.train(epochs=5)
    assert not t.needs_retrain(every=50)
    start = len(t.orders)
    while len(t.orders) - start < 50:
        t.tick(5, fraud_rate=0.12)
    assert t.needs_retrain(every=50)
    t.train(epochs=5)
    assert not t.needs_retrain(every=50)
