"""The dashboard of main.py (ex app.py) with the real market database, also through the gateway."""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

import pytest

import main
import self_test
from Authentication.gateway import TOKEN_HEADER, DashboardGateway

PERIOD = ["2020-01-01", "2026-11-22"]


def find(component, component_id):
    """Depth-first search of a Dash component tree by id."""
    if getattr(component, "id", None) == component_id:
        return component
    children = getattr(component, "children", None)
    for child in children if isinstance(children, (list, tuple)) else [children]:
        if child is not None and not isinstance(child, (str, int, float)):
            found = find(child, component_id)
            if found is not None:
                return found
    return None


@pytest.fixture(scope="module")
def gateway():
    g = DashboardGateway(main.app.server)
    g.start()
    yield g
    g.stop()


def request(gateway, path="", body=None, token=True):
    headers = {TOKEN_HEADER: gateway.token} if token and gateway.token else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(gateway.url + path.lstrip("/"), data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def test_entry_point_selftest():
    assert main.main(["main.py", "--selftest"]) == 0
    assert self_test.run_selftest(main.app) == 0


def test_dev_mode_runs_the_dashboard_alone_from_source(monkeypatch):
    calls = []
    monkeypatch.setattr(main.app, "run", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(main, "run_desktop", lambda *args: pytest.fail("the window should not open"))
    assert main.main(["main.py", "--dev"]) == 0
    assert calls == [{"debug": True}]


def test_packaged_app_ignores_dev_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)  # what PyInstaller sets in the .app
    monkeypatch.setattr(main.app, "run", lambda **kwargs: pytest.fail("dashboard without sign-in"))
    opened = []
    monkeypatch.setattr(main, "run_desktop", lambda server, argv: opened.append(server) or 0)
    assert main.main(["main.py", "--dev"]) == 0
    assert opened == [main.app.server]


def test_page_and_all_scripts_through_the_gateway(gateway):
    gateway.open_session()
    status, page = request(gateway)
    assert status == 200 and b"MFE | Multi-Market Financial Data Analysis Platform" in page
    scripts = re.findall(r'<script src="(/[^"]+)"', page.decode())
    assert len(scripts) >= 8
    for src in scripts:
        assert request(gateway, src)[0] == 200, src
    assert request(gateway, "/assets/MFEimage.png")[0] == 200
    assert request(gateway, "/_dash-layout", token=False)[0] == 403


def test_callback_over_http(gateway):
    """A real Dash callback, as the web view sends it: JSON POST with the session token."""
    gateway.open_session()
    body = {"output": "asset-status.children", "outputs": {"id": "asset-status", "property": "children"},
            "inputs": [{"id": "asset-class", "property": "value", "value": "fx"}],
            "changedPropIds": ["asset-class.value"], "state": []}
    status, data = request(gateway, "/_dash-update-component", body)
    assert status == 200
    assert json.loads(data)["response"]["asset-status"]["children"] == "3 mercati disponibili nel database."
    assert request(gateway, "/_dash-update-component", body, token=False)[0] == 403


def test_globe_has_a_marker_for_every_localised_equity_market():
    figure, message = main.draw_map("equity", "all")
    localised = [code for code in main.markets_of_asset_class("equity")["market_code"]
                 if main.geography_of(code)["lat"] is not None]
    assert len(figure.data[0].lat) == len(localised) == 14
    assert message.startswith("14 punti sul globo")


def test_market_page_lists_instruments_and_benchmark():
    page = main.render_market_page("EQ_IT")
    dropdown = find(page, "instrument")
    tickers = {option["value"] for option in dropdown.options}
    assert {"ENEL.MI", "ENI.MI", "ISP.MI", "STLAM.MI", "G.MI", "FTSEMIB.MI"} <= tickers
    assert dropdown.value != "FTSEMIB.MI"  # a security is selected by default, not the index


@pytest.mark.parametrize("tab", ["prices", "returns", "risk", "market"])
def test_every_analysis_tab_renders(tab):
    content = main.render_tab(tab, "ENEL.MI", PERIOD, "D", "EQ_IT")
    assert content and not isinstance(content, list)
    assert "Nessun dato" not in str(content)


def test_incomplete_period_is_not_queried():
    content = main.render_tab("prices", "ENEL.MI", [PERIOD[0], None], "D", "EQ_IT")
    assert "Seleziona sia la data iniziale" in str(content)
