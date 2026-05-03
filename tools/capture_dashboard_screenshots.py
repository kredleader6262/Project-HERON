"""
HERON dashboard screenshot capture.

Captures the current state of every dashboard route across desktop, mobile, and
tablet viewports. Saves to docs/screenshots/<page>/v4_refactor_before_<viewport>_<page>.jpg.

Designed as a "before" snapshot of the v4 refactor's state at a given point in
time. Run this before a stage that changes UI; the matching "after" capture is
a manual rerun with a renamed prefix.

Approach:
1. Boot the Flask app in a child process on a free port.
2. Discover routes via the app's url_map.
3. For each route, decide: skip (POST-only, downloads, etc.), capture as-is
   (no parameters), or substitute one sample value (parameterized detail routes).
4. For each capturable route, screenshot at 1440x900, 820x1180, and 390x844.
5. Save full-page JPGs to docs/screenshots/<page-slug>/.

Requirements:
    pip install -e ".[dev]"
    python -m playwright install chromium

Usage:
    python tools/capture_dashboard_screenshots.py
    python tools/capture_dashboard_screenshots.py --prefix v5_refactor_before
    python tools/capture_dashboard_screenshots.py --routes-file my_routes.txt
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# --- Configuration ----------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS_ROOT = REPO_ROOT / "docs" / "screenshots"

# Routes that should never be captured. Add patterns here for POST endpoints,
# downloads, redirects, etc. Matched against the rule string.
SKIP_RULE_PATTERNS = [
    r"^/static/",        # static assets
    r"/<path:",          # generic catch-all routes
]

# HTTP methods we consider capturable. GET only -- POST/PUT/DELETE require form
# data and would either fail or mutate state.
CAPTURABLE_METHODS = {"GET", "HEAD"}

# Viewports to capture. Keep names short -- they appear in filenames.
VIEWPORTS = [
    ("desktop", 1440, 900),
    ("tablet", 820, 1180),
    ("mobile", 390, 844),
]

# How long to wait after navigation before screenshotting. The dashboard uses
# server-rendered Flask + a little HTMX, so this is mostly about giving HTMX
# fragments and any deferred JS a chance to settle.
SETTLE_MS = 600

# How long to wait for the Flask app to become reachable before giving up.
APP_BOOT_TIMEOUT_S = 30

# Default filename prefix. Override with --prefix.
DEFAULT_PREFIX = "v4_refactor_before"


# --- Route model ------------------------------------------------------------


@dataclass
class CapturePlan:
    """A concrete URL to capture, plus the page slug used for filenames."""

    rule: str          # e.g. "/desk/<id>" or "/desks"
    url_path: str      # e.g. "/desk/default" or "/desks"
    slug: str          # e.g. "desk_detail" or "desks"


# --- Route discovery --------------------------------------------------------


def discover_routes(app) -> list[CapturePlan]:
    """Pull every GET-able rule from the Flask app and resolve detail routes
    to a single sample URL each."""

    plans: list[CapturePlan] = []
    seen_slugs: set[str] = set()

    for rule in app.url_map.iter_rules():
        rule_str = str(rule)

        # Skip filtered patterns
        if any(re.search(pat, rule_str) for pat in SKIP_RULE_PATTERNS):
            continue

        # Skip non-GET endpoints
        if not (rule.methods or set()) & CAPTURABLE_METHODS:
            continue

        # Skip rules with HTTP methods that mutate (action endpoints)
        # These are already filtered by method above, but action routes
        # often nest under detail routes -- skip if path looks action-like.
        if re.search(r"/(approve|reject|accept|promote|retire|action|cancel)(?:/|$)", rule_str):
            continue
        if re.search(r"/(fetch|snapshot|run|override|send|reset|test)$", rule_str):
            continue

        # Has parameters? Try to substitute. Only one sample per pattern.
        if rule.arguments:
            sample = sample_url_for_rule(rule_str, rule.arguments)
            if sample is None:
                continue  # couldn't find a sample, skip
            url_path = sample
        else:
            url_path = rule_str

        slug = slug_from_rule(rule_str)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        plans.append(CapturePlan(rule=rule_str, url_path=url_path, slug=slug))

    plans.sort(key=lambda p: p.slug)
    return plans


def slug_from_rule(rule: str) -> str:
    """Turn '/desk/<campaign_id>' into 'desk_detail', '/desks' into 'desks',
    '/' into 'index'."""
    if rule == "/":
        return "index"
    s = rule.lstrip("/")
    s = re.sub(r"<[^>]+>", "detail", s)
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    s = s.strip("_").lower()
    return s or "index"


def sample_url_for_rule(rule: str, arguments: Iterable[str]) -> str | None:
    """Substitute a sample value for each rule argument. Returns None if any
    argument can't be resolved."""

    args = list(arguments)
    url = rule

    for arg in args:
        sample = resolve_sample_for_arg(arg)
        if sample is None:
            return None
        # Replace any of <arg>, <int:arg>, <string:arg>, etc.
        url = re.sub(r"<[^>]*?\b" + re.escape(arg) + r"\b[^>]*?>", str(sample), url)

    return url


