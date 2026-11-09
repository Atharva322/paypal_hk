from fastapi.testclient import TestClient

from app.limits import DAY, HOUR, ModelCallLimiter
from app.site import serve

from .test_agent import make_client


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_each_visitor_gets_an_hourly_allowance():
    clock = Clock()
    limiter = ModelCallLimiter(per_hour=2, per_day=100, clock=clock)
    assert limiter.spend("a") is None
    assert limiter.spend("a") is None
    assert "hourly limit" in limiter.spend("a")
    assert limiter.spend("b") is None
    clock.now = HOUR
    assert limiter.spend("a") is None


def test_the_daily_total_covers_everyone():
    clock = Clock()
    limiter = ModelCallLimiter(per_hour=10, per_day=3, clock=clock)
    for client in "abc":
        assert limiter.spend(client) is None
    assert "today" in limiter.spend("d")
    clock.now = DAY
    assert limiter.spend("d") is None


def test_a_refused_call_is_not_counted():
    limiter = ModelCallLimiter(per_hour=1, per_day=2, clock=Clock())
    assert limiter.spend("a") is None
    assert limiter.spend("a") is not None
    assert limiter.spend("b") is None


def test_the_api_refuses_model_calls_over_the_limit(tmp_path):
    client, _ = make_client(tmp_path, model=None, model_calls_per_hour=1)
    with client:
        assert client.post("/mandates", json={"request": "running shoes under $80"}).status_code == 200
        refused = client.post("/mandates", json={"request": "running shoes under $80"})
        assert refused.status_code == 429
        assert "hourly limit" in refused.json()["detail"]


def test_a_deployment_serves_the_store_and_the_api_from_one_origin(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<h1>Pay Guard</h1>")
    plain, _ = make_client(tmp_path, model=None)
    with TestClient(serve(plain.app, tmp_path / "dist")) as client:
        assert "Pay Guard" in client.get("/").text
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/config").status_code == 200
