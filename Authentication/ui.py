"""PySide6 user interface: one window holding a stack of pages.

    first launch    SetupPage ─► EnrollPage ─► RecoveryCodesPage ─► DashboardPage
    every launch    LoginPage ─► OtpPage ───────────────────────► DashboardPage

The UI never decides who is signed in: it only shows what AuthService returns.
After sign-in, DashboardPage shows the MFE dashboard (main.py) in a web view,
served by the gateway (gateway.py) only to this window. Colours and style come
from style.py, the same file that styles the dashboard.
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import qrcode
import shiboken6
# QtWebEngine must be imported before the QApplication is created.
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QEvent, QObject, QRegularExpression, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QGuiApplication, QImage, QKeySequence, QPalette, QPixmap,
                           QRegularExpressionValidator)
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

import style  # visual identity of the platform (project folder): dashboard + this window

from . import config
from .auth import (AuthError, AuthService, PendingEnrollment, PendingLogin, PermissionDenied, RestartRequired,
                   Session)
from .gateway import TOKEN_HEADER, DashboardGateway
from .storage import Store

log = logging.getLogger(__name__)

QR_SIZE = 184
LOGO_SIZE = 56
BRAND_EYEBROW = "MASTER IN FINANCIAL ENGINEERING"
BRAND_NAME = "Multi-Market Financial Data Analysis Platform"


# ---------------------------------------------------------------------------------- helpers
def _label(text: str = "", name: str | None = None) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _field(placeholder: str, secret: bool = False) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setMinimumHeight(38)
    if secret:
        field.setEchoMode(QLineEdit.EchoMode.Password)  # Qt also disables copying from it
    return field


def _code_field() -> QLineEdit:
    field = QLineEdit()
    field.setMaxLength(6)
    field.setPlaceholderText("000000")
    field.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9]{0,6}"), field))
    field.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = QFont(field.font())
    font.setPixelSize(26)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
    field.setFont(font)
    field.setMinimumHeight(54)
    return field


def _button(text: str, primary: bool = True) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primary" if primary else "link")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        button.setMinimumHeight(40)
    return button


def qr_pixmap(data: str, size: int, ratio: float = 1.0) -> QPixmap:
    """Render `data` as a QR code with whole-pixel modules (sharp on Retina screens).
    Always black on white, whatever the theme: phone cameras need that contrast."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    image = QImage(n, n, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    black = QColor("black").rgb()
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                image.setPixel(x, y, black)
    target = max(n, int(size * ratio) // n * n)
    pixmap = QPixmap.fromImage(image.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.FastTransformation))
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


@lru_cache(maxsize=4)
def _logo_pixmap(size: int, ratio: float) -> QPixmap:
    """The MFE logo of the dashboard header, scaled once (the source image is 1254 px)."""
    image = QImage(str(style.LOGO_PATH))
    if image.isNull():  # logo missing: the pages work without it
        return QPixmap()
    side = round(size * ratio)
    pixmap = QPixmap.fromImage(image.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def _brand() -> QWidget:
    """Logo and platform name at the top of each sign-in card, as in the dashboard header."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    logo = QLabel()
    logo.setFixedSize(LOGO_SIZE, LOGO_SIZE)
    screen = QGuiApplication.primaryScreen()
    logo.setPixmap(_logo_pixmap(LOGO_SIZE, screen.devicePixelRatio() if screen else 2.0))
    row.addWidget(logo)
    words = QVBoxLayout()
    words.setSpacing(2)
    eyebrow = QLabel(BRAND_EYEBROW)
    eyebrow.setObjectName("brandEyebrow")
    font = QFont(eyebrow.font())
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
    eyebrow.setFont(font)
    name = _label(BRAND_NAME, "brandName")
    words.addStretch(1)
    words.addWidget(eyebrow)
    words.addWidget(name)
    words.addStretch(1)
    row.addLayout(words, 1)
    return box


@contextmanager
def busy():
    """Wait cursor while Argon2id works (a fraction of a second, on purpose)."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


# ------------------------------------------------------------------------------------ pages
class Page(QWidget):
    """Page with a centred card: platform logo, title, subtitle, fields and a message line."""

    def __init__(self, title: str, subtitle: str = "", width: int = 420) -> None:
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setFixedWidth(width)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(32, 26, 32, 24)
        self.body.setSpacing(10)
        self.body.addWidget(_brand())
        divider = QFrame()
        divider.setObjectName("divider")
        self.body.addSpacing(4)
        self.body.addWidget(divider)
        self.body.addSpacing(6)
        self.body.addWidget(_label(title, "title"))
        if subtitle:
            self.body.addWidget(_label(subtitle, "subtitle"))
        self.body.addSpacing(6)
        self.message = _label("", "error")
        self.message.hide()
        # Centre the card with stretches rather than alignment flags: this way Qt computes
        # its height from the real width, and wrapped text is never clipped.
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.card)
        row.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        self.first_field: QWidget | None = None

    def add(self, *widgets: QWidget) -> None:
        for widget in widgets:
            self.body.addWidget(widget)

    def show_message(self, text: str, kind: str = "error") -> None:
        self.message.setObjectName(kind)  # "error" (red) or "info" (blue)
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def clear(self) -> None:
        """Forget everything typed or shown (called whenever the page is left)."""
        self.show_message("")
        for field in self.findChildren(QLineEdit):
            field.clear()


