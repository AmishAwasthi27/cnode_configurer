"""cnode_configurer entry point."""
from __future__ import annotations
from rich.logging import RichHandler
from rich.console import Console
import logging

console = Console()

logging.basicConfig(
            level=logging.INFO,  # Set to DEBUG to capture all log levels
            format="[bold blue]%(name)s[/bold blue] [bold green]%(threadName)s[/bold green] | %(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(
                markup=True,
                console=console,
                show_time=True,
                show_level=True,
                show_path=True,
                rich_tracebacks=True  # Enable rich tracebacks for exceptions
            )]
        )

import sys
from pathlib import Path

# Make the sibling `cnode_manager_sdk/` package importable without needing a
# `pip install -e ..`. The SDK is the canonical HTTP client for the
# cnode_manager service and is used throughout the managers + pages.
_REPO_ROOT = Path(__file__).resolve().parent
_SDK_PATH = _REPO_ROOT / "cnode_manager_sdk"
if _SDK_PATH.exists() and str(_SDK_PATH) not in sys.path:
    sys.path.insert(0, str(_SDK_PATH))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.constants import APP_NAME, APP_ORG
from app.main_window import MainWindow
from app.theme import apply_theme

def main() -> int:
    start_live_detection = "--live-detection" in sys.argv[1:]
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    logger = logging.getLogger("cnode_configurer")
    logger.info("Starting cnode_configurer")
    # Keep app-specific flags from being interpreted as Qt command-line options.
    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setWindowIcon(QIcon())

    apply_theme(app)

    window = MainWindow()
    window.show()
    if start_live_detection:
        QTimer.singleShot(0, window.start_live_detection)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
