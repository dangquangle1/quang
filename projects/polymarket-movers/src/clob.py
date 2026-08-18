"""CLOB API client - live quotes.

Gamma is edge-cached for 300 seconds (`Cache-Control: public, max-age=300`), so
its prices can be five minutes stale - measured, 11 of 191 tracked markets
disagreed with the live book by up to 2 probability points, and nothing at all
changed across seven Gamma samples over two minutes. A rolling window built on
that would mostly compare a value against itself.

The CLOB is uncached (`cf-cache-status: DYNAMIC`) and takes batched requests, so
every tracked market fits in one call - measured at 191 tokens in 0.15s.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15

# One request comfortably carried every market we track; chunk anyway so the
# behaviour doesn't change shape if the universe grows.
CHUNK = 250

USER_AGENT = "polymarket-movers/1.0 (+https://github.com/dangquangle1/quang)"


@dataclass(frozen=True)
class Quote:
    mid: float
    bid: float
    ask: float


def fetch_quotes(token_ids: Sequence, base: str) -> Dict[str, Quote]:
    """Live midpoint and best bid/ask for each token id.

    Tokens with no two-sided book come back with bid/ask of 0, which the spread
    gate reads as "unquoted" and abstains on.
    """
    unique = list(dict.fromkeys(str(t) for t in token_ids if t))
    mids: Dict[str, float] = {}
    sides: Dict[str, Dict[str, float]] = {}

    for start in range(0, len(unique), CHUNK):
        chunk = unique[start : start + CHUNK]
        mids.update(_post_floats(base, "/midpoints", [{"token_id": t} for t in chunk]))
        sides.update(_post_sides(base, chunk))

    quotes = {}
    for token_id, mid in mids.items():
        side = sides.get(token_id, {})
        quotes[token_id] = Quote(
            mid=mid, bid=side.get("BUY", 0.0), ask=side.get("SELL", 0.0)
        )
    log.info("fetched %d live quotes from the CLOB", len(quotes))
    return quotes


def _post_floats(base: str, path: str, payload: List) -> Dict[str, float]:
    body = _post(base + path, payload) or {}
    out = {}
    for token_id, value in body.items():
        parsed = _to_float(value)
        if parsed is not None:
            out[str(token_id)] = parsed
    return out


def _post_sides(base: str, token_ids: Sequence) -> Dict[str, Dict[str, float]]:
    """Best bid and ask per token. BUY is the bid side, SELL the ask side."""
    payload = []
    for token_id in token_ids:
        payload.append({"token_id": token_id, "side": "BUY"})
        payload.append({"token_id": token_id, "side": "SELL"})

    body = _post(base + "/prices", payload) or {}
    out: Dict[str, Dict[str, float]] = {}
    for token_id, side_map in body.items():
        if not isinstance(side_map, dict):
            continue
        parsed = {}
        for side, value in side_map.items():
            as_float = _to_float(value)
            if as_float is not None:
                parsed[side] = as_float
        out[str(token_id)] = parsed
    return out


def _post(url: str, payload: List) -> Optional[dict]:
    """POST JSON, returning None on failure rather than raising.

    A failed quote batch costs one poll's worth of price samples, which the
    window recovers from. It shouldn't take the whole invocation down.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log.warning("CLOB POST %s failed: %s", url, exc)
        return None


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def token_id_of(market) -> Optional[str]:
    """First outcome's CLOB token id, which is the one we price."""
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if isinstance(raw, list) and raw:
        return str(raw[0])
    return None