class SetupPage(Page):
    submitted = Signal(str, str)  # username, password

    def __init__(self) -> None:
        super().__init__("Create the administrator",
                         f"First launch of {config.APP_NAME}. This account will manage the platform; "
                         "next you will protect it with two-factor authentication.")
        self.username = _field("Username")
        self.password = _field("Password", secret=True)
        self.confirm = _field("Repeat password", secret=True)
        hint = _label(f"At least {config.PASSWORD_MIN_LENGTH} characters. "
                      "A passphrase of a few words is ideal.", "hint")
        self.button = _button("Create account")
        self.add(self.username, self.password, self.confirm, hint, self.message, self.button)
        self.first_field = self.username
        self.username.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self.confirm.setFocus)
        self.confirm.returnPressed.connect(self._submit)
        self.button.clicked.connect(self._submit)

    def _submit(self) -> None:
        if self.password.text() != self.confirm.text():
            self.confirm.clear()
            self.show_message("The two passwords do not match.")
            self.confirm.setFocus()
            return
        self.submitted.emit(self.username.text(), self.password.text())


class LoginPage(Page):
    submitted = Signal(str, str)

    def __init__(self) -> None:
        super().__init__("Sign in", f"Welcome to {config.APP_NAME}. Step 1 of 2: username and password.")
        self.username = _field("Username")
        self.password = _field("Password", secret=True)
        self.button = _button("Continue")
        self.add(self.username, self.password, self.message, self.button)
        self.first_field = self.username
        self.username.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self._submit)
        self.button.clicked.connect(self._submit)

    def _submit(self) -> None:
        self.submitted.emit(self.username.text(), self.password.text())


class OtpPage(Page):
    code_entered = Signal(str)
    recovery_entered = Signal(str)
    back = Signal()

    def __init__(self) -> None:
        super().__init__("Two-factor authentication",
                         "Step 2 of 2: enter the 6-digit code shown by your authenticator app.")
        self.code = _code_field()
        self.recovery = _field("Recovery code (XXXX-XXXX-XXXX-XXXX)")
        self.recovery.hide()
        self.button = _button("Verify")
        self.toggle = _button("Use a recovery code instead", primary=False)
        self.back_button = _button("← Back to sign in", primary=False)
        self.add(self.code, self.recovery, self.message, self.button, self.toggle, self.back_button)
        self.first_field = self.code
        self._recovery_mode = False
        self.code.textChanged.connect(self._maybe_auto_submit)
        self.code.returnPressed.connect(self._submit)
        self.recovery.returnPressed.connect(self._submit)
        self.button.clicked.connect(self._submit)
        self.toggle.clicked.connect(lambda: self.set_recovery_mode(not self._recovery_mode))
        self.back_button.clicked.connect(lambda: self.back.emit())

    def _maybe_auto_submit(self, text: str) -> None:
        if len(text) == 6:  # no need to press Verify once the 6th digit is typed
            QTimer.singleShot(0, self._submit)

    def _submit(self) -> None:
        if self._recovery_mode:
            self.recovery_entered.emit(self.recovery.text())
        elif self.code.text():
            self.code_entered.emit(self.code.text())

    def set_recovery_mode(self, on: bool) -> None:
        self._recovery_mode = on
        self.code.setVisible(not on)
        self.recovery.setVisible(on)
        self.toggle.setText("Use the authenticator app instead" if on else "Use a recovery code instead")
        self.show_message("")
        (self.recovery if on else self.code).setFocus()

    def clear(self) -> None:
        super().clear()
        self.set_recovery_mode(False)


