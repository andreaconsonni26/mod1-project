"""Local gateway between the sign-in window and the dashboard.

The dashboard (main.py) is a Dash web application, so the window shows it with a
web engine and the dashboard must be served over HTTP. This module serves it:

* only on 127.0.0.1, the computer itself: other computers cannot connect;
* on a free port chosen by the operating system;
* only to requests carrying the session token: a 256-bit random secret created at
  every sign-in and known only to the window, which sends it in an HTTP header
  (never in the address). Anything else (another program on the same Mac, a web
  page open in a browser) gets "403 Forbidden";
* not at all before sign-in: the server starts at the first successful sign-in.

At logout or session expiry the token is deleted, so the dashboard stops answering
at once, even to a page that is still open.
"""
from __future__ import annotations

import hmac
import logging
import secrets
import threading
from urllib.parse import urlsplit

from werkzeug.serving import make_server

TOKEN_HEADER = "X-SecureApp-Token"
_ENVIRON_KEY = "HTTP_" + TOKEN_HEADER.upper().replace("-", "_")  # how WSGI exposes that header
_FORBIDDEN = (b"<!doctype html><meta charset='utf-8'><title>403</title>"
              b"<p>Access denied: sign in from the application window.</p>")


class DashboardGateway:
    """WSGI wrapper around the dashboard that lets through only the signed-in window."""

    def __init__(self, wsgi_app, host: str = "127.0.0.1") -> None:
        self._app = wsgi_app
        self._host = host
        self._server = None
        self._thread: threading.Thread | None = None
        self._token: str | None = None
        self._lock = threading.Lock()  # the token is read by the server threads

    # --- server -------------------------------------------------------------------------
    def start(self) -> None:
        """Start serving in a background thread. Calling it again does nothing."""
        if self._server is not None:
            return
        logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no log line per request
        self._server = make_server(self._host, 0, self, threaded=True)  # port 0 = any free port
        self._thread = threading.Thread(target=self._server.serve_forever, name="dashboard-gateway",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.close_session()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def port(self) -> int | None:
        return self._server.port if self._server is not None else None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("The gateway has not been started.")
        return f"http://{self._host}:{self.port}/"

    def is_own_url(self, url: str) -> bool:
        """True only for addresses served by this gateway (the token is never sent elsewhere)."""
        if self._server is None:
            return False
        try:
            parts = urlsplit(url)
            return parts.scheme == "http" and parts.hostname == self._host and parts.port == self.port
        except ValueError:  # malformed address, e.g. an invalid port
            return False

    # --- session token ----------------------------------------------------------------------
    def open_session(self) -> str:
        """New random token for a new session; any previous token stops working."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._token = token
        return token

    def close_session(self) -> None:
        with self._lock:
            self._token = None

    @property
    def token(self) -> str | None:
        with self._lock:
            return self._token

    # --- WSGI: every request passes through here ----------------------------------------------
    def __call__(self, environ, start_response):
        expected = self.token
        presented = environ.get(_ENVIRON_KEY, "").encode("latin-1", "replace")
        if expected is None or not hmac.compare_digest(presented, expected.encode("ascii")):
            start_response("403 Forbidden", [("Content-Type", "text/html; charset=utf-8"),
                                             ("Content-Length", str(len(_FORBIDDEN))),
                                             ("Cache-Control", "no-store")])
            return [_FORBIDDEN]
        return self._app(environ, start_response)
