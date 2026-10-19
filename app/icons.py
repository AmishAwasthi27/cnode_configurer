"""Monochromatic SVG icon library and QIcon loader.

All icons are defined inline as 24x24 outline SVGs using `currentColor`. A tiny
helper renders them into a `QIcon` (optionally tinted/resized) via `QSvgRenderer`,
so icons always match the current text colour in the UI.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


# ---------------------------------------------------------------------------
# SVG library (24x24 viewBox, monochrome, stroke-based).
# ---------------------------------------------------------------------------

_SVG_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
    'stroke-linejoin="round">'
)
_SVG_TAIL = "</svg>"


def _svg(body: str) -> str:
    return f"{_SVG_HEAD}{body}{_SVG_TAIL}"


_ICON_SOURCES: Dict[str, str] = {
    # Logo: hex/node-like
    "logo": _svg(
        '<polygon points="12,3 21,8 21,16 12,21 3,16 3,8" />'
        '<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>'
    ),

    # Navigation
    "dashboard": _svg(
        '<rect x="3" y="3" width="8" height="8" rx="1.5"/>'
        '<rect x="13" y="3" width="8" height="5" rx="1.5"/>'
        '<rect x="13" y="10" width="8" height="11" rx="1.5"/>'
        '<rect x="3" y="13" width="8" height="8" rx="1.5"/>'
    ),
    "configure": _svg(
        '<circle cx="6" cy="6" r="2.5"/>'
        '<path d="M10 6h10"/>'
        '<circle cx="18" cy="12" r="2.5"/>'
        '<path d="M4 12h12"/>'
        '<circle cx="8" cy="18" r="2.5"/>'
        '<path d="M12 18h8"/>'
    ),
    "workflows": _svg(
        '<rect x="3" y="4" width="6" height="5" rx="1"/>'
        '<rect x="15" y="4" width="6" height="5" rx="1"/>'
        '<rect x="9" y="15" width="6" height="5" rx="1"/>'
        '<path d="M6 9v3h12V9"/>'
        '<path d="M12 12v3"/>'
    ),
    "profiles": _svg(
        '<path d="M12 3l9 5-9 5-9-5 9-5z"/>'
        '<path d="M3 13l9 5 9-5"/>'
        '<path d="M3 18l9 5 9-5"/>'
    ),
    "cameras": _svg(
        '<rect x="3" y="7" width="18" height="13" rx="2"/>'
        '<circle cx="12" cy="13.5" r="3.5"/>'
        '<path d="M8 7l1.5-3h5L16 7"/>'
    ),
    "updates": _svg(
        '<path d="M12 3v12"/>'
        '<path d="M7 10l5 5 5-5"/>'
        '<path d="M4 20h16"/>'
    ),

    # Topbar
    "connection": _svg(
        '<path d="M10 14l-3 3a3.5 3.5 0 11-5-5l3-3"/>'
        '<path d="M14 10l3-3a3.5 3.5 0 115 5l-3 3"/>'
        '<path d="M9 15l6-6"/>'
    ),
    "mode": _svg(
        '<rect x="2" y="7" width="20" height="10" rx="5"/>'
        '<circle cx="8" cy="12" r="2.5" fill="currentColor" stroke="none"/>'
    ),
    "bell": _svg(
        '<path d="M6 8a6 6 0 0112 0v4l1.5 3h-15L6 12V8z"/>'
        '<path d="M10 18a2 2 0 004 0"/>'
    ),
    "user": _svg(
        '<circle cx="12" cy="8" r="4"/>'
        '<path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6"/>'
    ),

    # Generic actions
    "plus": _svg('<path d="M12 5v14M5 12h14"/>'),
    "close": _svg('<path d="M6 6l12 12M18 6L6 18"/>'),
    "save": _svg(
        '<path d="M5 12l5 5L20 6"/>'
    ),
    "trash": _svg(
        '<path d="M4 7h16"/>'
        '<path d="M9 7V4h6v3"/>'
        '<path d="M6 7l1 13h10l1-13"/>'
        '<path d="M10 11v5M14 11v5"/>'
    ),
    "edit": _svg(
        '<path d="M4 20h4l11-11-4-4L4 16v4z"/>'
        '<path d="M14 6l4 4"/>'
    ),
    "refresh": _svg(
        '<path d="M20 12a8 8 0 10-3 6.2"/>'
        '<path d="M20 4v6h-6"/>'
    ),
    "settings": _svg(
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19 12a7 7 0 00-.2-1.6l2-1.5-2-3.4-2.3.9a7 7 0 00-2.8-1.6L13.3 2h-2.6l-.4 2.8A7 7 0 007.5 6.4L5.2 5.5 3.2 8.9l2 1.5A7 7 0 005 12c0 .5.1 1.1.2 1.6l-2 1.5 2 3.4 2.3-.9a7 7 0 002.8 1.6l.4 2.8h2.6l.4-2.8a7 7 0 002.8-1.6l2.3.9 2-3.4-2-1.5c.1-.5.2-1.1.2-1.6z"/>'
    ),
    "help": _svg(
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M9 9a3 3 0 116 0c0 2-3 2.5-3 4.5"/>'
        '<circle cx="12" cy="17" r="0.9" fill="currentColor" stroke="none"/>'
    ),
    "info": _svg(
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M12 11v6"/>'
        '<circle cx="12" cy="8" r="0.9" fill="currentColor" stroke="none"/>'
    ),
    "crop": _svg(
        '<path d="M7 3v14h14"/>'
        '<path d="M3 7h14v14"/>'
    ),
    "camera-snap": _svg(
        '<rect x="3" y="6" width="18" height="14" rx="2"/>'
        '<circle cx="12" cy="13" r="4"/>'
        '<path d="M8 6l2-3h4l2 3"/>'
        '<circle cx="18" cy="9.5" r="0.9" fill="currentColor" stroke="none"/>'
    ),
    "download": _svg(
        '<path d="M12 4v11"/>'
        '<path d="M7 10l5 5 5-5"/>'
        '<path d="M4 20h16"/>'
    ),
    "search": _svg(
        '<circle cx="11" cy="11" r="6"/>'
        '<path d="M20 20l-4-4"/>'
    ),

    # Flow chart indicators
    "arrow-right": _svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
    "chevron-down": _svg('<path d="M6 9l6 6 6-6"/>'),
    "chevron-right": _svg('<path d="M9 6l6 6-6 6"/>'),

    # Workflow node markers (numbered / role)
    "trigger": _svg('<path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/>'),
    "detect": _svg(
        '<circle cx="11" cy="11" r="6"/>'
        '<path d="M20 20l-4-4"/>'
        '<path d="M11 8v6M8 11h6"/>'
    ),
    "relay": _svg(
        '<rect x="3" y="8" width="18" height="8" rx="2"/>'
        '<circle cx="8" cy="12" r="1.8" fill="currentColor" stroke="none"/>'
        '<path d="M12 12h6"/>'
    ),
}


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

DEFAULT_COLOR = "#374151"  # slate-700 (matches body text)
WHITE = "#FFFFFF"


@lru_cache(maxsize=512)
def _render_pixmap(name: str, color: str, size: int) -> QPixmap:
    source = _ICON_SOURCES.get(name)
    if source is None:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        return pm
    tinted = source.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(tinted.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pm


def icon(name: str, color: str = DEFAULT_COLOR, size: int = 20) -> QIcon:
    """Return a `QIcon` for the named SVG, tinted to ``color``."""
    ic = QIcon()
    ic.addPixmap(_render_pixmap(name, color, size))
    # High-DPI variants
    ic.addPixmap(_render_pixmap(name, color, size * 2))
    return ic


def pixmap(name: str, color: str = DEFAULT_COLOR, size: int = 20) -> QPixmap:
    """Return a `QPixmap` for the named SVG, tinted to ``color``."""
    return _render_pixmap(name, color, size)


def default_icon_size(px: int = 16) -> QSize:
    return QSize(px, px)