class EnrollPage(Page):
    code_entered = Signal(str)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__("Set up two-factor authentication",
                         "1. Open an authenticator app on your phone (Passwords on iPhone, "
                         "Google or Microsoft Authenticator…).\n2. Scan the QR code.\n"
                         "3. Type the 6-digit code it shows.", width=460)
        self.qr = QLabel()
        self.qr.setObjectName("qr")
        self.qr.setFixedSize(QR_SIZE, QR_SIZE)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        manual = _label("Can't scan it? Enter this key manually:", "hint")
        manual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.secret = _label("", "secret")
        self.secret.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.secret.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code = _code_field()
        self.button = _button("Activate")
        self.cancel = _button("Cancel", primary=False)
        self.body.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        self.add(manual, self.secret, self.code, self.message, self.button, self.cancel)
        self.first_field = self.code
        self.code.textChanged.connect(lambda t: len(t) == 6 and QTimer.singleShot(0, self._submit))
        self.code.returnPressed.connect(self._submit)
        self.button.clicked.connect(self._submit)
        self.cancel.clicked.connect(lambda: self.cancelled.emit())

    def set_secret(self, uri: str, secret: str) -> None:
        self.qr.setPixmap(qr_pixmap(uri, QR_SIZE, self.devicePixelRatioF()))
        self.secret.setText(" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)))

    def _submit(self) -> None:
        if self.code.text():
            self.code_entered.emit(self.code.text())

    def clear(self) -> None:
        super().clear()
        self.qr.clear()
        self.secret.clear()


class RecoveryCodesPage(Page):
    finished = Signal()

    def __init__(self) -> None:
        super().__init__("Save your recovery codes",
                         "If you lose your phone, each code lets you sign in once instead of the 6-digit "
                         "code. They will not be shown again: keep them somewhere safe, such as your "
                         "password manager.", width=460)
        self.codes = QPlainTextEdit()
        self.codes.setObjectName("codes")
        self.codes.setReadOnly(True)
        self.codes.setFixedHeight(8 * 22 + 24)
        self.copy = _button("Copy to clipboard", primary=False)
        self.ack = QCheckBox("I have stored these codes in a safe place")
        self.button = _button("Continue")
        self.button.setEnabled(False)
        self.add(self.codes, self.copy, self.ack, self.message, self.button)
        self.ack.toggled.connect(self.button.setEnabled)
        self.copy.clicked.connect(self._copy)
        self.button.clicked.connect(lambda: self.finished.emit())

    def set_codes(self, codes: list[str]) -> None:
        self.codes.setPlainText("\n".join(codes))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.codes.toPlainText())
        self.show_message("Copied. Paste them into your password manager.", "info")

    def clear(self) -> None:
        super().clear()
        self.codes.clear()
        self.ack.setChecked(False)


