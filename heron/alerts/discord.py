"""Discord webhook client with per-category rate limiting.

Categories (from Project-HERON Section 12):
  debrief           — end-of-day summary
  proposal          — new strategy proposed
  promotion         — PAPER→LIVE recommendation
  cost_warning      — approaching monthly ceiling
  cost_trip         — hard cap reached, research halted
  drift             — reconciliation mismatch with broker
  review_reminder   — monthly review pending

Rate limit: one alert per category per `ALERT_RATE_LIMIT_MINUTES` (default 10).
State persisted to a JSON file so limits survive restarts.
"""

import json
import logging
import time
from pathlib import Path

import httpx

from heron.config import (
    DISCORD_WEBHOOK_URL, ALERT_RATE_LIMIT_MINUTES, ALERT_STATE_FILE,
    DASHBOARD_URL,
)

log = logging.getLogger(__name__)

CATEGORIES = (
    "debrief", "proposal", "promotion",
    "cost_warning", "cost_trip", "drift", "review_reminder",
    "test",
)


def _load_state():
    p = Path(ALERT_STATE_FILE)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state):
    p = Path(ALERT_STATE_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def _rate_limited(category, state, now=None):
    """True if this category is within its cooldown window."""
    last = state.get(category)
    if not last:
        return False
    now = now or time.time()
    return (now - last) < (ALERT_RATE_LIMIT_MINUTES * 60)


def send(category, content, *, webhook_url=None, force=False,
         embed=None, dry_run=False):
    """Post to Discord. Returns dict with status.

    status values:
      sent        — HTTP 2xx
      rate_limited — cooldown not elapsed
      no_webhook  — DISCORD_WEBHOOK_URL not configured
      error       — HTTP failure
      dry_run     — dry_run=True, nothing sent

    force=True bypasses the rate limiter (for hard alerts like cost_trip/drift).
    """
    if category not in CATEGORIES:
        raise ValueError(f"Unknown alert category: {category!r}")

    url = webhook_url if webhook_url is not None else DISCORD_WEBHOOK_URL
    if not url:
        log.debug(f"[{category}] no webhook configured")
        return {"status": "no_webhook", "category": category}

    state = _load_state()
    if not force and _rate_limited(category, state):
        log.info(f"[{category}] rate-limited")
        return {"status": "rate_limited", "category": category}

    payload = {"content": content[:1800]}  # Discord hard limit 2000
    if embed:
        payload["embeds"] = [embed]

    if dry_run:
        return {"status": "dry_run", "category": category, "payload": payload}

    try:
        r = httpx.post(url, json=payload, timeout=10.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning(f"[{category}] discord post failed: {e}")
        return {"status": "error", "category": category, "error": str(e)}

    state[category] = time.time()
    _save_state(state)
    return {"status": "sent", "category": category}


def reset(category=None):
    """Clear rate-limit state. Useful for tests or forced re-sends."""
    state = _load_state()
    if category:
        state.pop(category, None)
    else:
        state = {}
    _save_state(state)


def dashboard_link(path=""):
    base = DASHBOARD_URL.rstrip("/")
    return f"{base}/{path.lstrip('/')}" if path else base
