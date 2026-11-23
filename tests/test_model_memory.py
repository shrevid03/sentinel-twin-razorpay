"""Copies of the model (model versions, scenario forks) must not carry the N×N training caches or attention maps:
on a 512 MB server each copy would otherwise cost tens of megabytes and the fraud-ring scenario ran out of memory."""
from app.twin import Twin


def test_trained_and_explained_model_keeps_no_big_arrays():
    t = Twin(seed=3)
    t.train(10)
    acc = next(iter(t.accounts))
    t.explain(acc, top=3)
    t._dirty = True
    t.refresh()
    m = t.model
    assert all(h.cache is None for h in m.heads1 + [m.head2])
    assert m._pre is None and not m.attn1 and m.attn2 is None
    f = t.fork()
    assert all(h.cache is None for h in f.model.heads1)
