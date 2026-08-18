import json

import pytest

from clob import Quote, token_id_of
from conftest import make_config
from handler import build_signals, live_market

CFG = make_config()

NOW = 10_000.0


def market(market_id, **overrides):
    base = {
        "id": market_id,
        "slug": "market-{}".format(market_id),
        "question": "Question {}?".format(market_id),
        "clobTokenIds": json.dumps(["tok-{}".format(market_id), "tok-{}-no".format(market_id)]),
        # Deliberately stale: Gamma is cached for 300s, so these must not be
        # what the window measures.
        "outcomePrices": json.dumps(["0.99", "0.01"]),
        "volume24hr": 50_000.0,
        "volumeNum": 400_000.0,
        "liquidityNum": 20_000.0,
        "bestBid": 0.98,
        "bestAsk": 0.99,
        "events": [{"slug": "event-{}".format(market_id)}],
    }
    base.update(overrides)
    return base


def quote(price, spread=0.01):
    return Quote(mid=price, bid=price - spread / 2, ask=price + spread / 2)


def quotes_for(*pairs):
    return {"tok-{}".format(mid): q for mid, q in pairs}


# --- token extraction -----------------------------------------------------


def test_token_id_is_the_first_outcome():
    assert token_id_of(market("1")) == "tok-1"


def test_token_id_handles_a_real_list_and_missing_values():
    assert token_id_of({"clobTokenIds": ["a", "b"]}) == "a"
    assert token_id_of({"clobTokenIds": "not json"}) is None
    assert token_id_of({}) is None


# --- live overlay ---------------------------------------------------------


def test_live_quote_replaces_the_cached_book():
    merged = live_market(market("1"), quote(0.40, spread=0.02))
    assert merged["bestBid"] == pytest.approx(0.39)
    assert merged["bestAsk"] == pytest.approx(0.41)


def test_overlay_does_not_mutate_the_original():
    original = market("1")
    live_market(original, quote(0.40))
    assert original["bestBid"] == 0.98


def test_a_market_wide_on_the_live_book_is_gated_out_despite_a_tight_cached_one():
    # The cached bestBid/bestAsk look fine; the live book does not.
    history, signals = build_signals(
        [market("1")], quotes_for(("1", quote(0.40, spread=0.30))), {}, NOW, CFG
    )
    assert history == {}
    assert signals == []


# --- window on live prices ------------------------------------------------


def test_window_uses_the_live_midpoint_not_the_cached_price():
    history, _ = build_signals([market("1")], quotes_for(("1", quote(0.40))), {}, NOW, CFG)
    # 0.99 is what Gamma reported; 0.40 is the live book.
    assert history == {"1": [(NOW, 0.40)]}


def test_first_sighting_records_history_but_cannot_signal():
    _, signals = build_signals([market("1")], quotes_for(("1", quote(0.40))), {}, NOW, CFG)
    assert signals == []


def test_second_sighting_past_threshold_signals():
    prior = {"1": [(NOW - 60, 0.40)]}
    history, signals = build_signals(
        [market("1")], quotes_for(("1", quote(0.47))), prior, NOW, CFG
    )
    assert len(signals) == 1
    assert signals[0].delta == pytest.approx(0.07)
    assert history["1"] == [(NOW - 60, 0.40), (NOW, 0.47)]


def test_samples_outside_the_window_are_dropped():
    stale = {"1": [(NOW - 10_000, 0.10), (NOW - 60, 0.46)]}
    history, signals = build_signals(
        [market("1")], quotes_for(("1", quote(0.47))), stale, NOW, CFG
    )
    assert history["1"] == [(NOW - 60, 0.46), (NOW, 0.47)]
    assert signals == []


def test_unquoted_markets_are_skipped_entirely():
    # A CLOB batch that came back without this token: no price, no window entry.
    history, signals = build_signals([market("1")], {}, {"1": [(NOW - 60, 0.4)]}, NOW, CFG)
    assert history == {}
    assert signals == []


def test_markets_failing_the_volume_gate_are_not_tracked():
    markets = [market("1", volume24hr=10.0)]
    history, signals = build_signals(markets, quotes_for(("1", quote(0.47))), {}, NOW, CFG)
    assert history == {}
    assert signals == []


def test_history_drops_markets_not_priced_this_run():
    prior = {"old": [(NOW - 60, 0.5)], "1": [(NOW - 60, 0.40)]}
    history, _ = build_signals(
        [market("1")], quotes_for(("1", quote(0.41))), prior, NOW, CFG
    )
    assert set(history) == {"1"}


def test_signals_are_ordered_by_size_of_move():
    prior = {
        "small": [(NOW - 60, 0.40)],
        "big": [(NOW - 60, 0.10)],
        "mid": [(NOW - 60, 0.30)],
    }
    markets = [market("small"), market("big"), market("mid")]
    qs = quotes_for(("small", quote(0.46)), ("big", quote(0.40)), ("mid", quote(0.42)))
    _, signals = build_signals(markets, qs, prior, NOW, CFG)
    assert [s.market_id for s in signals] == ["big", "mid", "small"]


def test_each_market_keeps_its_own_window():
    prior = {"1": [(NOW - 60, 0.40)], "2": [(NOW - 60, 0.80)]}
    qs = quotes_for(("1", quote(0.47)), ("2", quote(0.81)))
    history, signals = build_signals([market("1"), market("2")], qs, prior, NOW, CFG)
    assert [s.market_id for s in signals] == ["1"]
    assert len(history) == 2


def test_no_markets_yields_an_empty_snapshot():
    assert build_signals([], {}, {"stale": [(NOW - 60, 0.5)]}, NOW, CFG) == ({}, [])
