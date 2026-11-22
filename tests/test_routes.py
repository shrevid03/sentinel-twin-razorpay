"""The landing page lives at "/" and the app at "/app"; both load their assets from absolute /static paths."""
from fastapi.testclient import TestClient


def test_landing_and_app_routes(monkeypatch):
    monkeypatch.setenv("GAT_EPOCHS", "20")
    from app import main

    client = TestClient(main.app)
    landing = client.get("/")
    assert landing.status_code == 200 and "Open the live twin" in landing.text and 'href="/app"' in landing.text
    app_page = client.get("/app")
    assert app_page.status_code == 200 and 'id="tab-command"' in app_page.text
    assert 'src="/static/app.js' in app_page.text and 'href="/static/style.css' in app_page.text
    assert client.get("/static/landing.html").status_code == 200
    for asset in ("theme.css", "landing.css", "landing.js", "smoke.js", "vendor/gsap.min.js", "vendor/ScrollTrigger.min.js",
                  "vendor/lenis.min.js", "vendor/fonts/fraunces-latin-full-normal.woff2", "js/sound.js", "js/scene3d.js",
                  "vendor/three/three.module.min.js", "vendor/postprocessing/index.js"):  # self-hosted, no CDN at runtime
        assert client.get("/static/" + asset).status_code == 200, asset
