"""Pure movement detection.

No I/O, no clock, no AWS - a Signal is purely a function of the market payload,
the recent price samples, and the config. That's what makes the thresholds
testable without touching Polymarket or DynamoDB.

Detection is max-minus-min across a short rolling window rather than Gamma's
precomputed oneHourPriceChange, because a point-to-point delta reads a round
trip (up 6pts, back down 6pts within the hour) as roughly zero. The window
catches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from config import Config

EVENT_URL = "https://polymarket.com/event/{slug}"
MARKET_URL = "https://polymarket.com/market/{slug}"

# (unix_seconds, price)
Sample = Tuple[float, float]


@dataclass(frozen=True)
class Signal:
    market_id: str
    slug: str
    question: str
    direction: str  # "up" | "down"
    prev_price: float  # the far end of the window's range
    price: float  # latest observed price
    delta: float  # max - min across the window, always positive
    volume_24h: float
    liquidity: float
    spread: Optional[float]  # None when the book has no two-sided quote
    url: str


def event_tag_ids(event: Mapping) -> set:
    """Tag ids carried by an event, as strings."""
    return {
        str(tag.get("id"))
        for tag in (event.get("tags") or [])
        if isinstance(tag, dict) and tag.get("id") is not None
    }


def is_blocked(event: Mapping, blocked_tag_ids: Iterable) -> bool:
    """True if the event carries any blocked tag.

    Comparing tag ids rather than matching slug substrings is what makes this
    exact. A keyword blocklist cannot tell "nfl" inside "inflation" or "nba"
    inside "coinbase" from the real thing - measured against live data, that
    dropped 10 genuine news markets including four CPI ones.
    """
    return bool(event_tag_ids(event) & {str(t) for t in blocked_tag_ids})


def passes_gates(market: Mapping, cfg: Config) -> bool:
    """Volume weighting: is this move backed by real, tradeable interest?

    Without these, a single small trade on an illiquid market is
    indistinguishable from a genuine repricing.
    """
    if to_float(market.get("volume24hr")) < cfg.min_volume_24h:
        return False
    if lifetime_volume(market) < cfg.min_volume_total:
        return False
    if to_float(market.get("liquidityNum")) < cfg.min_liquidity:
        return False
    spread = spread_of(market)
    # An unquoted book (spread is None) isn't evidence of a bad market, but a
    # wide one means the printed price isn't something you could trade at.
    return spread is None or spread <= cfg.max_spread


def prune(samples: Sequence, cutoff: float) -> List:
    """Drop samples older than cutoff, keeping chronological order."""
    return [(ts, price) for ts, price in samples if ts >= cutoff]


def evaluate(market: Mapping, samples: Sequence, cfg: Config) -> Optional[Signal]:
    """Return a Signal if the window's price range clears the threshold.

    `samples` must already be pruned to the window and include the current
    price as its last entry.
    """
    if len(samples) < 2:
        return None

    prices = [price for _, price in samples]
    low, high = min(prices), max(prices)
    delta = high - low
    if delta < cfg.price_move_threshold:
        return None

    price = prices[-1]
    # Which end of the range we came from: if we're in the top half of the
    # window's range the move is up, and the low is where it started.
    moving_up = price >= (low + high) / 2

    return Signal(
        market_id=str(market.get("id", "")),
        slug=str(market.get("slug", "")),
        question=str(market.get("question", "")),
        direction="up" if moving_up else "down",
        prev_price=low if moving_up else high,
        price=price,
        delta=delta,
        volume_24h=to_float(market.get("volume24hr")),
        liquidity=to_float(market.get("liquidityNum")),
        spread=spread_of(market),
        url=_url(market),
    )


def lifetime_volume(market: Mapping) -> float:
    """Total volume traded since the market opened.

    `volumeNum` is the numeric form; `volume` carries the same figure as a
    string on some payloads, so it's the fallback rather than a second signal.
    """
    return to_float(market.get("volumeNum") or market.get("volume"))


def spread_of(market: Mapping) -> Optional[float]:
    """Relative bid/ask spread, or None when the book isn't two-sided."""
    bid = to_float(market.get("bestBid"))
    ask = to_float(market.get("bestAsk"))
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return (ask - bid) / mid


def to_float(value: Any) -> float:
    """Coerce a Gamma numeric field to a float, treating absent as zero.

    Gamma omits fields entirely rather than sending zero, and returns numbers
    as either JSON numbers or strings depending on the field.
    """
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _url(market: Mapping) -> str:
    """Prefer the parent event page; fall back to the market's own page."""
    events = market.get("events") or []
    if events and isinstance(events[0], dict) and events[0].get("slug"):
        return EVENT_URL.format(slug=events[0]["slug"])
    slug = market.get("slug")
    return MARKET_URL.format(slug=slug) if slug else ""
