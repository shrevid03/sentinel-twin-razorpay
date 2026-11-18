import numpy as np

from app.gat import GAT


def test_gradients_match_finite_differences():
    rng = np.random.default_rng(0)
    n, f = 12, 5
    X = rng.normal(size=(n, f))
    mask = (rng.random((n, n)) < 0.3) | np.eye(n, dtype=bool)
    mask = mask | mask.T
    y = rng.integers(0, 2, n)
    idx = np.arange(n)
    model = GAT(f, hidden=3, heads=2)
    _, grads, _ = model.loss_and_grads(X, mask, y, idx)
    eps = 1e-6
    for p, g in zip(model.params(), grads):
        flat = p.reshape(-1)
        for k in rng.choice(flat.size, size=min(4, flat.size), replace=False):
            old = flat[k]
            flat[k] = old + eps
            lp, _, _ = model.loss_and_grads(X, mask, y, idx)
            flat[k] = old - eps
            lm, _, _ = model.loss_and_grads(X, mask, y, idx)
            flat[k] = old
            num = (lp - lm) / (2 * eps)
            assert abs(num - g.reshape(-1)[k]) < 1e-5 + 1e-3 * abs(num)


def _grad_check(model, X, mask, y, idx, rng):
    _, grads, _ = model.loss_and_grads(X, mask, y, idx)
    eps = 1e-6
    for p, g in zip(model.params(), grads):
        flat = p.reshape(-1)
        for k in rng.choice(flat.size, size=min(4, flat.size), replace=False):
            old = flat[k]
            flat[k] = old + eps
            lp, _, _ = model.loss_and_grads(X, mask, y, idx)
            flat[k] = old - eps
            lm, _, _ = model.loss_and_grads(X, mask, y, idx)
            flat[k] = old
            num = (lp - lm) / (2 * eps)
            assert abs(num - g.reshape(-1)[k]) < 1e-5 + 1e-3 * abs(num)


def test_skip_connection_gradients_match_finite_differences():
    rng = np.random.default_rng(1)
    n, f = 12, 5
    X = rng.normal(size=(n, f))
    mask = (rng.random((n, n)) < 0.3) | np.eye(n, dtype=bool)
    mask = mask | mask.T
    y = rng.integers(0, 2, n)
    _grad_check(GAT(f, hidden=3, heads=2, skip=True), X, mask, y, np.arange(n), rng)
    # with skip on, a node with no neighbours (self loop only) gets 2x its own features: A @ Z + Z
    model = GAT(f, hidden=3, heads=2, skip=True)
    iso = np.eye(n, dtype=bool)
    out, _ = model.heads1[0].forward(X, iso, skip=True)
    assert np.allclose(out, 2 * X @ model.heads1[0].W)


def test_filtered_mask_gradients_match_finite_differences():
    rng = np.random.default_rng(2)
    n, f = 14, 4
    X = rng.normal(size=(n, f))
    mask = (rng.random((n, n)) < 0.5) | np.eye(n, dtype=bool)
    mask = mask | mask.T
    y = (X[:, 0] > 0).astype(int)
    model = GAT(f, hidden=3, heads=2, skip=True, filter_keep=0.5)
    model.fit_similarity(X, y, np.arange(n))
    _grad_check(model, X, model.filter_mask(X, mask), y, np.arange(n), rng)


def test_neighbour_filter_keeps_self_and_closest_fraction_and_ignores_test_labels():
    rng = np.random.default_rng(3)
    n, f = 30, 4
    X = rng.normal(size=(n, f))
    y = (X[:, 0] + 0.3 * rng.normal(size=n) > 0.5).astype(int)
    mask = (rng.random((n, n)) < 0.6) | np.eye(n, dtype=bool)
    mask = mask | mask.T
    tr = np.arange(20)
    model = GAT(f, filter_keep=0.5)
    model.fit_similarity(X, y, tr)
    fm = model.filter_mask(X, mask)
    assert fm.diagonal().all() and not (fm & ~mask).any()  # self kept, nothing new added
    deg = (mask & ~np.eye(n, dtype=bool)).sum(1)
    kept = (fm & ~np.eye(n, dtype=bool)).sum(1)
    assert (kept >= np.ceil(0.5 * deg)).all() and (kept <= deg).all()
    w, b = model._sim
    score = 1 / (1 + np.exp(-(X @ w + b)))
    for i in range(n):  # every kept neighbour is at least as close in score as every dropped one
        nb = np.where(mask[i] & (np.arange(n) != i))[0]
        if len(nb):
            d = np.abs(score[nb] - score[i])
            k = fm[i, nb]
            if k.any() and (~k).any():
                assert d[k].max() <= d[~k].min() + 1e-12
    y2 = y.copy()
    y2[20:] = 1 - y2[20:]  # flipping TEST labels must not change the filter
    m2 = GAT(f, filter_keep=0.5)
    m2.fit_similarity(X, y2, tr)
    assert (m2.filter_mask(X, mask) == fm).all()


def test_defaults_unchanged():
    m = GAT(5)
    assert m.skip is False and m.filter_keep is None
    rng = np.random.default_rng(4)
    X = rng.normal(size=(6, 5))
    mask = np.ones((6, 6), dtype=bool)
    assert (m.filter_mask(X, mask) == mask).all()


def test_twin_trains_and_detects_rings():
    from app.twin import Twin

    twin = Twin(seed=3)
    metrics = twin.train()
    assert metrics["test_auc"] > 0.85
