"""Brain ERP — owns the single ApiClient instance and swaps LoginWindow <-> MainWindow."""

import sys
import traceback

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication, QMessageBox

from .api_client import ApiClient
from .login_window import LoginWindow
from .main_window import MainWindow

_STYLESHEET = """
QWidget {
    font-size: 13px;
    color: #111827;
}

QGroupBox {
    font-weight: 600;
    margin-top: 10px;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    padding-top: 10px;
    color: #111827;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #111827;
}

QLabel {
    color: #111827;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    padding: 5px 8px;
    border: 1px solid #d1d5db;
    border-radius: 4px;
    background: #ffffff;
    color: #111827;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1.5px solid #2563eb;
}

QComboBox {
    padding: 5px 8px;
    border: 1px solid #d1d5db;
    border-radius: 4px;
    background: #ffffff;
    color: #111827;
}

QComboBox QAbstractItemView {
    background: #ffffff;
    color: #111827;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QCheckBox {
    color: #111827;
}

QTabBar::tab {
    padding: 8px 16px;
    color: #111827;
}

QTabBar::tab:selected {
    color: #2563eb;
    font-weight: 600;
}

QTableWidget {
    gridline-color: #e5e7eb;
    background: #ffffff;
    color: #111827;
}

QTableWidget::item {
    color: #111827;
}

QHeaderView::section {
    background: #f9fafb;
    color: #111827;
    font-weight: 600;
    border: none;
    border-bottom: 1px solid #e5e7eb;
    padding: 6px;
}
"""


class AppController:
    def __init__(self, base_url: str):
        self.api = ApiClient(base_url)
        self.api.on_unauthorized = self._on_unauthorized
        self.main_window: MainWindow | None = None
        self.login_window = LoginWindow(self.api)
        self.login_window.logged_in.connect(self._on_logged_in)
        self.login_window.show()

    def _on_logged_in(self, session: dict) -> None:
        try:
            win = MainWindow(self.api, session)
        except Exception as e:  # building the UI makes API calls; don't strand the user
            traceback.print_exc()
            self.api.clear_session()
            self.login_window.show_error(f"Could not open the main window: {e}")
            return
        self.main_window = win
        win.logged_out.connect(self._on_logged_out)
        win.show()
        self.login_window.close()

    def _on_logged_out(self, message: str = "") -> None:
        win, self.main_window = self.main_window, None
        self.api.clear_session()
        # Show the login window BEFORE closing the main window. If main_window closes
        # first there is momentarily no visible window, Qt's quit-on-last-window-closed
        # fires, and the whole app exits instead of returning to the login screen.
        self.login_window.clear()
        if message:
            self.login_window.show_error(message)
        self.login_window.show()
        self.login_window.raise_()
        if win:
            win.close()
            win.deleteLater()

    def _on_unauthorized(self) -> None:
        # Called from inside an API request made by a widget slot: defer the teardown
        # so we don't destroy that widget while its own method is still running.
        QTimer.singleShot(0, self._expire_session)

    def _expire_session(self) -> None:
        if self.main_window is None:  # already logged out / already handled
            return
        self._on_logged_out("Your session has expired. Please sign in again.")


_showing_error = False


def _excepthook(exc_type, exc, tb) -> None:
    """PyQt6 calls abort() on an exception that escapes a slot, killing the app and
    losing any unsaved form. Log it and show a dialog instead."""
    global _showing_error
    traceback.print_exception(exc_type, exc, tb)
    if _showing_error or QApplication.instance() is None:
        return
    _showing_error = True
    try:
        QMessageBox.critical(None, "Brain ERP", f"Something went wrong:\n\n{exc_type.__name__}: {exc}")
    finally:
        _showing_error = False


def run_frontend(base_url: str) -> int:
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName("Brain ERP")
    app.setStyle("Fusion")

    # Enforce standard light-theme palette to prevent OS dark mode from injecting white text
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f1f5f9"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111827"))
    app.setPalette(palette)

    app.setStyleSheet(_STYLESHEET)
    controller = AppController(base_url)
    app.aboutToQuit.connect(controller.api.close)
    return app.exec()