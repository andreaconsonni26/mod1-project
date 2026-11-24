from __future__ import annotations

import os
import sys

# Qt without a screen (tests, CI). Must be set before PySide6 is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")  # lets the web engine draw without a GPU
if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")  # Chromium refuses its sandbox as root

import pytest  # noqa: E402

from Authentication.auth import AuthService  # noqa: E402
from Authentication.storage import Store  # noqa: E402


class FakeClock:
    """Drives both clocks used by AuthService (wall time and monotonic time)."""

    def __init__(self) -> None:
        self.now = 1_800_000_015.0  # in the middle of a 30-s TOTP step

    def wall(self) -> float:
        return self.now

    def mono(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def auth(store, clock) -> AuthService:
    return AuthService(store, clock=clock.wall, monotonic=clock.mono)


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole test run, with the platform's colours and style."""
    from Authentication import ui  # imports Qt WebEngine before the application is created
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    ui._apply_appearance(application)
    return application
