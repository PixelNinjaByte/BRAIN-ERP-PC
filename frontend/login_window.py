"""Brain ERP — Login screen (desktop)."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QLabel,
)

from .api_client import ApiClient, ApiError


class LoginWindow(QWidget):
    logged_in = pyqtSignal(dict)  # session payload from ApiClient.login()

    def __init__(self, api: ApiClient):
        super().__init__()
        self.api = api
        self.setWindowTitle("Brain ERP — Sign in")
        self._busy = False
        # Width only: a fixed height clipped long error messages (e.g. connection errors)
        # because the word-wrapped error label needs more room than 260px allows.
        self.setFixedWidth(380)
        self._build_ui()

    def _build_ui(self) -> None:
        title = QLabel("Brain ERP")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #2563eb;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Sign in to continue")
        subtitle.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.username = QLineEdit()
        self.username.setPlaceholderText("admin")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.username.returnPressed.connect(self._do_login)
        self.password.returnPressed.connect(self._do_login)

        form = QFormLayout()
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626;")
        self.error_label.setWordWrap(True)

        self.login_btn = QPushButton("Sign in")
        self.login_btn.setDefault(True)
        self.login_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; padding: 8px; border-radius: 6px; font-weight: 600; }"
            "QPushButton:disabled { background: #93c5fd; }"
        )
        self.login_btn.clicked.connect(self._do_login)

        layout = QVBoxLayout()
        layout.setContentsMargins(28, 24, 28, 24)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addSpacing(4)
        layout.addWidget(self.login_btn)
        self.setLayout(layout)
        self.username.setFocus()

    def clear(self) -> None:
        self.username.clear()
        self.password.clear()
        self.show_error("")
        self.username.setFocus()

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.adjustSize()  # grow/shrink to fit the wrapped message

    def _do_login(self) -> None:
        if self._busy:  # Enter pressed again while a request is in flight
            return
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            self.show_error("Enter both username and password.")
            return

        self._busy = True
        self.login_btn.setEnabled(False)
        self.login_btn.setText("Signing in…")
        self.show_error("")
        # The request is synchronous; let Qt paint the disabled button first,
        # otherwise the UI just appears frozen with no feedback.
        QApplication.processEvents()

        session = None
        try:
            session = self.api.login(username, password)
            if not isinstance(session, dict):
                raise ApiError("Unexpected response from the server.")
        except ApiError as e:
            self.show_error(str(e))
            session = None
        finally:
            self._busy = False
            self.login_btn.setEnabled(True)
            self.login_btn.setText("Sign in")

        # Emit OUTSIDE the try: the slot builds the main window, which makes API calls.
        # Inside the try, an ApiError from that was shown here as a *login* error while
        # the half-built main window was left behind.
        if session is not None:
            self.password.clear()  # don't keep the password in the hidden window
            self.logged_in.emit(session)