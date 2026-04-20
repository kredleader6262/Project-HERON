"""Central logging setup for HERON.

Console + rotating file handlers. ISO-8601 UTC timestamps.
Severity prefix so tail-grepping errors is trivial.
Never logs secrets — callers are responsible for not passing tokens.

Usage:
    from heron.logging_setup import setup_logging
    setup_logging()            # INFO to console, DEBUG to file
    setup_logging(level="DEBUG")  # DEBUG to both
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOGS_DIR = Path(os.environ.get("HERON_LOGS_DIR", "logs"))
LOG_FILE = LOGS_DIR / "heron.log"
ERROR_FILE = LOGS_DIR / "heron.error.log"

# ISO-8601 UTC with millisecond precision
_FMT = "%(asctime)s.%(msecs)03dZ %(levelname)-7s %(name)s :: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


class _UTCFormatter(logging.Formatter):
    """Formatter that forces UTC in asctime."""
    converter = __import__("time").gmtime


_configured = False


def setup_logging(level=None, *, file_level="DEBUG", quiet_libs=True):
    """Install console + rotating file handlers on the root logger.

    Idempotent — safe to call more than once. Second call reconfigures levels.

    Args:
        level: Console log level. Defaults to env HERON_LOG_LEVEL or INFO.
        file_level: File log level (default DEBUG — keeps full trail).
        quiet_libs: Suppress noisy 3rd-party loggers to WARNING.
    """
    global _configured
    level = (level or os.environ.get("HERON_LOG_LEVEL") or "INFO").upper()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # gate at handler level

    fmt = _UTCFormatter(_FMT, datefmt=_DATEFMT)

    if _configured:
        # Re-adjust existing handler levels
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(level)
        return

    # Console — stderr so stdout stays machine-readable
    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Rotating main file — 5MB x 5 files
    try:
        main_fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        main_fh.setLevel(file_level)
        main_fh.setFormatter(fmt)
        root.addHandler(main_fh)

        err_fh = logging.handlers.RotatingFileHandler(
            ERROR_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        err_fh.setLevel(logging.WARNING)
        err_fh.setFormatter(fmt)
        root.addHandler(err_fh)
    except OSError as e:
        # Read-only filesystem or permission issue — continue with console only
        logging.getLogger(__name__).warning(f"File logging disabled: {e}")

    if quiet_libs:
        for noisy in ("httpx", "httpcore", "urllib3", "werkzeug",
                      "apscheduler", "anthropic", "alpaca"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).info(
        f"Logging configured: console={level}, file={file_level}, dir={LOGS_DIR}"
    )
