"""Minimal model clients (stdlib only).

provider "openai": OpenAI Responses API (enrich, group, memo roles) and Decisions API (verify role).
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

RESPONSES_URL = "https://api.openai.com/v1/responses"
DECISIONS_URL = "https://api.openai.com/v1/decisions"
ALLOWED_MODELS = {"gpt-6-luna"}          # model allowlist: anything else is refused before a call


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


def post_json(url, body, timeout):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise PermanentError("OPENAI_API_KEY is not set (put it in .env)")
    if body.get("model") not in ALLOWED_MODELS:
        raise PermanentError(f"model {body.get('model')!r} is not on the allowlist")
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data, resp.headers.get("x-request-id") or data.get("id")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (408, 409, 429) or e.code >= 500:
            raise TransientError(f"HTTP {e.code}: {detail}")
        raise PermanentError(f"HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # A timeout may still be billed by the provider: logged as failed with unknown usage.
        raise TransientError(f"network/timeout (charge unknown): {e}")


def _usage(usage):
    return {
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
    }


def call_openai(model, effort, instructions, user, schema, schema_name, max_output_tokens,
                cache_key=None, service_tier="standard", timeout=180):
    body = {
        "model": model,
        "instructions": instructions,           # identical prefix every call -> provider prompt caching
        "input": user,
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "store": False,
        "service_tier": "default" if service_tier == "standard" else service_tier,
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
    }
    if cache_key:
        body["prompt_cache_key"] = cache_key
    data, request_id = post_json(RESPONSES_URL, body, timeout)
    text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                   for c in item.get("content", []) if c.get("type") == "output_text")
    tier = data.get("service_tier")
    return {"request_id": request_id, "status": data.get("status"),          # completed | incomplete
            "incomplete_reason": (data.get("incomplete_details") or {}).get("reason"),
            "service_tier": "standard" if tier in (None, "default", "auto") else tier,
            "text": text, "usage": _usage(data.get("usage") or {})}


def call_decisions(model, text, questions, timeout=60, **_):
    """Decisions API: typed answers (choice/score/predicate) with probabilities; bills input only."""
    data, request_id = post_json(DECISIONS_URL, {"model": model, "input": text, "questions": questions}, timeout)
    answers = {a.get("name"): a for a in data.get("answers", [])}
    return {"request_id": request_id, "status": "completed", "incomplete_reason": None, "service_tier": "standard",
            "answers": answers, "text": json.dumps(answers), "usage": _usage(data.get("usage") or {})}


_MOCK_RULES = [("access", r"log ?in|password|account|sign ?up"), ("billing", r"premium|pay|price|charg|subscri"),
               ("downloads", r"download|offline"), ("playback", r"crash|lag|stop|play|buffer"),
               ("usability", r"\bads?\b|shuffle|queue"), ("catalog", r"song|artist|lyric|search|podcast")]


def _mock_label(text):
    low = text.lower()
    topic = next((t for t, rx in _MOCK_RULES if re.search(rx, low)), "other")
    neg = bool(re.search(r"not|n't|bad|worst|hate|crash|ads", low))
    return topic, ("complaint" if neg else "praise"), (3 if neg else 1), (-1 if neg else 1)


def call_mock(user=None, text=None, questions=None, **_):
    """Deterministic stand-in for wiring tests. MOCK_FAIL_RATE env var injects transient failures."""
    if random.random() < float(os.environ.get("MOCK_FAIL_RATE", "0")):
        raise TransientError("mock injected transient failure")
    time.sleep(0.01)
    base = {"request_id": "mock-" + uuid.uuid4().hex[:12], "status": "completed", "incomplete_reason": None,
            "service_tier": "standard", "usage": {"input_tokens": None, "cached_input_tokens": None,
                                                   "output_tokens": None, "reasoning_tokens": None}}
    if questions is not None:       # decisions-shaped mock
        t, n, v, s = _mock_label(text)
        answers = {"topic": {"choice": t}, "intent": {"choice": n}, "severity": {"choice": str(v)},
                   "sentiment": {"score": s + 2}}
        return {**base, "answers": answers, "text": json.dumps(answers)}
    lines = []
    for i, t in re.findall(r"<r i=(\d+)[^>]*>(.*?)</r>", user or "", flags=re.S):
        topic, intent, sev, sent = _mock_label(t)
        lines.append(f"{i}|{topic}|{intent}|{sev}|{sent}|0|{t[:40] if len(t) > 160 else ''}")
    return {**base, "text": json.dumps({"r": lines})}


def call(provider, api="responses", **kwargs):
    if provider == "mock":
        return call_mock(**kwargs)
    if provider == "openai":
        return call_decisions(**kwargs) if api == "decisions" else call_openai(**kwargs)
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
