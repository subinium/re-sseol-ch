"""Shared helpers for paper-report scripts (HTTP with retry, JSON IO)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "paper-report-skill/1.0 (research summarizer)"
DEFAULT_TIMEOUT_S = 40
MAX_RETRIES = 5
BASE_BACKOFF_S = 3.0
RETRYABLE = (429, 500, 502, 503, 504)


def log(msg: str) -> None:
    """Print a progress message to stderr."""
    print(msg, file=sys.stderr, flush=True)


def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    retries: int = MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT_S,
    data: bytes | None = None,
) -> bytes:
    """GET a URL, retrying on 429/5xx with exponential backoff.

    Honors Retry-After headers and OpenAlex's JSON `retryAfter` field.

    Raises:
        urllib.error.HTTPError: When a non-retryable status is returned or
            retries are exhausted.
    """
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=req_headers, data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as error:
            last_error = error
            # arXiv's export API throttles bursts with 406 instead of 429.
            throttled = error.code == 406 and "arxiv.org" in url
            if (error.code not in RETRYABLE and not throttled) or attempt == retries:
                raise
            wait = BASE_BACKOFF_S * (2**attempt)
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if retry_after and retry_after.isdigit():
                wait = max(wait, float(retry_after))
            try:
                body = json.loads(error.read().decode("utf-8", "ignore"))
                if isinstance(body, dict) and body.get("retryAfter"):
                    wait = max(wait, float(body["retryAfter"]))
            except (ValueError, OSError):
                pass
            wait = min(wait, 60.0)
            log(f"  HTTP {error.code} for {short(url)}; retry in {wait:.0f}s")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == retries:
                raise
            time.sleep(BASE_BACKOFF_S * (2**attempt))
    raise RuntimeError(f"unreachable: {last_error}")


def http_json(url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
    """GET a URL and decode JSON."""
    return json.loads(http_get(url, headers=headers, **kw).decode("utf-8"))


def http_post_json(url: str, payload: Any, headers: dict[str, str] | None = None) -> Any:
    """POST JSON with the same retry policy and decode the JSON response."""
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    body = http_get(url, headers=hdrs, data=json.dumps(payload).encode("utf-8"))
    return json.loads(body.decode("utf-8"))


def short(url: str, n: int = 90) -> str:
    """Shorten a URL for logs."""
    return url if len(url) <= n else url[: n - 3] + "..."


def q(value: str) -> str:
    """URL-quote a path or query component."""
    return urllib.parse.quote(value, safe="")


def read_json(path: Path) -> Any:
    """Read a UTF-8 JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Write pretty UTF-8 JSON."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def env(name: str) -> str | None:
    """Return a stripped env var or None."""
    value = os.environ.get(name, "").strip()
    return value or None
