import httpx
import pytest

from radar.config import RateLimit
from radar.http import BlockedByRobots, FetchError, PoliteClient, RequestBudgetExceeded
from tests.helpers import FakeClock


def client(handler, rpm=600, max_req=10, clock=None):
    clock = clock or FakeClock()
    return PoliteClient(
        "src", RateLimit(requests_per_minute=rpm, max_requests_per_run=max_req),
        transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock.monotonic,
    ), clock


def make_handler(robots: httpx.Response | None, seen: list[str], page_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return robots if robots is not None else httpx.Response(404)
        return httpx.Response(page_status, text="ok", headers={"content-type": "text/html"})

    return handler


def test_robots_disallow_blocks_without_ever_requesting_the_page():
    seen: list[str] = []
    c, _ = client(make_handler(httpx.Response(200, text="User-agent: *\nDisallow: /privato/"), seen))
    with pytest.raises(BlockedByRobots):
        c.get("https://www.comune.example.it/privato/bando")
    assert not any("/privato/" in u for u in seen)


def test_bot_specific_disallow_is_honored():
    seen: list[str] = []
    robots = httpx.Response(200, text="User-agent: GlobalOpportunityRadar\nDisallow: /\n")
    c, _ = client(make_handler(robots, seen))
    with pytest.raises(BlockedByRobots):
        c.get("https://www.comune.example.it/qualquer")


def test_missing_robots_txt_allows_fetching():
    seen: list[str] = []
    c, _ = client(make_handler(None, seen))
    assert c.get("https://www.comune.example.it/bando").text == "ok"


def test_unavailable_robots_txt_blocks_conservatively():
    seen: list[str] = []
    c, _ = client(make_handler(httpx.Response(503), seen))
    with pytest.raises(BlockedByRobots, match="robots"):
        c.get("https://www.comune.example.it/bando")


def test_requests_are_spaced_according_to_requests_per_minute():
    seen: list[str] = []
    c, clock = client(make_handler(None, seen), rpm=30)  # 1 requisição a cada 2s
    for i in range(3):
        c.get(f"https://www.comune.example.it/p{i}")
    assert sum(clock.sleeps) >= 4.0


def test_request_budget_per_run_is_enforced_but_robots_fetch_is_free():
    seen: list[str] = []
    c, _ = client(make_handler(None, seen), max_req=2)
    c.get("https://www.comune.example.it/a")
    c.get("https://www.comune.example.it/b")
    with pytest.raises(RequestBudgetExceeded):
        c.get("https://www.comune.example.it/c")
    assert c.requests_made == 2


def test_user_agent_identifies_the_bot_and_has_contact_slot():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers["user-agent"]
        return httpx.Response(404 if request.url.path == "/robots.txt" else 200, text="x")

    c, _ = client(handler)
    c.get("https://www.comune.example.it/a")
    assert captured["ua"].startswith("GlobalOpportunityRadar/")


def test_redirect_into_a_disallowed_path_is_blocked():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /area-riservata/")
        if request.url.path == "/bando":
            return httpx.Response(302, headers={"location": "/area-riservata/doc"})
        return httpx.Response(200, text="segredo")

    c, _ = client(handler)
    with pytest.raises(BlockedByRobots):
        c.get("https://www.comune.example.it/bando")
    assert not any(u.endswith("/area-riservata/doc") for u in seen)


def test_non_http_schemes_are_rejected():
    c, _ = client(make_handler(None, []))
    with pytest.raises(FetchError):
        c.get("file:///etc/passwd")


def test_server_errors_raise_fetch_error_with_status():
    c, _ = client(make_handler(None, [], page_status=503))
    with pytest.raises(FetchError) as ei:
        c.get("https://www.comune.example.it/a")
    assert ei.value.status == 503
