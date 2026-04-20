"""Ollama client — thin wrapper for local LLM calls with forced JSON output.

Uses Ollama's HTTP API directly (no SDK dependency). The local model classifies
only — it never sizes, risks, or writes final theses.
"""

import json
import logging
import time

import httpx

from heron.config import OLLAMA_BASE_URL, OLLAMA_MODEL

log = logging.getLogger(__name__)

_TIMEOUT = 120  # seconds — local inference on 7B can be slow


def generate(prompt, *, model=None, json_mode=True, temperature=0.1,
             on_progress=None, stream=True):
    """Call Ollama /api/generate. Returns dict with text/parsed/tokens/elapsed.

    When stream=True (default), reads NDJSON chunks and calls on_progress(info)
    with {"tokens_out": n, "elapsed_s": t, "done": bool} as they arrive.
    Raises httpx.HTTPError on connection/timeout failures.
    """
    model = model or OLLAMA_MODEL
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {"temperature": temperature},
    }
    if json_mode:
        payload["format"] = "json"

    t0 = time.monotonic()

    if not stream:
        r = httpx.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        response_text = body.get("response", "")
        tokens_in = body.get("prompt_eval_count", 0)
        tokens_out = body.get("eval_count", 0)
    else:
        response_text = ""
        tokens_in = 0
        tokens_out = 0
        with httpx.stream("POST", f"{OLLAMA_BASE_URL}/api/generate",
                          json=payload, timeout=_TIMEOUT) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                response_text += chunk.get("response", "")
                if chunk.get("done"):
                    tokens_in = chunk.get("prompt_eval_count", 0)
                    tokens_out = chunk.get("eval_count", 0)
                else:
                    tokens_out += 1  # approx — each chunk ≈ 1 token
                if on_progress:
                    try:
                        on_progress({
                            "tokens_out": tokens_out,
                            "elapsed_s": time.monotonic() - t0,
                            "done": bool(chunk.get("done")),
                        })
                    except Exception as e:
                        # Don't let caller-supplied callback bugs kill the request
                        log.debug(f"on_progress callback raised: {e}")

    elapsed = time.monotonic() - t0
    log.debug(f"Ollama [{model}] {tokens_in}→{tokens_out} tok, {elapsed:.1f}s")

    result = {
        "text": response_text,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "elapsed_s": round(elapsed, 2),
    }

    if json_mode:
        try:
            result["parsed"] = json.loads(response_text)
        except json.JSONDecodeError:
            log.warning(f"Ollama returned invalid JSON: {response_text[:200]}")
            result["parsed"] = None

    return result


def is_available(model=None):
    """Check if Ollama is running and model is loaded."""
    model = model or OLLAMA_MODEL
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        # Match with or without tag suffix
        return any(model in m or m in model for m in models)
    except (httpx.HTTPError, KeyError, ValueError):
        return False
