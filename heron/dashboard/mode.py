"""Paper/Live/All mode — global filter driven by a cookie.

One template per page. Every query asks the filter for its SQL shape,
so there's a single source of truth for what "paper view" vs "live view"
vs "all view" means.
"""

from flask import request

COOKIE = "heron_mode"
MODES = ("paper", "live", "all")
DEFAULT = "paper"  # safety-first default


def get_mode(req=None):
    """Resolve current mode from the cookie. Falls back to DEFAULT."""
    req = req or request
    value = (req.cookies.get(COOKIE) or "").lower()
    return value if value in MODES else DEFAULT


def strategy_states(mode):
    """Strategy states visible in this mode.

    paper → PAPER (running + pending approval from paper pool)
    live  → LIVE
    all   → every state
    """
    if mode == "paper":
        return ("PAPER", "PROPOSED")
    if mode == "live":
        return ("LIVE",)
    return ("PROPOSED", "PAPER", "LIVE", "RETIRED")


def trade_mode(mode):
    """Value for the trades.mode column filter, or None for no filter."""
    if mode == "paper":
        return "paper"
    if mode == "live":
        return "live"
    return None


def in_clause(values):
    """Build a sqlite IN (?, ?, …) clause + params tuple."""
    placeholders = ",".join("?" * len(values))
    return f"IN ({placeholders})", tuple(values)


def accent(mode):
    """CSS accent hex for the current mode (also used client-side via var)."""
    return {"paper": "#5eaaff", "live": "#ff6b6b", "all": "#9aa3b5"}[mode]


def label(mode):
    return {"paper": "Paper", "live": "Live", "all": "All"}[mode]
