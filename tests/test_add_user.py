"""Tests for add_user.py, the Terminal tool to create users (admin only)."""
from __future__ import annotations

import getpass
import time

import pyotp

from Authentication import add_user
from Authentication.auth import AuthService, PendingEnrollment
from Authentication.storage import Store

ADMIN_PW = "correct horse battery staple"
MARIO_PW = "il mio portafoglio 2026"


def next_code(secret: str) -> str:
    """Code of the next 30-s step: accepted (±1 step) and never a replay of the last one."""
    return pyotp.TOTP(secret).at(time.time() + 30)


def wrong_code(secret: str) -> str:
    valid = {pyotp.TOTP(secret).at(time.time() + 30 * k) for k in (-1, 0, 1, 2)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)


def run_script(monkeypatch, inputs: list[str], passwords: list[str]) -> int:
    answers, secrets_ = iter(inputs), iter(passwords)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(secrets_))
    return add_user.main()


def test_admin_creates_user(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SECUREAPP_DATA_DIR", str(tmp_path))
    db = tmp_path / "secureapp.db"
    auth = AuthService(Store(db))
    pending = auth.create_admin("andrea", ADMIN_PW)
    secret = pending.totp_secret
    auth.confirm_enrollment(pending, pyotp.TOTP(secret).now())

    code = run_script(monkeypatch, ["andrea", wrong_code(secret), next_code(secret), "Mario", "user"], [ADMIN_PW, MARIO_PW, MARIO_PW])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Invalid code" in out  # a wrong code can be retried
    assert "Created 'mario' with role 'user'" in out

    # the new user exists and must set up 2FA at the first sign-in
    assert isinstance(AuthService(Store(db)).login_password("mario", MARIO_PW), PendingEnrollment)


def test_non_admin_cannot_create_users(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SECUREAPP_DATA_DIR", str(tmp_path))
    db = tmp_path / "secureapp.db"
    auth = AuthService(Store(db))
    pending = auth.create_admin("andrea", ADMIN_PW)
    admin, _ = auth.confirm_enrollment(pending, pyotp.TOTP(pending.totp_secret).now())
    auth.create_user(admin, "mario", MARIO_PW, role="user")
    auth.logout(admin)
    enrollment = auth.login_password("mario", MARIO_PW)
    mario_secret = enrollment.totp_secret
    auth.confirm_enrollment(enrollment, pyotp.TOTP(mario_secret).now())

    code = run_script(monkeypatch, ["mario", next_code(mario_secret), "eve", "admin"],
                      [MARIO_PW, "another long passphrase", "another long passphrase"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Your role does not allow this action." in out
    assert Store(db).user_by_name("eve") is None
