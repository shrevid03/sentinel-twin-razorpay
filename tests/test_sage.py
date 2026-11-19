"""NumPy GraphSAGE: analytic gradients match finite differences, shapes, determinism, learns a structural signal."""
import numpy as np

from app.sage import SAGE, MeanAgg, SageMaskModel, mask_edges


def _toy(seed=0, n=12, f=4):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, f))
    src = rng.integers(0, n, 30)
    dst = rng.integers(0, n, 30)
    y = rng.integers(0, 2, n)
    return X, np.r_[src, dst], np.r_[dst, src], y


def test_gradients_match_finite_differences():
    X, src, dst, y = _toy()
    net = SAGE(X.shape[1], hidden=5, dropout=0.0, seed=3)
    agg = MeanAgg(src, dst, len(X))
    tr = np.arange(len(X))
    cw = np.array([1.0, 2.0])
    _, grads, _ = net.loss_and_grads(X, agg, y, tr, cw)
    for p, g in zip(net.params(), grads):
        for idx in list(np.ndindex(p.shape))[:6]:
            old = p[idx]
            p[idx] = old + 1e-6
            lp = net.loss_and_grads(X, agg, y, tr, cw)[0]
            p[idx] = old - 1e-6
            lm = net.loss_and_grads(X, agg, y, tr, cw)[0]
            p[idx] = old
            assert abs((lp - lm) / 2e-6 - g[idx]) < 1e-5


def test_mean_aggregator_transpose_is_exact():
    X, src, dst, _ = _toy(1)
    agg = MeanAgg(src, dst, len(X))
    A = np.zeros((len(X), len(X)))
    for s, d in zip(src, dst):
        A[d, s] += 1
    A = A / np.maximum(A.sum(1, keepdims=True), 1)
    G = np.random.default_rng(2).normal(size=X.shape)
    assert np.allclose(agg(X), A @ X) and np.allclose(agg.T(G), A.T @ G)


def test_shapes_and_determinism():
    X, src, dst, y = _toy(4, n=30)
    p1 = SAGE(4, seed=9)
    p1.fit(X, src, dst, y, np.arange(20), epochs=20)
    p2 = SAGE(4, seed=9)
    p2.fit(X, src, dst, y, np.arange(20), epochs=20)
    a, b = p1.predict_proba(X, src, dst), p2.predict_proba(X, src, dst)
    assert a.shape == (30,) and np.array_equal(a, b)


def test_learns_a_signal_only_visible_through_neighbours():
    rng = np.random.default_rng(0)
    n = 200
    y = (np.arange(n) < 40).astype(int)  # fraud = members of a dense block; features are pure noise
    X = rng.normal(size=(n, 3))
    src, dst = [], []
    for i in range(n):
        pool = range(40) if y[i] else range(40, n)
        for j in rng.choice(list(pool), 4, replace=False):
            src += [i, j]
            dst += [j, i]
    m = SAGE(3, seed=1)
    tr = rng.permutation(n)[:120]
    m.fit(X, np.array(src), np.array(dst), y, tr, epochs=150)
    te = np.setdiff1d(np.arange(n), tr)
    p = m.predict_proba(X, np.array(src), np.array(dst))
    assert ((p[te] >= 0.5) == y[te]).mean() > 0.6  # better than chance (block-membership needs propagation)


def test_mask_adapter_matches_edge_interface():
    rng = np.random.default_rng(5)
    mask = rng.random((10, 10)) < 0.3
    mask = mask | mask.T | np.eye(10, dtype=bool)
    src, dst = mask_edges(mask)
    assert not np.any(src == dst)
    m = SageMaskModel(3, hidden=8, seed=2)
    X = rng.normal(size=(10, 3))
    m.fit(X, mask, rng.integers(0, 2, 10), np.arange(8), np.arange(8, 10), epochs=5)
    assert m.predict_proba(X, mask).shape == (10,)


def test_twin_can_run_on_graphsage(monkeypatch):
    from app.agents import Team
    from app.razorpay import Razorpay
    from app.twin import Twin
    monkeypatch.setenv("TWIN_MODEL", "graphsage")
    t = Twin(seed=3)
    t.train(30)
    assert t.metrics["model"] == "graphsage" and 0 <= t.risk("A-001") <= 1
    assert t.explain("A-001")["top_neighbors"]
    team = Team(t, Razorpay())
    case = team.casebook.open_case(next(a.id for a in t.accounts.values() if a.segment == "cycle-1"))
    assert case["pattern"] == "cycle"
