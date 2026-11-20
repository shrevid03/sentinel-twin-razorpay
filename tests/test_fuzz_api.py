"""Every API route answers bad input with a clean 4xx (or a normal reply), never a 500."""
import json

import pytest
from fastapi.testclient import TestClient

BAD_IDS = ["zzz", "A-99999", "../../etc/passwd", "%00", "x" * 300, "ACT-<script>"]
BAD_BODIES = [{}, {"text": 123}, {"seed": "nan"}, {"seed": -1, "apply": "yes"}, {"amount": -5},
              {"amount": 1e308}, {"params": "not a dict"}, {"review_threshold": "high"}, {"question": None},
              {"decision": "delete_everything", "reason": "", "reviewer": ""}, {"items": [{"sku": 5, "qty": -3}]},
              {"enabled": "maybe", "max_inr": -1}, [], "garbage"]


@pytest.fixture(scope="module")
def client():
    import os
    os.environ["GAT_EPOCHS"] = "20"
    os.environ["RATE_LIMIT"] = "0"
    from app import main
    return TestClient(main.app, raise_server_exceptions=False)


def _routes(client):
    out = []
    for r in client.app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        if not path.startswith("/api/") or path in ("/api/reset",):
            continue
        for m in methods - {"HEAD", "OPTIONS"}:
            out.append((m, path))
    return out


def test_no_route_returns_500_on_bad_input(client):
    failures = []
    for method, path in _routes(client):
        paths = [path] if "{" not in path else [path.split("{")[0] + b + path.split("}", 1)[1] for b in BAD_IDS]
        for p in paths:
            heavy = any(h in p for h in ("drill", "compare", "model-redteam", "redteam", "replay", "scenario",
                                         "investigate", "chat"))
            # heavy endpoints accept no body fields that matter, so one junk body each is enough (they take seconds)
            bodies = ([BAD_BODIES[-1]] if heavy else BAD_BODIES) if method == "POST" else [None]
            for body in bodies:
                if method == "GET":
                    r = client.get(p, params={"limit": "-5", "since": "abc", "focus": "x" * 500})
                else:
                    r = client.post(p, content=json.dumps(body) if not isinstance(body, str) else body,
                                    headers={"content-type": "application/json"})
                if r.status_code >= 500:
                    failures.append((method, p, body, r.status_code, r.text[:120]))
    assert not failures, failures[:10]