# ------------------------------------------------------------------------------- dashboard
class _TokenInterceptor(QWebEngineUrlRequestInterceptor):
    """Adds the session token to every request for the gateway, and only for the gateway:
    other addresses (map and font services used by the dashboard) never see it."""

    def __init__(self, gateway: DashboardGateway, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._gateway = gateway

    def interceptRequest(self, info) -> None:
        token = self._gateway.token
        if token and self._gateway.is_own_url(info.requestUrl().toString()):
            info.setHttpHeader(TOKEN_HEADER.encode("ascii"), token.encode("ascii"))


class _GuardedPage(QWebEnginePage):
    """The web page stays on the dashboard: no navigation to other sites; script errors
    go to app.log, where they can be read even in the packaged app."""

    def __init__(self, profile: QWebEngineProfile, gateway: DashboardGateway, parent: QObject) -> None:
        super().__init__(profile, parent)
        self._gateway = gateway

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        if not is_main_frame or url.scheme() in ("about", "data", "blob"):
            return True  # sub-frames, blank page, chart downloads
        return self._gateway.is_own_url(url.toString())

    def javaScriptConsoleMessage(self, level, message: str, line: int, source: str) -> None:
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            log.warning("Dashboard script error: %s (%s:%s)", message, source, line)


class DashboardView(QWebEngineView):
    """Web view that shows the dashboard. Its profile is off the record: no cache, cookies
    or history are written to disk, and everything is forgotten when the app closes."""

    file_saved = Signal(str)  # path of a chart image saved to Downloads

    def __init__(self, gateway: DashboardGateway, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The profile must outlive its page: dispose() deletes them in the right order.
        self._profile = QWebEngineProfile()
        self._interceptor = _TokenInterceptor(gateway, self._profile)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.downloadRequested.connect(self._save_download)
        page = _GuardedPage(self._profile, gateway, self)
        page.setBackgroundColor(QColor(style.COLORS["bg"]))  # navy while the page loads, no white flash
        self.setPage(page)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def open_dashboard(self, url: str) -> None:
        self.setUrl(QUrl(url))

    def dispose(self) -> None:
        """Delete the view with its page first, then the profile, as Qt WebEngine requires."""
        profile = self._profile
        shiboken6.delete(self)
        shiboken6.delete(profile)

    def close_dashboard(self) -> None:
        self.setUrl(QUrl("about:blank"))
        self.history().clear()
        self._profile.clearHttpCache()

    def _save_download(self, download) -> None:
        """Plotly's "download plot as png" button: save the image in the Downloads folder."""
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        download.setDownloadDirectory(folder)
        download.isFinishedChanged.connect(
            lambda: self.file_saved.emit(str(Path(download.downloadDirectory()) / download.downloadFileName())))
        download.accept()


class DashboardPage(QWidget):
    """After sign-in: the dashboard, with a thin bar showing who is signed in."""

    logout_requested = Signal()

    def __init__(self, gateway: DashboardGateway, view_factory=DashboardView) -> None:
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._gateway = gateway
        self._view_factory = view_factory
        self._who = ""
        self.view = None  # created at the first sign-in, then reused
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("sessionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 4, 10, 4)
        self.user_label = QLabel("")
        self.user_label.setObjectName("sessionUser")
        self.logout_button = QPushButton("Log Out")
        self.logout_button.setObjectName("sessionLogout")
        self.logout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Mouse only (or ⌘L): with keyboard focus, a space typed for the dashboard would log out.
        self.logout_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.logout_button.clicked.connect(lambda: self.logout_requested.emit())
        row.addWidget(self.user_label)
        row.addStretch(1)
        row.addWidget(self.logout_button)
        self._layout.addWidget(bar)

    def open(self, url: str, username: str, role: str) -> None:
        if self.view is None:
            self.view = self._view_factory(self._gateway, self)
            self._layout.insertWidget(0, self.view, 1)
            if hasattr(self.view, "file_saved"):
                self.view.file_saved.connect(self._show_saved)
        self._who = f"Signed in as {username} ({role})"
        self.user_label.setText(self._who)
        self.view.open_dashboard(url)
        self.view.setFocus()  # keys go to the dashboard straight away

    def _show_saved(self, path: str) -> None:
        self.user_label.setText(f"Chart saved: {path}")
        QTimer.singleShot(5000, lambda: self._who and self.user_label.setText(self._who))

    def clear(self) -> None:
        self._who = ""
        self.user_label.setText("")
        if self.view is not None:
            self.view.close_dashboard()

    def dispose(self) -> None:
        """Free the web engine when the window closes."""
        if self.view is not None and hasattr(self.view, "dispose"):
            self.view.dispose()
        self.view = None


# ------------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    SESSION_CHECK_MS = 5_000
    ACTIVITY_EVENTS = {QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel}

    def __init__(self, auth: AuthService, gateway: DashboardGateway, view_factory=DashboardView) -> None:
        super().__init__()
        self.auth = auth
        self.gateway = gateway
        self.session: Session | None = None
        self.pending: PendingLogin | PendingEnrollment | None = None
        self.setWindowTitle(config.APP_NAME)
        self.resize(1360, 860)
        self.setMinimumSize(900, 760)

        self.setup_page = SetupPage()
        self.login_page = LoginPage()
        self.otp_page = OtpPage()
        self.enroll_page = EnrollPage()
        self.codes_page = RecoveryCodesPage()
        self.dashboard_page = DashboardPage(gateway, view_factory)
        self.pages = (self.setup_page, self.login_page, self.otp_page, self.enroll_page, self.codes_page,
                      self.dashboard_page)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        self.setCentralWidget(self.stack)

        self.setup_page.submitted.connect(self._create_admin)
        self.enroll_page.code_entered.connect(self._confirm_enrollment)
        self.enroll_page.cancelled.connect(lambda: self._restart())
        self.codes_page.finished.connect(self._enter_dashboard)
        self.login_page.submitted.connect(self._login)
        self.otp_page.code_entered.connect(lambda code: self._second_factor(self.auth.verify_otp, code))
        self.otp_page.recovery_entered.connect(
            lambda code: self._second_factor(self.auth.verify_recovery_code, code))
        self.otp_page.back.connect(lambda: self._restart())
        self.dashboard_page.logout_requested.connect(lambda: self.logout("You have been signed out."))

        account_menu = self.menuBar().addMenu("&Account")  # on macOS: in the top menu bar
        self.logout_action = QAction("Log Out", self)
        self.logout_action.setShortcut(QKeySequence("Ctrl+L"))  # Qt maps Ctrl to ⌘ on macOS
        self.logout_action.triggered.connect(lambda: self.logout("You have been signed out."))
        account_menu.addAction(self.logout_action)

        self._session_timer = QTimer(self)
        self._session_timer.setInterval(self.SESSION_CHECK_MS)
        self._session_timer.timeout.connect(self._check_session)
        # Any key, click or scroll in this window counts as activity. The filter sits on the
        # window itself (QWindow), which receives every input before passing it to the page or
        # to the web view; an application-wide filter written in Python crashes Qt WebEngine.
        self.winId()  # creates the native window, so that windowHandle() exists
        self.windowHandle().installEventFilter(self)
        self._restart()

    # --- navigation ---------------------------------------------------------------------------
    def _show(self, page: QWidget) -> None:
        for other in self.pages:
            if other is not page and isinstance(other, Page):
                other.clear()
        if page is not self.dashboard_page:
            self.dashboard_page.clear()  # nothing of the dashboard stays on screen
        self.stack.setCurrentWidget(page)
        self.logout_action.setEnabled(page is self.dashboard_page)
        if isinstance(page, Page) and page.first_field is not None:
            QTimer.singleShot(0, page.first_field.setFocus)

    def _restart(self, message: str = "", kind: str = "info") -> None:
        """Back to step 1: account creation on first launch, otherwise the sign-in page."""
        self._close_pending()
        page = self.setup_page if self.auth.needs_setup() else self.login_page
        page.clear()
        self._show(page)
        page.show_message(message, kind)

    def _close_pending(self) -> None:
        if self.pending is not None:
            self.pending.close()
            self.pending = None

    # --- first launch -------------------------------------------------------------------------
    def _create_admin(self, username: str, password: str) -> None:
        try:
            with busy():
                pending = self.auth.create_admin(username, password)
        except AuthError as exc:
            self.setup_page.show_message(str(exc))
            return
        self._start_enrollment(pending)

    def _start_enrollment(self, pending: PendingEnrollment) -> None:
        self.pending = pending
        self.enroll_page.set_secret(pending.provisioning_uri, pending.totp_secret)
        self._show(self.enroll_page)

    def _confirm_enrollment(self, code: str) -> None:
        if self.pending is None or self.stack.currentWidget() is not self.enroll_page:
            return  # stray signal (e.g. Enter pressed right after the auto-submit)
        try:
            session, codes = self.auth.confirm_enrollment(self.pending, code)
        except RestartRequired as exc:
            self._restart(str(exc), "error")
            return
        except AuthError as exc:
            self.enroll_page.code.clear()
            self.enroll_page.show_message(str(exc))
            return
        self.pending = None
        self.session = session
        self.codes_page.set_codes(codes)
        self._show(self.codes_page)

    # --- sign-in ------------------------------------------------------------------------------
    def _login(self, username: str, password: str) -> None:
        try:
            with busy():
                pending = self.auth.login_password(username, password)
        except RestartRequired as exc:  # account locked
            self._restart(str(exc), "error")
            return
        except AuthError as exc:
            self.login_page.password.clear()
            self.login_page.show_message(str(exc))
            self.login_page.password.setFocus()
            return
        if isinstance(pending, PendingEnrollment):
            self._start_enrollment(pending)
        else:
            self.pending = pending
            self._show(self.otp_page)

    def _second_factor(self, verify, code: str) -> None:
        if self.pending is None or self.stack.currentWidget() is not self.otp_page:
            return  # stray signal (e.g. Enter pressed right after the auto-submit)
        try:
            session = verify(self.pending, code)
        except RestartRequired as exc:
            self._restart(str(exc), "error")
            return
        except AuthError as exc:
            self.otp_page.code.clear()
            self.otp_page.recovery.clear()
            self.otp_page.show_message(str(exc))
            return
        self.pending = None
        self.session = session
        self._enter_dashboard()

    # --- signed in ------------------------------------------------------------------------------
    def _enter_dashboard(self) -> None:
        if not self.auth.is_session_valid(self.session):
            self._restart("Please sign in again.", "error")
            return
        try:
            self.auth.open_dashboard(self.session)  # access control: the role needs "data.read"
        except PermissionDenied as exc:
            self.logout(str(exc), "error")
            return
        try:
            self.gateway.start()  # the dashboard is served only from the first sign-in on
        except OSError as exc:
            log.exception("Cannot start the dashboard gateway")
            QMessageBox.critical(self, config.APP_NAME, f"Cannot start the dashboard:\n{exc}")
            self.logout()
            return
        self.gateway.open_session()  # new token: valid for this session only
        self.dashboard_page.open(self.gateway.url, self.session.username, self.session.role)
        self._show(self.dashboard_page)
        self._session_timer.start()

    def logout(self, message: str = "", kind: str = "info") -> None:
        self._session_timer.stop()
        self.gateway.close_session()  # first of all: the dashboard stops answering
        self.dashboard_page.clear()
        if self.session is not None:
            self.auth.logout(self.session)
        self.session = None
        self._restart(message, kind)

    def _check_session(self) -> None:
        if self.session is not None and not self.auth.is_session_valid(self.session):
            self.logout("Your session expired after a period of inactivity. Please sign in again.")

    # --- Qt overrides -----------------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        session = getattr(self, "session", None)
        if session is not None and event.type() in self.ACTIVITY_EVENTS:
            if not self.auth.touch(session):
                QTimer.singleShot(0, self._check_session)
        return False  # never swallow the event

    def closeEvent(self, event) -> None:
        self.windowHandle().removeEventFilter(self)
        self._session_timer.stop()
        self.gateway.close_session()
        self.dashboard_page.clear()
        if self.session is not None:
            self.auth.logout(self.session)
        self._close_pending()
        self.dashboard_page.dispose()
        super().closeEvent(event)


# ---------------------------------------------------------------------------------------- app
def _apply_appearance(app: QApplication) -> None:
    """Same palette as the dashboard (style.py), whatever the macOS light/dark setting."""
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        hints.setColorScheme(Qt.ColorScheme.Dark)
    palette = QPalette()
    for role, color in style.QT_PALETTE.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(style.QT_STYLESHEET)


def _install_error_handler(log_file: Path) -> None:
    """Packaged apps have no console: write unexpected errors to a log file and say where."""
    logging.basicConfig(filename=log_file, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def handler(exc_type, exc, tb) -> None:
        logging.getLogger("secureapp").critical("Unhandled error", exc_info=(exc_type, exc, tb))
        if QApplication.instance() is not None:
            QMessageBox.critical(None, config.APP_NAME, f"Unexpected error: {exc}\n\nDetails: {log_file}")

    sys.excepthook = handler


def _fit_to_screen(window: QMainWindow) -> None:
    """Large enough for the dashboard, never larger than the screen."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry()
    width = min(1440, int(area.width() * 0.92))
    height = min(920, int(area.height() * 0.92))
    window.resize(max(width, window.minimumWidth()), max(height, window.minimumHeight()))
    frame = window.frameGeometry()
    frame.moveCenter(area.center())
    window.move(frame.topLeft())


def run(dashboard, argv: list[str] | None = None) -> int:
    """Start the desktop app. `dashboard` is the WSGI application shown after sign-in
    (main.py passes the Dash server); it is never reachable without signing in."""
    argv = list(sys.argv if argv is None else argv)
    data_dir = config.data_dir()
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _install_error_handler(data_dir / "app.log")
    app = QApplication(argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    _apply_appearance(app)
    try:
        store = Store(data_dir / "secureapp.db")
    except Exception as exc:  # e.g. permissions on the data folder
        log.exception("Cannot open the database")
        QMessageBox.critical(None, config.APP_NAME, f"Cannot open the database:\n{exc}")
        return 1
    gateway = DashboardGateway(dashboard)
    window = MainWindow(AuthService(store), gateway)
    _fit_to_screen(window)
    window.show()
    window.raise_()
    window.activateWindow()
    exit_code = app.exec()
    gateway.stop()
    store.close()
    return exit_code
