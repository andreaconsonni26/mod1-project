"""Create a new user from the Terminal (administrators only).

    python -m Authentication.add_user      (from the project folder)

You first sign in as an administrator with both factors (password + 6-digit code),
then choose the new user's username, temporary password and role. At their first
sign-in the new user scans their own QR code in the app. Uses the same database as
the app, so it also works for the installed .app.
"""
from __future__ import annotations

import getpass
import sys

from . import config, policy
from .auth import ROLE_PERMISSIONS, AuthError, AuthService, PendingEnrollment, RestartRequired
from .storage import Store


def main() -> int:
    store = Store(config.data_dir() / "secureapp.db")
    auth = AuthService(store)
    try:
        if auth.needs_setup():
            print("No users yet: open the app first and create the administrator.")
            return 1

        print("Sign in as administrator")
        pending = auth.login_password(input("  Username: "), getpass.getpass("  Password: "))
        if isinstance(pending, PendingEnrollment):
            print("This account has not set up two-factor authentication yet: do it in the app first.")
            return 1
        while True:  # a mistyped code can be retried (attempts are still limited)
            try:
                session = auth.verify_otp(pending, input("  6-digit code: "))
                break
            except RestartRequired:
                raise
            except AuthError as exc:
                print(f"  {exc}")

        print("\nNew user")
        username = input("  Username: ")
        password = getpass.getpass(f"  Temporary password (min. {config.PASSWORD_MIN_LENGTH} characters): ")
        if password != getpass.getpass("  Repeat password: "):
            print("The two passwords do not match.")
            return 1
        roles = ", ".join(sorted(ROLE_PERMISSIONS))
        role = input(f"  Role ({roles}) [user]: ").strip().lower() or "user"
        auth.create_user(session, username, password, role)  # checks the 'users.manage' permission
        auth.logout(session)
        print(f"\nCreated '{policy.normalize_username(username)}' with role '{role}'. "
              "At the first sign-in they will set up their own authenticator app.")
        return 0
    except AuthError as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
