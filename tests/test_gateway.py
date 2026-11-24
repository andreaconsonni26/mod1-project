"""The local gateway: the dashboard answers only the signed-in window. Real HTTP requests."""
from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from Authentication.gateway import TOKEN_HEADER, DashboardGateway


def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"secret market data"]


def get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@pytest.fixture
def gateway():
    g = DashboardGateway(app)
    g.start()
    yield g
    g.stop()


def test_listens_only_on_this_computer(gateway):
    assert gateway.url == f"http://127.0.0.1:{gateway.port}/"
    assert gateway.port > 0


def test_closed_without_a_session(gateway):
    status, body = get(gateway.url)
    assert status == 403 and b"secret" not in body


def test_requires_the_exact_token(gateway):
    token = gateway.open_session()
    assert get(gateway.url, {TOKEN_HEADER: token}) == (200, b"secret market data")
    assert get(gateway.url)[0] == 403  # header missing
    assert get(gateway.url, {TOKEN_HEADER: token[:-1]})[0] == 403  # almost right
    assert get(gateway.url, {TOKEN_HEADER: "x" * len(token)})[0] == 403
    assert get(gateway.url, {"X-Other-Header": token})[0] == 403  # right value, wrong header


def test_token_never_in_the_address(gateway):
    token = gateway.open_session()
    assert get(gateway.url + "?token=" + token)[0] == 403


def test_logout_and_new_session_revoke_old_tokens(gateway):
    first = gateway.open_session()
    second = gateway.open_session()
    assert first != second and len(second) >= 43  # 32 random bytes, base64
    assert get(gateway.url, {TOKEN_HEADER: first})[0] == 403
    assert get(gateway.url, {TOKEN_HEADER: second})[0] == 200
    gateway.close_session()
    assert get(gateway.url, {TOKEN_HEADER: second})[0] == 403


def test_token_is_sent_only_to_the_gateway(gateway):
    port = gateway.port
    assert gateway.is_own_url(f"http://127.0.0.1:{port}/_dash-layout")
    for other in (f"http://127.0.0.1:{port + 1}/", f"https://127.0.0.1:{port}/", f"http://localhost:{port}/",
                  "https://cdn.plot.ly/world_110m.json", "https://fonts.googleapis.com/css2",
                  "http://127.0.0.1:99999999/", "not a url"):
        assert not gateway.is_own_url(other), other


def test_stop_and_not_started():
    g = DashboardGateway(app)
    assert g.port is None and not g.is_own_url("http://127.0.0.1:8050/")
    with pytest.raises(RuntimeError):
        _ = g.url
    g.start()
    url = g.url
    token = g.open_session()
    g.stop()
    assert g.token is None
    with pytest.raises(OSError):  # nothing listens any more
        urllib.request.urlopen(urllib.request.Request(url, headers={TOKEN_HEADER: token}), timeout=2)
