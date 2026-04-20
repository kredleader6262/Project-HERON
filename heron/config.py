"""Configuration loader. Reads config.yaml + .env, exposes typed config."""

import os
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"


def _load_dotenv():
    """Minimal .env loader — no dependency needed."""
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()


def load_config(path=None):
    p = Path(path) if path else _CONFIG_PATH
    with open(p) as f:
        return yaml.safe_load(f)


_cfg = load_config()


# --- Watchlist ---
WATCHLIST = (
    _cfg["watchlist"]["mega_cap"]
    + _cfg["watchlist"]["broad_etfs"]
    + _cfg["watchlist"]["sector_etfs"]
)
MEGA_CAP = _cfg["watchlist"]["mega_cap"]
TICKER_FAMILIES = _cfg.get("ticker_families", {})

# --- Data settings ---
_data = _cfg.get("data", {})
CACHE_DIR = _ROOT / _data.get("cache_dir", "data")
CACHE_DB = CACHE_DIR / _data.get("cache_db", "heron.db")
QUOTE_STALE_SECONDS = _data.get("quote_stale_seconds", 10)

# --- News sources ---
NEWS_SOURCES = _cfg.get("news_sources", [])

# --- Timeframes ---
TIMEFRAMES = _cfg.get("timeframes", ["1Day"])

# --- Alpaca credentials (from env) ---
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# --- SEC ---
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "HERON-research your-email@example.com")

# --- Cost ---
MONTHLY_COST_CEILING = _cfg.get("cost", {}).get("monthly_ceiling_usd", 45.0)

# --- Ollama ---
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")

# --- Claude API ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_SONNET_MODEL = os.environ.get("CLAUDE_SONNET_MODEL", "claude-sonnet-4-6")
CLAUDE_HAIKU_MODEL = os.environ.get("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

# --- Audit (Section 6) ---
_audit = _cfg.get("audit", {})
# Knowledge-cutoff guard for retroactive comparisons (memorization defense).
# Only audit events/articles dated strictly after this.
LOCAL_MODEL_KNOWLEDGE_CUTOFF = _audit.get("local_model_cutoff", "2024-06-01")
CLAUDE_KNOWLEDGE_CUTOFF = _audit.get("claude_cutoff", "2025-03-01")
TRUST_SCORE_WINDOW_DAYS = _audit.get("trust_window_days", 30)
TRUST_SCORE_MIN_SAMPLES = _audit.get("trust_min_samples", 10)
TRUST_SCORE_THRESHOLD = _audit.get("trust_threshold", 0.70)
POST_MORTEM_DAILY_LIMIT = _audit.get("post_mortem_daily_limit", 5)

# --- Alerts / Discord (Section 12) ---
_alerts = _cfg.get("alerts", {})
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
ALERT_RATE_LIMIT_MINUTES = _alerts.get("rate_limit_minutes", 10)
ALERT_STATE_FILE = CACHE_DIR / _alerts.get("state_file", "alert_state.json")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5001")
