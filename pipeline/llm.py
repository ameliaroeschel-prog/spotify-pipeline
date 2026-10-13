"""Minimal model client (stdlib only).

provider "openai": OpenAI Responses API with strict JSON-schema output.
provider "mock":   deterministic keyword labels, zero usage. For testing plumbing only; its
                   label_config contains "mock" so its results can never be mistaken for or
                   cached as real model output.
"""

import json
import os
import random
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid

OPENAI_URL = "https://api.openai.com/v1/responses"


def ssl_context():
    """python.org builds on macOS ship without CA certs; fall back to the system bundle."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx


SSL_CONTEXT = ssl_context()


class TransientError(Exception):
    """Rate limit, server error or timeout: safe to retry with backoff."""


class PermanentError(Exception):
    """Bad request/auth: retrying will not help."""


def call_openai(model, effort, instructions, user, schema, schema_name, max_output_tokens,
                cache_key=None, timeout=120):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise PermanentError("OPENAI_API_KEY is not set (put it in .env)")
    body = {
        "model": model,
        "instructions": instructions,           # identical prefix every call -> provider prompt caching
        "input": user,
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "store": False,
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
    }
    if cache_key:
        body["prompt_cache_key"] = cache_key
    req = urllib.request.Request(OPENAI_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            request_id = resp.headers.get("x-request-id") or data.get("id")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (408, 409, 429) or e.code >= 500:
            raise TransientError(f"HTTP {e.code}: {detail}")
        raise PermanentError(f"HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TransientError(f"network/timeout: {e}")

    usage = data.get("usage") or {}
    text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                   for c in item.get("content", []) if c.get("type") == "output_text")
    return {
        "request_id": request_id,
        "status": data.get("status"),                    # completed | incomplete
        "incomplete_reason": (data.get("incomplete_details") or {}).get("reason"),
        "text": text,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "cached_input_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
        },
    }


_MOCK_RULES = [("access", r"log ?in|password|account|sign ?up"), ("billing", r"premium|pay|price|charg|subscri"),
               ("downloads", r"download|offline"), ("playback", r"crash|lag|stop|play|buffer"),
               ("usability", r"\bads?\b|shuffle|queue"), ("catalog", r"song|artist|lyric|search|podcast")]


def call_mock(user, **_):
    """Deterministic stand-in for wiring tests. MOCK_FAIL_RATE env var injects transient failures."""
    if random.random() < float(os.environ.get("MOCK_FAIL_RATE", "0")):
        raise TransientError("mock injected transient failure")
    items = []
    for i, text in re.findall(r"<r i=(\d+)[^>]*>(.*?)</r>", user, flags=re.S):
        low = text.lower()
        topic = next((t for t, rx in _MOCK_RULES if re.search(rx, low)), "other")
        neg = bool(re.search(r"not|n't|bad|worst|hate|crash|ads", low))
        items.append({"i": int(i), "t": topic, "n": "complaint" if neg else "praise", "v": 3 if neg else 1,
                      "s": -1 if neg else 1, "e": [], "q": text[:40] if len(text) > 160 else "", "f": False})
    time.sleep(0.01)
    return {"request_id": "mock-" + uuid.uuid4().hex[:12], "status": "completed", "incomplete_reason": None,
            "text": json.dumps({"r": items}),
            "usage": {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None, "reasoning_tokens": None}}


def call(provider, **kwargs):
    if provider == "openai":
        return call_openai(**kwargs)
    if provider == "mock":
        return call_mock(**kwargs)
    raise PermanentError(f"provider {provider!r} not on the allowlist")


def with_retries(fn, log_attempt, max_attempts=4, base_delay=2.0):
    """Bounded exponential backoff with jitter for transient errors. Every attempt is logged,
    including failures, so the call log shows real retry behavior."""
    for attempt in range(1, max_attempts + 1):
        started = time.time()
        try:
            result = fn()
            log_attempt(attempt, "succeeded", result, None, started)
            return result
        except TransientError as e:
            log_attempt(attempt, "failed", None, str(e), started)
            if attempt == max_attempts:
                raise
            time.sleep(base_delay * 2 ** (attempt - 1) * (0.5 + random.random()))
        except PermanentError as e:
            log_attempt(attempt, "failed", None, str(e), started)
            raise
