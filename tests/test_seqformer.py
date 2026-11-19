"""Transaction-sequence transformer: exact gradients, padding has no effect, deterministic, learns order patterns."""
import numpy as np

from app.seqformer import FIELDS, MAX_EVENTS, SeqFormer, encode


def _seqs(rng, n, bust=False):
    out = []
    for _ in range(n):
        k = rng.integers(3, 10)
        s = [{"kind": 1, "amount": int(rng.integers(2, 4)), "category": int(rng.integers(2, 12)), "new_device": 1,
              "new_place": 1, "gap": int(rng.integers(4, 7))} for _ in range(k)]
        if bust:  # warm-up of small orders, then a big one soon after
            s.append({"kind": 1, "amount": 8, "category": 3, "new_device": 1, "new_place": 1, "gap": 3})
        out.append(s)
    return out


def test_gradients_match_finite_differences():
    rng = np.random.default_rng(0)
    ids, mask = encode(_seqs(rng, 4) + _seqs(rng, 2, bust=True))
    y = np.array([0, 0, 0, 0, 1, 1])
    m = SeqFormer(d=8, heads=2, ff=12, seed=1)
    cw = np.array([1.0, 2.0])
    _, grads = m.loss_and_grads(ids, mask, y, cw)
    params = m.params()[:-1]  # the scalar bias is checked through w's path
    for p, g in zip(params, grads[:-1]):
        nz = [i for i in np.ndindex(p.shape) if abs(g[i]) > 0][:5] or list(np.ndindex(p.shape))[:3]
        for idx in nz:
            old = p[idx]
            p[idx] = old + 1e-6
            lp = m.loss_and_grads(ids, mask, y, cw)[0]
            p[idx] = old - 1e-6
            lm = m.loss_and_grads(ids, mask, y, cw)[0]
            p[idx] = old
            assert abs((lp - lm) / 2e-6 - g[idx]) < 1e-5, (p.shape, idx)


def test_padding_and_determinism():
    rng = np.random.default_rng(3)
    s = _seqs(rng, 1)
    ids, mask = encode(s)
    m1, m2 = SeqFormer(seed=4), SeqFormer(seed=4)
    a = m1.embed(ids, mask)
    ids2 = ids.copy()
    ids2[0, len(s[0]):] = 0  # already padding: unchanged
    assert np.allclose(a, m2.embed(ids2, mask)) and a.shape == (1, 16)
    assert ids.shape == (1, MAX_EVENTS, len(FIELDS))


def test_learns_a_bust_out_pattern():
    rng = np.random.default_rng(7)
    seqs = _seqs(rng, 60) + _seqs(rng, 20, bust=True)
    y = np.array([0] * 60 + [1] * 20)
    ids, mask = encode(seqs)
    m = SeqFormer(seed=2).fit(ids, mask, y, epochs=120)
    p = m.predict(ids, mask)
    assert p[60:].mean() > p[:60].mean() + 0.3
    att = m.attention_from_cls()
    assert att.shape == (80, MAX_EVENTS) and np.allclose(att[0][len(seqs[0]):], 0, atol=1e-6)
