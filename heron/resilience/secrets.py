"""Secrets hygiene (M15) — verify .env perms, required vars, log scrubbing.

Never enforces; reports. Operator acts on findings.
"""

import logging
import os
import re
import sys
from pathlib import Path

log = logging.getLogger(__name__)

REQUIRED_VARS = [
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "ANTHROPIC_API_KEY",
]
OPTIONAL_VARS = [
    "DISCORD_WEBHOOK_URL",
]

# Loose patterns for accidental secret exposure in log files.
SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "anthropic key"),
    (re.compile(r"PK[A-Z0-9]{18,}"), "alpaca key id"),
    (re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"), "discord webhook"),
]


def check_env_file(env_path=".env"):
    """Check .env file existence and permissions."""
    p = Path(env_path)
    result = {"path": str(p), "exists": p.exists()}
    if not p.exists():
        result["status"] = "missing"
        return result

    if sys.platform == "win32":
        result["status"] = "ok"
        result["note"] = "permission check skipped on Windows"
    else:
        mode = p.stat().st_mode & 0o777
        result["mode"] = oct(mode)
        if mode & 0o077:
            result["status"] = "insecure"
            result["note"] = f"mode {oct(mode)} — should be 0o600"
        else:
            result["status"] = "ok"
    return result


def check_required_vars():
    """Verify required env vars are set (value not logged)."""
    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    missing_optional = [v for v in OPTIONAL_VARS if not os.environ.get(v)]
    return {
        "status": "ok" if not missing else "missing",
        "missing_required": missing,
        "missing_optional": missing_optional,
    }


def scan_log_for_secrets(log_path, max_lines=5000):
    """Scan a log file for accidental secret leaks."""
    p = Path(log_path)
    if not p.exists():
        return {"status": "skipped", "reason": "log file not found", "path": str(p)}

    findings = []
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if i > max_lines:
                    break
                for pat, label in SECRET_PATTERNS:
                    if pat.search(line):
                        findings.append({"line": i, "pattern": label})
                        break  # one hit per line is enough
    except Exception as e:
        return {"status": "error", "error": str(e), "path": str(p)}

    return {
        "status": "clean" if not findings else "leaked",
        "path": str(p),
        "findings": findings,
        "scanned_lines": min(i if findings or i else 0, max_lines),
    }


def check_secrets_hygiene(env_path=".env", log_path=None):
    """Run all secrets checks. Returns consolidated dict."""
    result = {
        "env_file": check_env_file(env_path),
        "env_vars": check_required_vars(),
    }
    if log_path:
        result["log_scan"] = scan_log_for_secrets(log_path)

    issues = []
    if result["env_file"]["status"] not in ("ok",):
        issues.append(f"env file: {result['env_file']['status']}")
    if result["env_vars"]["missing_required"]:
        issues.append(f"missing required vars: {result['env_vars']['missing_required']}")
    if log_path and result.get("log_scan", {}).get("status") == "leaked":
        issues.append(f"log leak: {len(result['log_scan']['findings'])} finding(s)")

    result["status"] = "clean" if not issues else "issues"
    result["issues"] = issues
    return result
