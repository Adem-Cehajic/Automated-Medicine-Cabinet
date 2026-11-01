from __future__ import annotations

SHOW_ALL_ACTIONS = False

WINDOW_TITLE = "Automated Medicine Cabinet"
CABINET_ID = "ADC-001"

COLORS = {
    "background": "#f2f5f8",
    "surface": "#ffffff",
    "text": "#17212b",
    "muted": "#43515e",
    "primary": "#124c78",
    "primary_text": "#ffffff",
    "danger": "#8f1d1d",
    "warning": "#734700",
    "success": "#14532d",
    "focus": "#ffbf47",
}

FONTS = {
    "base": ("Segoe UI", 14),
    "heading": ("Segoe UI", 22, "bold"),
    "button": ("Segoe UI", 18, "bold"),
    "small": ("Segoe UI", 14),
}

SOUND_PATTERNS = {
    "low_stock": [(660, 120), (660, 120)],
    "fault": [(1050, 100), (1050, 100), (1050, 100)],
    "communication_loss": [(330, 700)],
}
