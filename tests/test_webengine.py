"""The real web engine (Qt WebEngine / Chromium) showing the dashboard in the window.

Optional, because it starts a browser engine without a screen:
    SECUREAPP_WEBENGINE_TESTS=1 pytest tests/test_webengine.py
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request

import pyotp
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SECUREAPP_WEBENGINE_TESTS") != "1",
                                reason="real web engine: set SECUREAPP_WEBENGINE_TESTS=1")

TITLE = "MFE | Multi-Market Financial Data Analysis Platform"
PW = "correct horse battery staple"


def pause(ms):
    """Let Qt run for `ms` milliseconds.

    A QEventLoop is used instead of QTest.qWait because qWait keeps Python's GIL, which would
    starve the gateway's server threads (the dashboard's requests would take tens of seconds).
    """
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(condition, timeout_ms=30_000, step_ms=100):
    waited = 0
    while not condition() and waited < timeout_ms:
        pause(step_ms)
        waited += step_ms
    return condition()


def run_js(view, code):
    result = {}
    view.page().runJavaScript(code, 0, lambda value: result.setdefault("value", value))
    wait_until(lambda: "value" in result, 10_000, 20)
    return result.get("value")


def status_of(url, token=None):
    from Authentication.gateway import TOKEN_HEADER
    request = urllib.request.Request(url, headers={TOKEN_HEADER: token} if token else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_dashboard_in_the_window(qapp, store, clock, monkeypatch):
    import main
    import shiboken6
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from Authentication import ui
    from Authentication.auth import AuthService
    from Authentication.gateway import DashboardGateway

    auth = AuthService(store, clock=clock.wall, monotonic=clock.mono)
    gateway = DashboardGateway(main.app.server)
    win = ui.MainWindow(auth, gateway)  # real DashboardView
    other = None
    try:
        _check_dashboard_in_the_window(win, auth, clock, monkeypatch)
        other = QWebEngineView()
        _check_other_browser_is_refused(win.dashboard_page.view, other)
        _check_logout(win, gateway)
    finally:
        # Even after a failure: a web page must be deleted before the profile it uses,
        # otherwise Chromium crashes when Python exits.
        win.close()  # disposes the dashboard view, then its private profile
        if other is not None:
            shiboken6.delete(other)
        gateway.stop()


def _check_dashboard_in_the_window(win, auth, clock, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from Authentication import ui

    win.resize(1400, 900)
    win.show()
    win.setup_page.username.setText("andrea")
    win.setup_page.password.setText(PW)
    win.setup_page.confirm.setText(PW)
    win.setup_page.button.click()
    win.enroll_page.code.setText(pyotp.TOTP(win.pending.totp_secret).at(clock.now))
    assert wait_until(lambda: win.stack.currentWidget() is win.codes_page, 5_000)
    win.codes_page.ack.setChecked(True)
    win.codes_page.button.click()

    view = win.dashboard_page.view
    assert isinstance(view, ui.DashboardView)
    # The page loads only if the interceptor adds the token to every request: without it the
    # gateway answers 403 and the title would never become the dashboard's.
    assert wait_until(lambda: view.title() == TITLE), view.title()
    assert wait_until(lambda: "Multi-Market Financial Data Analysis Platform" in (
        run_js(view, "document.body ? document.body.innerText : ''") or ""))
    assert wait_until(lambda: "mercati disponibili nel database" in (
        run_js(view, "document.body.innerText") or ""))  # a callback ran through the gateway
    if os.environ.get("SCREENSHOT_DIR"):
        pause(1500)
        win.grab().save(os.path.join(os.environ["SCREENSHOT_DIR"], "8_dashboard_in_window.png"))

    # A market page, as when Borsa Italiana is chosen in the menu: several callbacks in a row.
    # A choice made while the start-up callbacks are still running is reset by them (menus
    # go back to "no market"), so the choice is repeated until the page shows.
    def market_page_is_open():
        if "STRUMENTI QUOTATI" in (run_js(view, "document.body.innerText") or "").upper():
            return True
        run_js(view, "window.dash_clientside.set_props('market-dropdown', {value: 'EQ_IT'})")
        return False

    assert wait_until(market_page_is_open, 30_000, 2_000)
    assert wait_until(lambda: (run_js(view, "document.querySelectorAll('.js-plotly-plot').length") or 0) >= 1)

    # Clicking or scrolling in the dashboard counts as activity for the idle timeout.
    touched = []
    original_touch = auth.touch
    monkeypatch.setattr(auth, "touch", lambda session: touched.append(1) or original_touch(session))
    inside_view = view.mapTo(win, view.rect().center())
    QTest.mouseClick(win.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, inside_view)
    pause(200)
    assert touched


def _check_other_browser_is_refused(view, other):
    """Another browser on the same computer (here: a second web view without the token)."""
    other.setUrl(view.url())
    assert wait_until(lambda: "Access denied" in (run_js(other, "document.body ? document.body.innerText : ''")
                                                    or ""))
    assert other.title() != TITLE


def _check_logout(win, gateway):
    """Logout: blank page, and the old token no longer opens anything."""
    view = win.dashboard_page.view
    token = gateway.token
    url = gateway.url
    win.logout_action.trigger()
    assert wait_until(lambda: view.url().toString() == "about:blank")
    assert status_of(url, token) == 403
