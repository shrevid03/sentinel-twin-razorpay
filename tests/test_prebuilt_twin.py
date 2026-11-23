"""A twin trained at build time and loaded from disk behaves exactly like one trained at startup."""
import pickle

from app.twin import Twin


def test_pickled_twin_scores_identically():
    t = Twin(seed=3)
    t.train(20)
    import threading
    lock, t.lock = t.lock, None   # as scripts/prebuild_twin.py does
    t2 = pickle.loads(pickle.dumps(t))
    t.lock, t2.lock = lock, threading.RLock()
    accs = list(t.accounts)[:40]
    assert [round(t.risk(a), 6) for a in accs] == [round(t2.risk(a), 6) for a in accs]
    assert t2.model_version == t.model_version
