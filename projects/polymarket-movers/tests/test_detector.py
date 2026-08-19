from dataclasses import replace

import pytest

from conftest import make_config
from detector import (
    evaluate,
    event_tag_ids,
    is_blocked,
    lifetime_volume,
    passes_gates,
    prune,
    spread_of,
)

CFG = make_config()


def market(**overrides):
    """A market that clears every gate, with a tight book, unless overridden."""
    base = {
        "id": "12345",
        "slug": "will-x-happen",
        "question": "Will X happen?",
        "volume24hr": 50_000.0,
        "volumeNum": 400_000.0,
        "liquidityNum": 20_000.0,
        "bestBid": 0.41,
        "bestAsk": 0.43,
        "events": [{"slug": "x-event"}],
    }
    base.update(overrides)
    return base


def samples(*prices, start=1_000.0, step=60.0):
    return [(start + i * step, p) for i, p in enumerate(prices)]


# --- slug blocklist -------------------------------------------------------


BLOCK_IDS = ("1", "101757", "102169")  # sports, recurring, hide-from-new


def event(*tag_ids, **overrides):
    base = {
        "id": "30829",
        "slug": "some-event",
        "tags": [{"id": t, "slug": "tag-{}".format(t)} for t in tag_ids],
    }
    base.update(overrides)
    return base


def test_event_carrying_a_blocked_tag_is_blocked():
    assert is_blocked(event("2", "101757"), BLOCK_IDS)


def test_event_with_only_allowed_tags_is_kept():
    assert not is_blocked(event("2", "144", "126"), BLOCK_IDS)


def test_untagged_event_is_kept():
    assert not is_blocked(event(), BLOCK_IDS)
    assert not is_blocked({"id": "1"}, BLOCK_IDS)
    assert not is_blocked({"id": "1", "tags": None}, BLOCK_IDS)


def test_ids_compare_across_int_and_string_forms():
    # Gamma returns tag ids as strings; a hand-written blocklist may use ints.
    assert is_blocked(event(1), ("1",))
    assert is_blocked(event("1"), (1,))


def test_empty_blocklist_blocks_nothing():
    assert not is_blocked(event("1", "101757"), ())


def test_malformed_tag_entries_are_ignored():
    assert not is_blocked({"tags": ["not-a-dict", {"no": "id"}]}, BLOCK_IDS)


@pytest.mark.parametrize(
    "slug",
    [
        "will-annual-inflation-be-3pt3-in-july",
        "brian-armstrong-out-as-coinbase-ceo-before-2027",
        "will-ukraine-agree-to-give-up-the-rest-of-donbas-before-2027",
    ],
)
def test_slug_text_has_no_bearing_on_blocking(slug):
    # The whole point of moving to tag ids: "inflation" contains "nfl",
    # "coinbase" and "donbas" contain "nba". A keyword blocklist dropped all
    # three; matching on tags cannot.
    assert not is_blocked(event("2", slug=slug), BLOCK_IDS)


def test_tag_ids_are_extracted_from_an_event():
    assert event_tag_ids(event("2", "144")) == {"2", "144"}
    assert event_tag_ids({}) == set()


# --- gates ----------------------------------------------------------------


def test_a_healthy_market_passes_every_gate():
    assert passes_gates(market(), CFG)


def test_thin_volume_fails():
    assert not passes_gates(market(volume24hr=100.0), CFG)


def test_thin_lifetime_volume_fails():
    # Busy today but barely traded ever: a market this new is thin whatever its
    # last 24 hours look like.
    assert not passes_gates(market(volumeNum=9_000.0), CFG)


def test_lifetime_volume_falls_back_to_the_string_field():
    # Some payloads carry the figure only as `volume`, as a string.
    thin = market(volumeNum=None, volume="9000")
    assert lifetime_volume(thin) == pytest.approx(9_000.0)
    assert not passes_gates(thin, CFG)
    assert passes_gates(market(volumeNum=None, volume="400000"), CFG)


def test_thin_liquidity_fails():
    assert not passes_gates(market(liquidityNum=10.0), CFG)


def test_wide_spread_fails():
    # 0.30/0.70 -> spread 0.8 of mid, far beyond the 0.10 ceiling.
    assert not passes_gates(market(bestBid=0.30, bestAsk=0.70), CFG)


def test_one_sided_book_is_not_penalised():
    # No quote isn't evidence of a bad market, so the spread gate abstains.
    assert passes_gates(market(bestBid=0, bestAsk=0), CFG)
    assert spread_of(market(bestBid=0, bestAsk=0)) is None


def test_spread_is_relative_to_the_midpoint():
    assert spread_of(market(bestBid=0.49, bestAsk=0.51)) == pytest.approx(0.04)


# --- rolling window -------------------------------------------------------


def test_move_across_the_window_alerts():
    sig = evaluate(market(), samples(0.40, 0.43, 0.47), CFG)
    assert sig is not None
    assert sig.delta == pytest.approx(0.07)
    assert sig.direction == "up"
    assert sig.prev_price == pytest.approx(0.40)
    assert sig.price == pytest.approx(0.47)


def test_move_below_threshold_is_silent():
    assert evaluate(market(), samples(0.40, 0.42, 0.44), CFG) is None


def test_move_exactly_at_threshold_alerts():
    # Binary-exact values, so this tests the >= boundary rather than float
    # representation error: 0.45 - 0.40 is actually 0.04999999999999999.
    cfg = replace(CFG, price_move_threshold=0.25)
    assert evaluate(market(), samples(0.25, 0.50), cfg) is not None


def test_move_a_hair_under_threshold_is_silent():
    cfg = replace(CFG, price_move_threshold=0.25)
    assert evaluate(market(), samples(0.25, 0.4375), cfg) is None


def test_downward_move_reports_the_high_as_the_start():
    sig = evaluate(market(), samples(0.50, 0.46, 0.42), CFG)
    assert sig.direction == "down"
    assert sig.prev_price == pytest.approx(0.50)
    assert sig.price == pytest.approx(0.42)
    assert sig.delta == pytest.approx(0.08)


def test_a_single_sample_cannot_form_a_delta():
    assert evaluate(market(), samples(0.40), CFG) is None


def test_empty_history_is_silent():
    assert evaluate(market(), [], CFG) is None


def test_round_trip_spike_is_caught():
    # The whole reason for a max-min window: a point-to-point delta would read
    # this as zero, because it ends where it started.
    sig = evaluate(market(), samples(0.40, 0.48, 0.40), CFG)
    assert sig is not None
    assert sig.delta == pytest.approx(0.08)


# --- pruning --------------------------------------------------------------


def test_prune_drops_samples_older_than_the_cutoff():
    assert prune(samples(0.40, 0.41, 0.42), cutoff=1_060.0) == [
        (1_060.0, 0.41),
        (1_120.0, 0.42),
    ]


def test_prune_keeps_everything_when_nothing_is_stale():
    assert len(prune(samples(0.40, 0.41), cutoff=0.0)) == 2


def test_prune_can_empty_the_window():
    assert prune(samples(0.40, 0.41), cutoff=9_999.0) == []


# --- payload parsing ------------------------------------------------------


def test_numeric_fields_may_arrive_as_strings():
    assert passes_gates(market(volume24hr="9000", liquidityNum="2000"), CFG)


def test_url_prefers_the_parent_event():
    assert evaluate(market(), samples(0.40, 0.47), CFG).url == (
        "https://polymarket.com/event/x-event"
    )


def test_url_falls_back_to_the_market_page():
    sig = evaluate(market(events=[]), samples(0.40, 0.47), CFG)
    assert sig.url == "https://polymarket.com/market/will-x-happen"
