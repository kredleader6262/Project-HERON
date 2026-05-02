"""Claude API client — thesis writing, conviction scoring, strategy proposals.

Uses httpx directly against the Anthropic Messages API. No SDK dependency.
API tier handles tasks requiring reasoning quality: thesis, conviction, debrief.
"""

import json
import logging
import time

import httpx

from heron.config import ANTHROPIC_API_KEY, CLAUDE_SONNET_MODEL
from heron.data.sanitize import sanitize

log = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_TIMEOUT = 60
_API_VERSION = "2023-06-01"

# Per-token costs (approximate, USD) — Sonnet/Haiku as of 2025
COST_PER_TOKEN = {
    "claude-sonnet": {"in": 3.0 / 1_000_000, "out": 15.0 / 1_000_000},
    "claude-haiku": {"in": 1.0 / 1_000_000, "out": 5.0 / 1_000_000},
}


def call(prompt, *, model=None, system=None,
         max_tokens=1024, temperature=0.3, json_mode=False):
    """Call Claude Messages API. Returns result dict.

    Raises httpx.HTTPError on failure, ValueError if no API key.
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set")
    if model is None:
        model = CLAUDE_SONNET_MODEL

    messages = [{"role": "user", "content": prompt}]
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "temperature": temperature,
    }
    if system:
        payload["system"] = system
    # JSON mode: strengthen instruction, no prefill (newer Claude models reject assistant prefill)
    if json_mode:
        messages[0] = {
            "role": "user",
            "content": prompt + "\n\nRespond with ONLY the JSON object, no prose, no markdown fences.",
        }

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
    }

    t0 = time.monotonic()
    r = httpx.post(_API_URL, json=payload, headers=headers, timeout=_TIMEOUT)
    try:
        if int(r.status_code) >= 400:
            log.error(f"Claude API {r.status_code}: {r.text[:500]}")
    except (TypeError, ValueError):
        pass
    r.raise_for_status()
    elapsed = time.monotonic() - t0

    body = r.json()
    text_blocks = [b["text"] for b in body.get("content", []) if b.get("type") == "text"]
    response_text = "".join(text_blocks)

    usage = body.get("usage", {})
    tokens_in = usage.get("input_tokens", 0)
    tokens_out = usage.get("output_tokens", 0)

    # Estimate cost
    cost_key = "claude-sonnet" if "sonnet" in model else "claude-haiku"
    rates = COST_PER_TOKEN.get(cost_key, COST_PER_TOKEN["claude-sonnet"])
    cost = tokens_in * rates["in"] + tokens_out * rates["out"]

    log.debug(f"Claude [{model}] {tokens_in}→{tokens_out} tok, ${cost:.4f}, {elapsed:.1f}s")

    result = {
        "text": response_text,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost, 6),
        "elapsed_s": round(elapsed, 2),
    }

    if json_mode:
        result["parsed"] = _extract_json(response_text)
        if result["parsed"] is None:
            log.warning(f"Claude returned invalid JSON: {response_text[:200]}")

    return result


def _extract_json(text):
    """Pull first {...} block from a string and parse. Returns None on failure."""
    if not text:
        return None
    # Strip common markdown fences
    t = text.strip()
    if t.startswith("```"):
        # Remove ```json or ``` lines
        lines = t.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        t = "\n".join(lines).strip()
    # Find first { and match braces
    start = t.find("{")
    if start == -1:
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None
