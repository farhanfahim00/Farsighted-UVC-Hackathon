"""Shared Claude client: structured JSON output, retries, and an on-disk cache.

    from llm import call_json
    data, info = call_json(model, system_prompt_file, content_blocks, schema, label="profile")

* API rules (version 2026-10-21): never send temperature / top_p / top_k. Structured output goes through
  output_config={"format": {"type": "json_schema", "schema": ...}}.
* A response is accepted only if stop_reason == "end_turn" and its text block (found by type, not by position)
  parses as JSON. On "max_tokens" the call is retried once with a higher limit. Anything else raises LLMFailure,
  and failures are never cached.
* Rate limits, overloads, connection errors and timeouts are retried with backoff.
* Every accepted response is cached under build/cache/, keyed by model + prompt file contents + schema + input.
  The same input never makes a second API call. Calls and tokens are counted in build/cache/usage.jsonl.
* ANTHROPIC_API_KEY comes from .env (python-dotenv); it is never printed or logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import config
from config import CACHE_DIR, PROMPTS_DIR, ROOT

load_dotenv(ROOT / ".env")

TIMEOUT_S = 300
MAX_ATTEMPTS = 6
RETRYABLE = (anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.APIConnectionError,
             anthropic.InternalServerError, anthropic.OverloadedError, anthropic.ServiceUnavailableError,
             anthropic.DeadlineExceededError, TimeoutError)

_lock = threading.Lock()
_client: anthropic.Anthropic | None = None
STATS = {"api_calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0, "failures": 0,
         "cache_read_tokens": 0, "cache_write_tokens": 0}


class LLMFailure(RuntimeError):
    """A call that could not produce an accepted response (after retries)."""


def client() -> anthropic.Anthropic:
    global _client
    with _lock:
        if _client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise LLMFailure("ANTHROPIC_API_KEY is not set (expected in .env)")
            # our own retry loop handles rate limits and timeouts, so the SDK's is off
            _client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=0)
        return _client


def prompt_text(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def cache_key(model: str, system: str, content: list[dict], schema: dict) -> str:
    payload = json.dumps({"model": model, "system": system, "schema": schema, "content": content},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text_block(message) -> str | None:
    """The first text block, found by type (the response may start with a thinking block)."""
    return next((b.text for b in message.content if getattr(b, "type", None) == "text"), None)


CACHE_MARK = {"type": "ephemeral"}
PROFILE_HEAD = "PRODUCT PROFILE (JSON)\n"


def with_cache_markers(system: str, content: list[dict]) -> tuple[list[dict], list[dict]]:
    """Request-time prompt caching (config.PROMPT_CACHING). The system prompt is one cached block; a user text that starts
    with the product profile (the same for every call of a case) is split after the profile into a cached block and the
    varying rest. The cached-response key (cache_key) is computed from the unmarked prompt, so nothing already cached
    under build/cache/ is invalidated."""
    system_blocks = [{"type": "text", "text": system, "cache_control": CACHE_MARK}]
    out: list[dict] = []
    for k, block in enumerate(content):
        text = block.get("text", "") if block.get("type") == "text" else ""
        i = text.find(PROFILE_HEAD)
        j = text.find("\n\n", i) if 0 <= i < 40 else -1
        if k == 0 and j > 0:
            out += [{"type": "text", "text": text[:j + 2], "cache_control": CACHE_MARK}, {"type": "text", "text": text[j + 2:]}]
        else:
            out.append(block)
    return system_blocks, out


def _request(model: str, system: str, content: list[dict], schema: dict, max_tokens: int):
    """One streamed request with retries on transient errors. Returns the final message."""
    last: Exception | None = None
    if config.PROMPT_CACHING:
        system, content = with_cache_markers(system, content)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with client().messages.stream(
                model=model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            ) as stream:
                return stream.get_final_message()
        except RETRYABLE as exc:
            last = exc
            time.sleep(min(60, 2 ** attempt) + random.random())
    raise LLMFailure(f"gave up after {MAX_ATTEMPTS} attempts: {type(last).__name__}")


def _log_usage(label: str, model: str, key: str, usage, stop_reason: str) -> None:
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    row = {"label": label, "model": model, "key": key[:16], "input_tokens": usage.input_tokens,
           "output_tokens": usage.output_tokens, "cache_read_tokens": read, "cache_write_tokens": write,
           "stop_reason": stop_reason, "time": time.strftime("%H:%M:%S")}
    with _lock:
        STATS["api_calls"] += 1
        STATS["input_tokens"] += usage.input_tokens
        STATS["output_tokens"] += usage.output_tokens
        STATS["cache_read_tokens"] += read
        STATS["cache_write_tokens"] += write
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_DIR / "usage.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


def call_json(model: str, system_file: str, content: list[dict], schema: dict, *, label: str = "",
              max_tokens: int = 8000) -> tuple[dict, dict]:
    """Run one structured call (or read it from the cache). Returns (parsed JSON, info).
    info = {"cached": bool, "input_tokens": int, "output_tokens": int, "stop_reason": str}."""
    system = prompt_text(system_file)
    key = cache_key(model, system, content, schema)
    path = CACHE_DIR / f"{key}.json"
    if path.is_file():
        hit = json.loads(path.read_text(encoding="utf-8"))
        with _lock:
            STATS["cache_hits"] += 1
        return hit["data"], {"cached": True, **hit["info"]}

    limit = max_tokens
    for _ in range(2):                    # the second pass only happens after max_tokens
        message = _request(model, system, content, schema, limit)
        _log_usage(label, model, key, message.usage, message.stop_reason)
        if message.stop_reason == "max_tokens" and limit == max_tokens:
            limit = max_tokens * 2        # retry once with a higher limit
            continue
        break
    if message.stop_reason != "end_turn":
        with _lock:
            STATS["failures"] += 1
        raise LLMFailure(f"stop_reason={message.stop_reason} (label {label!r})")
    text = _text_block(message)
    try:
        data = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        with _lock:
            STATS["failures"] += 1
        raise LLMFailure(f"no JSON object in the text block (label {label!r})")

    info = {"input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens,
            "stop_reason": message.stop_reason}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"data": data, "info": info, "model": model, "label": label}, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)                     # atomic: a half-written file is never read as a hit
    return data, {"cached": False, **info}


def object_schema(properties: dict, *, required: list[str] | None = None) -> dict:
    """A JSON-schema object with every property required and no extra properties (the API needs both)."""
    return {"type": "object", "properties": properties, "required": required or list(properties),
            "additionalProperties": False}


def usage_summary() -> str:
    s = STATS
    return (f"api_calls={s['api_calls']} cache_hits={s['cache_hits']} failures={s['failures']} "
            f"input_tokens={s['input_tokens']} output_tokens={s['output_tokens']} "
            f"cache_read_tokens={s['cache_read_tokens']} cache_write_tokens={s['cache_write_tokens']}")


def total_logged_usage() -> dict:
    """Totals over every real API call ever logged in build/cache/usage.jsonl (all runs, all sessions)."""
    rows = []
    path = CACHE_DIR / "usage.jsonl"
    if path.is_file():
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_model: dict[str, dict] = {}
    for r in rows:
        m = by_model.setdefault(r["model"], {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        m["calls"] += 1
        m["input_tokens"] += r["input_tokens"]
        m["output_tokens"] += r["output_tokens"]
    return {"calls": len(rows), "by_model": by_model}
