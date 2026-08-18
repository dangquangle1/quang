"""Environment-driven configuration, loaded once per cold start.

Thresholds live in the environment rather than in code so they can be retuned
by changing a Terraform variable — the right numbers aren't knowable up front.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

# Categories to poll. This is an allowlist because /markets silently ignores
# exclude_tag_id and caps untagged queries at 2100 rows, so there is no way to
# enumerate "everything". See docs/design.md "Category selection".
DEFAULT_TAG_SLUGS = (
    "politics",
    "geopolitics",
    "elections",
    "economy",
    "world",
    "trump",
    "finance",
    "business",
    "crypto",
    "ai",
    "science",
)

# Events carrying any of these tags are discarded. Filtering on tag ids rather
# than slug keywords is exact: a substring blocklist cannot distinguish "nfl"
# inside "inflation" or "nba" inside "coinbase" from the real thing, and
# silently dropped 10 genuine news markets when measured against live data.
#
# The first six are Polymarket's own metadata for mechanical, repeating
# markets - more reliable than guessing slug patterns, and they keep working
# as new ones appear rather than needing a year-pinned keyword each season.
DEFAULT_BLOCK_TAG_SLUGS = (
    "recurring",
    "hide-from-new",
    "daily",
    "daily-close",
    "up-or-down",
    "crypto-prices",
    # Musk tweet-count markets are tagged politics and clear every volume gate,
    # but a tweet tally is a counter, not news.
    "tweets-markets",
    "sports",
    "esports",
    "weather",
    "music",
    "movies",
    "box-office",
    "awards",
    "gaming",
    "video-games",
    "space",
)


class ConfigError(RuntimeError):
    """The environment is missing a required value or one won't parse."""


@dataclass(frozen=True)
class Config:
    gamma_api_base: str
    clob_api_base: str
    gamma_refresh_seconds: int
    tag_slugs: tuple
    block_tag_slugs: tuple
    window_seconds: int
    price_move_threshold: float
    min_volume_24h: float
    min_volume_total: float
    min_liquidity: float
    max_spread: float
    realert_move_threshold: float
    alert_floor_seconds: int
    alert_ttl_seconds: int
    ddb_table: str
    ssm_bot_token_param: str
    ssm_chat_id_param: str
    log_level: str


def load(env: Optional[Mapping] = None) -> Config:
    """Build a Config from the environment, raising ConfigError on bad input."""
    e = os.environ if env is None else env

    # Two values are defaults for others, so they're parsed up front.
    window = _int(e, "WINDOW_SECONDS", 300)
    price_move = _float(e, "PRICE_MOVE_THRESHOLD", 0.05)

    return Config(
        gamma_api_base=e.get("GAMMA_API_BASE", "https://gamma-api.polymarket.com").rstrip("/"),
        clob_api_base=e.get("CLOB_API_BASE", "https://clob.polymarket.com").rstrip("/"),
        # Gamma sets Cache-Control: max-age=300, so refetching the universe more
        # often than that just re-reads the same cached response.
        gamma_refresh_seconds=_int(e, "GAMMA_REFRESH_SECONDS", 300),
        tag_slugs=_csv(e.get("TAG_SLUGS"), DEFAULT_TAG_SLUGS, "TAG_SLUGS"),
        block_tag_slugs=_csv(
            e.get("BLOCK_TAG_SLUGS"), DEFAULT_BLOCK_TAG_SLUGS, "BLOCK_TAG_SLUGS"
        ),
        window_seconds=window,
        price_move_threshold=price_move,
        min_volume_24h=_float(e, "MIN_VOLUME_24H_USD", 5_000.0),
        # Lifetime volume: a market that has never traded much is thin whatever
        # its last 24 hours looked like. Measured against live data, 50k drops
        # 33 of 191 alertable markets while keeping newly-created news markets
        # like a fresh ceasefire question, which 100k would have cut.
        min_volume_total=_float(e, "MIN_VOLUME_TOTAL_USD", 50_000.0),
        min_liquidity=_float(e, "MIN_LIQUIDITY_USD", 1_000.0),
        max_spread=_float(e, "MAX_SPREAD", 0.10),
        # Re-alerting costs the same movement the first alert did, so a story
        # that keeps developing keeps reporting.
        realert_move_threshold=_float(e, "REALERT_MOVE_THRESHOLD", price_move),
        # Spam floor only. Detection can't re-fire on the same move (the last
        # alerted price is the reference), but a market oscillating across the
        # threshold could otherwise alert every poll.
        alert_floor_seconds=_int(e, "ALERT_FLOOR_SECONDS", window),
        # How long a claim's reference price survives. Past this the market is
        # treated as unseen and the next qualifying move alerts fresh.
        alert_ttl_seconds=_int(e, "ALERT_TTL_SECONDS", 86_400),
        ddb_table=_required(e, "DDB_TABLE"),
        ssm_bot_token_param=_required(e, "SSM_BOT_TOKEN_PARAM"),
        ssm_chat_id_param=_required(e, "SSM_CHAT_ID_PARAM"),
        log_level=e.get("LOG_LEVEL", "INFO").upper(),
    )


def _csv(raw: Optional[str], default: tuple, name: str) -> tuple:
    if raw is None or not raw.strip():
        return default
    values = tuple(v.strip().lower() for v in raw.split(",") if v.strip())
    if not values:
        raise ConfigError("{} was set but contained no usable values".format(name))
    return values


def _required(env: Mapping, name: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError("{} is required".format(name))
    return value


def _float(env: Mapping, name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ConfigError("{} must be a number, got {!r}".format(name, raw)) from None


def _int(env: Mapping, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ConfigError("{} must be an integer, got {!r}".format(name, raw)) from None
