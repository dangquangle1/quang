"""Lambda entrypoint: one poll pass.

Two data sources, for a reason. Gamma is edge-cached for 300s, so it drives
*discovery* - which markets exist, their tags, volume, liquidity - all of which
change slowly. The CLOB is uncached and batched, so it drives *prices*, which
is what the rolling window actually measures.

Cold start resolves tag ids and reads the Telegram credentials; both, plus the
Gamma universe, are cached for the life of the execution environment.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import clob
import config
import detector
import gamma
import ssm
import state
import telegram

log = logging.getLogger()

# Telegram rate-limits channel posts to roughly 20/minute, and a burst that
# large almost certainly means a misconfigured threshold rather than 40
# simultaneous news events. Biggest movers win the cap; the rest are left
# unclaimed so they can alert on a later poll if still moving.
MAX_ALERTS_PER_RUN = 10

_cache: Dict = {}


def _bootstrap() -> Dict:
    if "cfg" in _cache:
        return _cache

    cfg = config.load()
    logging.getLogger().setLevel(cfg.log_level)

    token, chat_id = ssm.fetch(cfg.ssm_bot_token_param, cfg.ssm_chat_id_param)
    _cache.update(
        cfg=cfg,
        allow_tag_ids=gamma.resolve_tag_ids(cfg.tag_slugs, cfg),
        block_tag_ids=gamma.resolve_tag_ids(cfg.block_tag_slugs, cfg, required=False),
        token=token,
        chat_id=chat_id,
        table=state.table(cfg.ddb_table),
    )
    log.info(
        "cold start: %d allow tags, %d block tags",
        len(_cache["allow_tag_ids"]),
        len(_cache["block_tag_ids"]),
    )
    return _cache


def _universe(ctx: Dict, now: float) -> List[Dict]:
    """The tracked market list, refreshed no faster than Gamma's cache TTL.

    Refetching more often than max-age=300 returns byte-identical data, so this
    keeps the expensive call (~15s) off most invocations.
    """
    cfg = ctx["cfg"]
    fetched_at = ctx.get("universe_at", 0.0)
    if ctx.get("universe") and now - fetched_at < cfg.gamma_refresh_seconds:
        return ctx["universe"]

    markets = gamma.fetch_markets(ctx["allow_tag_ids"], ctx["block_tag_ids"], cfg)
    tracked = [m for m in markets if detector.passes_gates(m, cfg)]
    ctx["universe"] = tracked
    ctx["universe_at"] = now
    log.info("universe refreshed: %d markets tracked of %d fetched", len(tracked), len(markets))
    return tracked


def live_market(market: Mapping, quote: clob.Quote) -> Dict:
    """Overlay live book values onto a Gamma market payload.

    Gamma's bestBid/bestAsk are as stale as its prices, so the spread gate has
    to re-run against the live quote rather than the cached one.
    """
    merged = dict(market)
    merged["bestBid"] = quote.bid
    merged["bestAsk"] = quote.ask
    return merged


def build_signals(
    markets: Sequence,
    quotes: Mapping,
    history: Mapping,
    now: float,
    cfg: config.Config,
) -> Tuple[Dict, List]:
    """Advance the rolling window on live prices and collect signals. Pure.

    Returns the next history snapshot and the signals found, ordered by size of
    move so the alert cap keeps the most significant ones.

    The returned history contains only markets priced on *this* run, which is
    what stops the snapshot growing without bound as markets close or fall out
    of the tracked universe.
    """
    cutoff = now - cfg.window_seconds
    next_history: Dict[str, List] = {}
    signals = []

    for market in markets:
        market_id = str(market.get("id") or "")
        token_id = clob.token_id_of(market)
        if not market_id or not token_id:
            continue
        quote = quotes.get(token_id)
        if quote is None:
            continue

        merged = live_market(market, quote)
        if not detector.passes_gates(merged, cfg):
            continue

        samples = detector.prune(history.get(market_id, []), cutoff)
        samples.append((now, quote.mid))
        next_history[market_id] = samples

        signal = detector.evaluate(merged, samples, cfg)
        if signal is not None:
            signals.append(signal)

    signals.sort(key=lambda s: s.delta, reverse=True)
    return next_history, signals


def lambda_handler(event=None, context=None) -> Dict:
    ctx = _bootstrap()
    cfg = ctx["cfg"]
    now = time.time()

    markets = _universe(ctx, now)
    token_ids = [t for t in (clob.token_id_of(m) for m in markets) if t]
    quotes = clob.fetch_quotes(token_ids, cfg.clob_api_base)

    history = state.load_history(ctx["table"])
    next_history, signals = build_signals(markets, quotes, history, now, cfg)
    snapshot_bytes = state.save_history(ctx["table"], next_history, now)

    capped = max(0, len(signals) - MAX_ALERTS_PER_RUN)
    if capped:
        log.warning(
            "%d signals exceeded the per-run cap of %d and were not alerted; "
            "they will re-alert on a later poll if still moving",
            capped,
            MAX_ALERTS_PER_RUN,
        )

    sent = suppressed = failed = 0
    for signal in signals[:MAX_ALERTS_PER_RUN]:
        claimed = state.try_claim(
            ctx["table"],
            signal.market_id,
            signal.price,
            now,
            cfg.realert_move_threshold,
            cfg.alert_floor_seconds,
            cfg.alert_ttl_seconds,
        )
        if not claimed:
            suppressed += 1
            continue
        if telegram.send_alert(signal, ctx["token"], ctx["chat_id"], cfg.window_seconds):
            sent += 1
        else:
            failed += 1

    summary = {
        "tracked": len(markets),
        "quoted": len(quotes),
        "windowed": len(next_history),
        "signals": len(signals),
        "sent": sent,
        "suppressed": suppressed,
        "failed": failed,
        "capped": capped,
        "snapshot_bytes": snapshot_bytes,
        "elapsed_s": round(time.time() - now, 2),
    }
    log.info("poll complete: %s", summary)
    return summary