def resolve_sample_for_arg(arg: str) -> str | int | None:
    """Pick a sample value for a route argument by inspecting the journal DB
    if available. Falls back to literals for known argument names."""

    # Hardcoded fallbacks for well-known args
    HARDCODED = {
        "m": "paper",
        "mode": "paper",
        "action": "view",
    }
    if arg in HARDCODED:
        return HARDCODED[arg]

    # Try to pull a real ID from SQLite
    db_path = locate_journal_db()
    if db_path is not None:
        sample = sample_from_db(db_path, arg)
        if sample is not None:
            return sample

    # Last-resort fallbacks
    LITERAL_FALLBACKS = {
        "campaign_id": "default",
        "desk_id": "default",
        "strategy_id": "demo",
        "candidate_id": 1,
        "report_id": 1,
        "sweep_id": 1,
        "job_id": "demo",
        "job": "demo",
    }
    return LITERAL_FALLBACKS.get(arg)


def locate_journal_db() -> Path | None:
    """Find the SQLite journal database. Look in common spots."""
    candidates = [
        REPO_ROOT / "heron.db",
        REPO_ROOT / "data" / "heron.db",
        REPO_ROOT / "var" / "heron.db",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def sample_from_db(db_path: Path, arg: str) -> str | int | None:
    """Open the DB read-only and pick a real ID for the given argument name."""

    QUERIES = {
        "campaign_id": ("SELECT id FROM campaigns ORDER BY created_at DESC LIMIT 1", str),
        "desk_id":     ("SELECT id FROM campaigns ORDER BY created_at DESC LIMIT 1", str),
        "strategy_id": ("SELECT id FROM strategies ORDER BY created_at DESC LIMIT 1", str),
        "candidate_id":("SELECT id FROM candidates ORDER BY created_at DESC LIMIT 1", int),
        "signal_id":   ("SELECT id FROM signals ORDER BY created_at DESC LIMIT 1", int),
        "trade_id":    ("SELECT id FROM trades ORDER BY entry_filled_at DESC LIMIT 1", int),
        "report_id":   ("SELECT id FROM backtest_reports ORDER BY created_at DESC LIMIT 1", int),
        "sweep_id":    ("SELECT id FROM backtest_sweeps ORDER BY created_at DESC LIMIT 1", int),
    }
    query_spec = QUERIES.get(arg)
    if query_spec is None:
        return None

    sql, caster = query_spec
    try:
        # Read-only URI form so we don't accidentally mutate or lock writes.
        uri = f"file:{db_path}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            row = conn.execute(sql).fetchone()
            if row and row[0] is not None:
                return caster(row[0])
    except sqlite3.Error:
        # Table might not exist yet (e.g., signals before Stage 5)
        return None
    return None


# --- Flask app boot ---------------------------------------------------------


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def boot_flask_app(port: int) -> subprocess.Popen:
    """Start the Flask app as a child process and wait for it to respond."""

    env = os.environ.copy()
    env["FLASK_APP"] = "heron.dashboard:create_app"
    env["FLASK_ENV"] = "development"
    env["HERON_DASHBOARD_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "run", "--port", str(port), "--no-reload"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for the app to start serving
    deadline = time.time() + APP_BOOT_TIMEOUT_S
    while time.time() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc
        if proc.poll() is not None:
            raise RuntimeError("Flask app exited before becoming reachable")
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError(f"Flask app did not respond on port {port} within {APP_BOOT_TIMEOUT_S}s")


def import_app():
    """Import the Flask app object so we can read its url_map without serving."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from heron.dashboard import create_app
        return create_app()
    except Exception as e:
        raise RuntimeError(f"Could not create heron.dashboard app: {e}")


# --- Capture loop -----------------------------------------------------------


def capture_all(prefix: str, plans: list[CapturePlan], base_url: str) -> None:
    """Run Playwright over every (plan, viewport) pair."""

    from playwright.sync_api import sync_playwright

    SCREENSHOTS_ROOT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for plan in plans:
                page_dir = SCREENSHOTS_ROOT / plan.slug
                page_dir.mkdir(parents=True, exist_ok=True)

                for vp_name, width, height in VIEWPORTS:
                    out_path = page_dir / f"{prefix}_{vp_name}_{plan.slug}.jpg"
                    capture_one(browser, base_url + plan.url_path, width, height, out_path)
                    print(f"  saved {out_path.relative_to(REPO_ROOT)}")
        finally:
            browser.close()


def capture_one(browser, url: str, width: int, height: int, out_path: Path) -> None:
    """Open one URL at one viewport and save a full-page JPG."""

    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(SETTLE_MS)
        # full_page=True captures the whole scroll area, not just the viewport
        page.screenshot(path=str(out_path), full_page=True, type="jpeg", quality=85)
    except Exception as e:
        print(f"  ! failed to capture {url} at {width}x{height}: {e}")
    finally:
        context.close()


# --- Custom routes file -----------------------------------------------------


def load_routes_file(path: Path) -> list[CapturePlan]:
    """Load a hand-written list of routes. One URL path per line. Comments
    start with #. Slug is derived from the path."""

    plans: list[CapturePlan] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slug = slug_from_rule(line.split("?", 1)[0])
        plans.append(CapturePlan(rule=line, url_path=line, slug=slug))
    return plans


# --- Entry point ------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Filename prefix (default: {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--routes-file",
        type=Path,
        help="Optional file with explicit URL paths to capture, one per line. "
             "Overrides automatic discovery.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Print the discovered capture plan and exit. Don't boot the app or screenshot.",
    )
    args = parser.parse_args()

    print("HERON dashboard screenshot capture")
    print(f"  repo:  {REPO_ROOT}")
    print(f"  out:   {SCREENSHOTS_ROOT.relative_to(REPO_ROOT)}")
    print(f"  prefix: {args.prefix}")
    print()

    # Build the capture plan
    if args.routes_file:
        print(f"Loading routes from {args.routes_file}")
        plans = load_routes_file(args.routes_file)
    else:
        print("Discovering routes from Flask url_map...")
        app = import_app()
        plans = discover_routes(app)

    print(f"Capture plan: {len(plans)} unique pages, {len(VIEWPORTS)} viewports each "
          f"-> {len(plans) * len(VIEWPORTS)} screenshots")
    for plan in plans:
        print(f"  {plan.slug:30s}  {plan.rule}  ->  {plan.url_path}")
    print()

    if args.list_only:
        return 0

    # Boot the app
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    print(f"Booting Flask app on {base_url}...")
    proc = boot_flask_app(port)

    try:
        print("Capturing screenshots...")
        capture_all(args.prefix, plans, base_url)
    finally:
        print("Shutting down Flask app...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    print(f"Done. Screenshots saved under {SCREENSHOTS_ROOT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
