"""Repo-local Ollama process manager.

Runs `ollama serve` with OLLAMA_MODELS pointing to tools/ollama-models, so
all model data stays inside the repo (gitignored). PID tracked in tools/ollama.pid.
"""

import os
import subprocess
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
OLLAMA_DIR = REPO_ROOT / "tools" / "ollama"
OLLAMA_EXE = OLLAMA_DIR / ("ollama.exe" if os.name == "nt" else "ollama")
MODELS_DIR = REPO_ROOT / "tools" / "ollama-models"
PID_FILE = REPO_ROOT / "tools" / "ollama.pid"
LOG_FILE = REPO_ROOT / "tools" / "ollama.log"
DEFAULT_URL = "http://127.0.0.1:11434"


def is_installed():
    return OLLAMA_EXE.exists()


def is_running(url=DEFAULT_URL, timeout=1.0):
    try:
        r = httpx.get(f"{url}/api/tags", timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _env():
    env = os.environ.copy()
    env["OLLAMA_MODELS"] = str(MODELS_DIR)
    env["OLLAMA_HOST"] = "127.0.0.1:11434"
    return env


def start(wait_seconds=15):
    """Start ollama serve in background. Returns (started, message)."""
    if not is_installed():
        return False, f"Ollama binary not found at {OLLAMA_EXE}"
    if is_running():
        return True, "Ollama already running"

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Detached on Windows so it survives parent exit
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    log = open(LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [str(OLLAMA_EXE), "serve"],
        env=_env(),
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=flags, close_fds=True,
    )
    PID_FILE.write_text(str(proc.pid))

    for _ in range(wait_seconds * 2):
        if is_running():
            return True, f"Ollama started (pid={proc.pid})"
        time.sleep(0.5)
    return False, f"Ollama process spawned (pid={proc.pid}) but did not respond within {wait_seconds}s"


def stop():
    """Stop ollama serve if running."""
    if not PID_FILE.exists():
        if is_running():
            return False, "Ollama is running but not managed by this wrapper (no PID file)"
        return True, "Ollama not running"

    pid = int(PID_FILE.read_text().strip())
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, check=False)
        else:
            os.kill(pid, 15)
    finally:
        try:
            PID_FILE.unlink()
        except OSError:
            # File already gone or locked — not critical
            pass
    return True, f"Stopped pid={pid}"

def run_cmd(args, capture=True):
    """Run the ollama CLI with our env (e.g. pull, list, rm)."""
    if not is_installed():
        raise RuntimeError(f"Ollama binary not found at {OLLAMA_EXE}")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [str(OLLAMA_EXE), *args],
        env=_env(),
        capture_output=capture,
        text=True,
    )


def status():
    return {
        "installed": is_installed(),
        "binary": str(OLLAMA_EXE),
        "models_dir": str(MODELS_DIR),
        "running": is_running(),
        "pid_file": str(PID_FILE) if PID_FILE.exists() else None,
    }
