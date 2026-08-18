"""Polymarket Gamma API client, on urllib so the Lambda has no dependencies.

Markets are read via /events rather than /markets. Events carry a tags[] list
and nest their markets, so one fetch gives both the category metadata needed to
filter and every field needed to evaluate. /markets exposes no tags at all,
which would leave slug keyword matching as the only option.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from config import Config
from detector import is_blocked, to_float

log = logging.getLogger(__name__)

# Gamma caps limit at 100 no matter what is requested - asking for more
# silently returns 100, which is how the prototype ended up reading three
# scattered pages while believing it had scanned 3000 markets.
PAGE_SIZE = 100

# Safety stop. With events sorted by volume descending we expect to break out
# on the volume gate long before this; hitting it means the gate is too low.
MAX_PAGES_PER_TAG = 20

# An event's volume24hr is the sum across its markets, but not exactly - up to
# ~4% drift observed. Stopping a little below the gate avoids clipping a market
# that would have cleared it.
EVENT_VOLUME_MARGIN = 0.9

TIMEOUT_SECONDS = 10
RETRIES = 2

# Gamma sits behind Cloudflare, which 403s urllib's default
# "Python-urllib/3.x" User-Agent. Identifying ourselves properly is required,
# not cosmetic - without this every request fails.
USER_AGENT = "polymarket-movers/1.0 (+https://github.com/dangquangle1/quang)"


def resolve_tag_ids(slugs: Sequence, cfg: Config, required: bool = True) -> List[str]:
    """Map tag slugs to numeric ids, skipping any that don't resolve.

    A renamed or retired tag shouldn't take the whole run down, so a failed
    lookup is logged and dropped. `required` guards the allowlist, where
    resolving nothing means we would poll nothing at all.
    """
    ids: List[str] = []
    for slug in slugs:
        url = "{}/tags/slug/{}".format(cfg.gamma_api_base, urllib.parse.quote(slug))
        try:
            tag = _get_json(url)
        except OSError as exc:
            log.warning("tag %r did not resolve, skipping: %s", slug, exc)
            continue
        tag_id = (tag or {}).get("id")
        if tag_id is None:
            log.warning("tag %r returned no id, skipping", slug)
            continue
        ids.append(str(tag_id))
    if required and not ids:
        raise RuntimeError("no tag slugs resolved; check TAG_SLUGS")
    return ids


def fetch_markets(
    allow_tag_ids: Sequence, block_tag_ids: Iterable, cfg: Config
) -> List[Dict]:
    """Fetch markets from every allowed tag, dropping blocked events.

    Events are deduped by id (the configured tags overlap heavily), then their
    nested markets are flattened and deduped in turn.
    """
    events: Dict[str, Dict] = {}
    for tag_id in allow_tag_ids:
        for event in _fetch_events_for_tag(tag_id, cfg):
            event_id = str(event.get("id") or "")
            if event_id:
                events[event_id] = event

    markets: Dict[str, Dict] = {}
    blocked = 0
    for event in events.values():
        if is_blocked(event, block_tag_ids):
            blocked += 1
            continue
        for market in event.get("markets") or []:
            market_id = str(market.get("id") or "")
            if market_id:
                markets[market_id] = market

    log.info(
        "fetched %d events across %d tags (%d blocked by tag) -> %d markets",
        len(events),
        len(allow_tag_ids),
        blocked,
        len(markets),
    )
    return list(markets.values())


def _fetch_events_for_tag(tag_id: str, cfg: Config) -> Iterator[Dict]:
    """Yield a tag's events, stopping once volume drops below the gate.

    Events come back sorted by 24h volume descending, so the first one under
    the gate means every remaining one is too. This is what keeps a poll to
    ~12 requests rather than exhausting every page.
    """
    floor = cfg.min_volume_24h * EVENT_VOLUME_MARGIN

    for page_number in range(MAX_PAGES_PER_TAG):
        params = urllib.parse.urlencode(
            {
                "tag_id": tag_id,
                "closed": "false",
                "active": "true",
                "limit": PAGE_SIZE,
                "offset": page_number * PAGE_SIZE,
                "order": "volume24hr",
                "ascending": "false",
            }
        )
        url = "{}/events?{}".format(cfg.gamma_api_base, params)
        try:
            page = _get_json(url)
        except OSError as exc:
            log.warning("tag %s page %d failed, skipping rest of tag: %s", tag_id, page_number, exc)
            return

        if not page:
            return

        for event in page:
            if to_float(event.get("volume24hr")) < floor:
                return
            yield event

        if len(page) < PAGE_SIZE:
            return

    log.warning(
        "tag %s hit the %d page cap without reaching the volume gate; "
        "results are truncated",
        tag_id,
        MAX_PAGES_PER_TAG,
    )


def _get_json(url: str, retries: int = RETRIES) -> Optional[object]:
    """GET and parse JSON, retrying transient failures with a short backoff."""
    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 4xx won't fix itself; only retry server-side failures.
            if exc.code < 500:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    raise OSError("GET {} failed after {} attempts: {}".format(url, retries + 1, last_error))
