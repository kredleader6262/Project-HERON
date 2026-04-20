"""Graceful shutdown (M15) — install signal handlers, snapshot state.

On SIGTERM/SIGINT: log shutdown event with snapshot of open work so startup
audit on next launch has a baseline to compare against.
"""

import json
import logging
import signal
import sys
from datetime import datetime, timezone

from heron.journal.ops import log_event
from heron.journal.trades import list_trades

log = logging.getLogger(__name__)

_installed = False


def snapshot_state(conn):
    """Capture minimal state snapshot for shutdown event."""
    open_trades = list_trades(conn, open_only=True)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "open_trades": [
            {"id": t["id"], "ticker": t["ticker"],
             "fill_qty": t["fill_qty"], "fill_price": t["fill_price"],
             "stop_price": t["stop_price"]}
            for t in open_trades
        ],
        "open_count": len(open_trades),
    }


def log_shutdown(conn, reason="signal"):
    """Log a shutdown event with a snapshot. Safe to call from signal handler."""
    try:
        snap = snapshot_state(conn)
        log_event(
            conn, "shutdown_graceful",
            f"Graceful shutdown: {reason} ({snap['open_count']} open trade(s))",
            severity="warning", source="resilience",
            details_json=json.dumps(snap),
        )
        log.info(f"Shutdown logged: {reason}")
    except Exception as e:
        # Best-effort; never raise from a signal handler
        log.error(f"Shutdown log failed: {e}")


def install_signal_handlers(conn, exit_on_signal=True):
    """Register SIGINT/SIGTERM handlers that log and optionally exit.

    Idempotent — safe to call multiple times.
    """
    global _installed
    if _installed:
        return
    _installed = True

    def _handler(signum, frame):
        name = signal.Signals(signum).name
        log_shutdown(conn, reason=name)
        if exit_on_signal:
            sys.exit(0)

    # SIGTERM may not exist on Windows console (available in 3.11+ for most cases)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, AttributeError, OSError) as e:
            log.debug(f"Could not install handler for {sig}: {e}")
