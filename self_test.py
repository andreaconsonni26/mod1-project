"""`python main.py --selftest`: checks, without opening any window, that every part
of the program works. build_mac.sh runs it on the packaged app, where PyInstaller
may have left out a library or a data file (a JavaScript bundle, the database).

Exit code 0 and "SELFTEST OK" if everything works, 1 and the reason otherwise.
"""
from __future__ import annotations

import math
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _check(condition: bool, what: str) -> None:
    if not condition:
        raise RuntimeError(f"check failed: {what}")


def _check_authentication() -> None:
    """Password hashing, encryption, one-time codes, QR code and user storage."""
    import pyotp
    import qrcode

    from Authentication import security
    from Authentication.auth import AuthService
    from Authentication.storage import Store

    stored = security.hash_password("selftest passphrase")
    _check(security.verify_password(stored, "selftest passphrase"), "Argon2id hash/verify")
    key = security.derive_key("selftest passphrase", security.new_salt())
    _check(security.decrypt(key, security.encrypt(key, b"ok", b"ctx"), b"ctx") == b"ok", "AES-256-GCM")
    secret = security.new_totp_secret()
    _check(security.match_totp(secret, pyotp.TOTP(secret).now(), time.time(), 1) is not None, "TOTP")
    qr = qrcode.QRCode()
    qr.add_data(security.totp_provisioning_uri(secret, "selftest"))
    qr.make(fit=True)
    _check(len(qr.get_matrix()) > 20, "QR code")
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "selftest.db")
        _check(AuthService(store).needs_setup(), "user storage (SQLite)")
        store.close()


def _check_dashboard(dash_app) -> None:
    """Market database, analytics and every file the dashboard sends to the web view."""
    from analytics import metrics
    from database import repository
    from database.connection import database_exists

    _check(database_exists(), "market database present")
    markets = repository.list_markets()
    _check(not markets.empty, "markets in the database")
    tickers = [ticker for code in markets["market_code"]  # some markets have no instruments yet
               for ticker in repository.list_instruments(code)["ticker"]]
    _check(bool(tickers), "instruments in the database")
    prices = repository.get_prices(tickers[0])
    _check(len(prices) > 2, "price history")
    summary = metrics.summary(prices["adj_close"].fillna(prices["close"]))
    _check(math.isfinite(summary["Volatilità annualizzata"]), "risk metrics")

    client = dash_app.server.test_client()
    index = client.get("/")
    _check(index.status_code == 200, "dashboard page")
    scripts = re.findall(r'<script src="(/[^"]+)"', index.get_data(as_text=True))
    _check(len(scripts) >= 5, "dashboard scripts listed")
    for src in scripts:  # React, Dash, Mantine, Plotly bundles: data files PyInstaller must collect
        _check(client.get(src).status_code == 200, f"script {src}")
    for path in ("/_dash-layout", "/_dash-dependencies", dash_app.get_asset_url("MFEimage.png")):
        response = client.get(path)
        _check(response.status_code == 200 and response.content_length != 0, path)


def _check_gateway(dash_app) -> None:
    """The real local server: closed without the session token, open with it."""
    from Authentication.gateway import TOKEN_HEADER, DashboardGateway

    gateway = DashboardGateway(dash_app.server)
    gateway.start()
    try:
        try:
            urllib.request.urlopen(gateway.url, timeout=10)
            raise RuntimeError("check failed: gateway answered without a token")
        except urllib.error.HTTPError as error:
            _check(error.code == 403, "gateway refuses requests without a token")
        token = gateway.open_session()
        request = urllib.request.Request(gateway.url, headers={TOKEN_HEADER: token})
        with urllib.request.urlopen(request, timeout=10) as response:
            _check(response.status == 200 and b"Multi-Market" in response.read(), "gateway serves the dashboard")
    finally:
        gateway.stop()


def _check_web_engine() -> str:
    """The web engine that shows the dashboard in the window (Qt WebEngine)."""
    from PySide6 import QtCore
    from PySide6.QtWebEngineCore import QWebEngineProfile, qWebEngineChromiumVersion
    from PySide6.QtWebEngineWidgets import QWebEngineView  # the widget shown after sign-in

    _check(hasattr(QWebEngineProfile, "setUrlRequestInterceptor"), "Qt WebEngine API")
    _check(hasattr(QWebEngineView, "setPage"), "Qt WebEngine widgets")
    return f"Qt {QtCore.qVersion()}, Chromium {qWebEngineChromiumVersion()}"


def run_selftest(dash_app) -> int:
    try:
        _check_authentication()
        _check_dashboard(dash_app)
        _check_gateway(dash_app)
        engines = _check_web_engine()
    except Exception as exc:  # noqa: BLE001
        print(f"SELFTEST FAILED: {exc!r}", file=sys.stderr)
        return 1
    print(f"SELFTEST OK (Python {sys.version.split()[0]}, {engines})")
    return 0
