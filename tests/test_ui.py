"""End-to-end test of the window, driven by code on Qt's 'offscreen' platform (no screen needed).

The web view is replaced by FakeView (it records what it is asked to show), so these tests
run anywhere without the web engine; test_webengine.py uses the real one.
Set SCREENSHOT_DIR=some/folder to also save a PNG of every page.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request

import pyotp
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

import style
from Authentication import config, ui
from Authentication.auth import GENERIC_LOGIN_ERROR, AuthService
from Authentication.gateway import TOKEN_HEADER, DashboardGateway

PW = "correct horse battery staple"


class FakeView(QWidget):
    """Stands in for the web view: records the address the window asks it to open."""

    def __init__(self, gateway, parent=None):
        super().__init__(parent)
        self.gateway = gateway
        self.url = None

    def open_dashboard(self, url):
        self.url = url

    def close_dashboard(self):
        self.url = None


def fake_dashboard(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"dashboard"]


def http_get(url, token=None):
    request = urllib.request.Request(url, headers={TOKEN_HEADER: token} if token else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def pump(ms: int = 20) -> None:
    QTest.qWait(ms)  # let queued signals/timers run


def shot(window, name: str) -> None:
    folder = os.environ.get("SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        window.grab().save(os.path.join(folder, f"{name}.png"))


@pytest.fixture
def window(qapp, store, clock):
    gateway = DashboardGateway(fake_dashboard)
    win = ui.MainWindow(AuthService(store, clock=clock.wall, monotonic=clock.mono), gateway,
                        view_factory=FakeView)
    win.show()
    pump()
    yield win
    win.close()
    gateway.stop()


def valid_codes(secret, clock):
    return {pyotp.TOTP(secret).at(clock.now + 30 * k) for k in (-1, 0, 1)}


def test_full_flow(window, clock):
    win = window
    # 1. first launch: the window opens directly on account creation, in the platform colours
    assert win.stack.currentWidget() is win.setup_page
    assert not win.logout_action.isEnabled()
    pump()
    image = win.setup_page.grab().toImage()
    assert image.pixelColor(5, 5).name() == style.COLORS["bg"].lower()  # page background
    card = win.setup_page.card
    card_image = card.grab().toImage()
    assert card_image.pixelColor(card.width() // 2, card.height() - 8).name() == style.COLORS["bg_panel"].lower()
    shot(win, "1_setup")
    setup = win.setup_page
    setup.username.setText("andrea")
    setup.password.setText(PW)
    setup.confirm.setText("something else")
    setup.button.click()
    assert "do not match" in setup.message.text()
    setup.confirm.setText(PW)
    setup.button.click()

    # 2. two-factor setup with QR code
    assert win.stack.currentWidget() is win.enroll_page
    assert not win.enroll_page.qr.pixmap().isNull()
    secret = win.pending.totp_secret
    assert secret[:4] in win.enroll_page.secret.text()
    shot(win, "2_enroll")
    wrong = next(c for c in ("000000", "111111", "222222") if c not in valid_codes(secret, clock))
    win.enroll_page.code.setText(wrong)  # 6 digits: submitted automatically
    pump()
    assert win.stack.currentWidget() is win.enroll_page
    assert "Invalid code" in win.enroll_page.message.text()
    win.enroll_page.code.setText(pyotp.TOTP(secret).at(clock.now))
    pump()

    # 3. recovery codes, shown once
    assert win.stack.currentWidget() is win.codes_page
    codes = win.codes_page.codes.toPlainText().splitlines()
    assert len(codes) == config.RECOVERY_CODE_COUNT
    shot(win, "3_recovery_codes")
    assert not win.codes_page.button.isEnabled()
    win.codes_page.ack.setChecked(True)
    win.codes_page.button.click()

    # 4. the dashboard: opened through the gateway, which now answers only with the session token
    gateway = win.gateway
    assert win.stack.currentWidget() is win.dashboard_page
    assert win.logout_action.isEnabled()
    token = gateway.token
    assert token
    assert win.dashboard_page.view.url == gateway.url
    assert "andrea (admin)" in win.dashboard_page.user_label.text()
    assert http_get(gateway.url, token) == (200, b"dashboard")
    assert http_get(gateway.url)[0] == 403  # no token: refused
    assert win.codes_page.codes.toPlainText() == ""  # codes wiped once the page is left
    shot(win, "4_dashboard")

    # 5. log out from the bar under the dashboard: the token stops working at once
    win.dashboard_page.logout_button.click()
    assert win.stack.currentWidget() is win.login_page
    assert "signed out" in win.login_page.message.text()
    assert gateway.token is None and win.dashboard_page.view.url is None
    assert http_get(gateway.url, token)[0] == 403

    # 6. level 1: wrong password, then right password
    login = win.login_page
    login.username.setText("andrea")
    login.password.setText("not the right passphrase")
    login.button.click()
    assert login.message.text() == GENERIC_LOGIN_ERROR and login.password.text() == ""
    clock.advance(31)
    login.password.setText(PW)
    shot(win, "5_login")
    login.button.click()
    assert win.stack.currentWidget() is win.otp_page

    # 7. level 2: wrong code, then right code; every session gets a new token
    win.otp_page.code.setText(next(c for c in ("123456", "654321") if c not in valid_codes(secret, clock)))
    pump()
    assert "Invalid code" in win.otp_page.message.text()
    shot(win, "6_otp")
    win.otp_page.code.setText(pyotp.TOTP(secret).at(clock.now))
    pump()
    assert win.stack.currentWidget() is win.dashboard_page
    second_token = gateway.token
    assert second_token and second_token != token
    assert http_get(gateway.url, second_token)[0] == 200

    # 8. inactivity: the session expires, the window goes back to sign-in, the dashboard closes
    clock.advance(config.SESSION_IDLE_TIMEOUT + 1)
    win._check_session()
    assert win.stack.currentWidget() is win.login_page
    assert "expired" in win.login_page.message.text()
    assert http_get(gateway.url, second_token)[0] == 403
    shot(win, "7_expired")

    # 9. recovery code instead of the authenticator
    login.username.setText("andrea")
    login.password.setText(PW)
    login.button.click()
    win.otp_page.toggle.click()
    win.otp_page.recovery.setText(codes[0])
    win.otp_page.button.click()
    assert win.stack.currentWidget() is win.dashboard_page


def test_no_dashboard_before_sign_in(window):
    """Before the first sign-in the gateway is not even listening."""
    assert window.gateway.port is None and window.gateway.token is None
    assert window.dashboard_page.view is None


def test_closing_the_window_closes_the_dashboard(qapp, store, clock):
    auth = AuthService(store, clock=clock.wall, monotonic=clock.mono)
    pending = auth.create_admin("andrea", PW)
    session, _ = auth.confirm_enrollment(pending, pyotp.TOTP(pending.totp_secret).at(clock.now))
    gateway = DashboardGateway(fake_dashboard)
    win = ui.MainWindow(auth, gateway, view_factory=FakeView)
    win.session = session
    win._enter_dashboard()
    token = gateway.token
    assert http_get(gateway.url, token)[0] == 200
    win.close()
    assert http_get(gateway.url, token)[0] == 403
    gateway.stop()


def test_role_without_data_read_gets_no_dashboard(qapp, store, clock, monkeypatch):
    import Authentication.auth as auth_module
    monkeypatch.setitem(auth_module.ROLE_PERMISSIONS, "admin", frozenset({"users.manage"}))
    auth = AuthService(store, clock=clock.wall, monotonic=clock.mono)
    pending = auth.create_admin("andrea", PW)
    session, _ = auth.confirm_enrollment(pending, pyotp.TOTP(pending.totp_secret).at(clock.now))
    gateway = DashboardGateway(fake_dashboard)
    win = ui.MainWindow(auth, gateway, view_factory=FakeView)
    win.session = session
    win._enter_dashboard()
    assert gateway.token is None and win.dashboard_page.view is None  # no token, no dashboard
    assert win.stack.currentWidget() is win.login_page
    assert "role does not allow" in win.login_page.message.text()
    assert win.session is None
    win.close()
    gateway.stop()


def sign_in(win, clock):
    """Shortcut to the dashboard: create the admin and confirm the code through the service."""
    pending = win.auth.create_admin("andrea", PW)
    session, _ = win.auth.confirm_enrollment(pending, pyotp.TOTP(pending.totp_secret).at(clock.now))
    win.session = session
    win._enter_dashboard()
    assert win.stack.currentWidget() is win.dashboard_page


def test_activity_in_the_window_keeps_the_session_alive(window, clock):
    """Clicks and keys reach the window (QWindow) first, wherever they land: page or web view."""
    sign_in(window, clock)
    clock.advance(config.SESSION_IDLE_TIMEOUT - 60)
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(200, 200))  # as the operating system delivers it
    clock.advance(120)  # 16 minutes after sign-in, 2 minutes after the click
    window._check_session()
    assert window.stack.currentWidget() is window.dashboard_page
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_Down)
    clock.advance(config.SESSION_IDLE_TIMEOUT - 1)
    window._check_session()
    assert window.stack.currentWidget() is window.dashboard_page
    clock.advance(2)  # now really idle for 15 minutes
    window._check_session()
    assert window.stack.currentWidget() is window.login_page
    assert window.gateway.token is None
