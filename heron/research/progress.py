"""Lightweight progress helpers — spinner + stage timing for long LLM calls.

Writes to stderr so it doesn't pollute captured stdout. Silent when stderr
isn't a tty (e.g. inside tests).
"""

import sys
import threading
import time


class Spinner:
    """Context manager that shows a live elapsed-time ticker on stderr.

    Usage:
        with Spinner("Classifying batch 1/3") as sp:
            ...                       # sp.update(extra="42 tokens")
        # prints "✓ Classifying batch 1/3 — 12.3s" on exit
    """

    _frames = "|/-\\"

    def __init__(self, label, interval=0.25, stream=None):
        self.label = label
        self.interval = interval
        self.stream = stream or sys.stderr
        self._extra = ""
        self._stop = threading.Event()
        self._thread = None
        self._t0 = None
        self._active = False

    def _is_tty(self):
        try:
            return self.stream.isatty()
        except (AttributeError, ValueError):
            return False

    def _loop(self):
        i = 0
        while not self._stop.is_set():
            elapsed = time.monotonic() - self._t0
            extra = f" — {self._extra}" if self._extra else ""
            msg = f"\r{self._frames[i % 4]} {self.label} ({elapsed:5.1f}s){extra}   "
            try:
                self.stream.write(msg)
                self.stream.flush()
            except (OSError, ValueError):
                # Stream closed or unwritable — exit spinner loop cleanly
                return
            i += 1
            self._stop.wait(self.interval)

    def update(self, extra=""):
        self._extra = extra

    def __enter__(self):
        self._t0 = time.monotonic()
        if self._is_tty():
            self._active = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        else:
            # Non-tty: just print start line
            self.stream.write(f"  {self.label}...\n")
            self.stream.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.monotonic() - self._t0
        if self._active:
            self._stop.set()
            self._thread.join(timeout=1.0)
            # Clear line + final
            mark = "✗" if exc_type else "✓"
            extra = f" — {self._extra}" if self._extra else ""
            self.stream.write(f"\r{mark} {self.label} ({elapsed:.1f}s){extra}           \n")
            self.stream.flush()
        else:
            mark = "✗" if exc_type else "✓"
            self.stream.write(f"  {mark} {self.label} done in {elapsed:.1f}s\n")
            self.stream.flush()
        return False
