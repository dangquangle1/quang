from detector import Signal
from telegram import format_alert


def signal(**overrides):
    base = dict(
        market_id="12345",
        slug="will-x-happen",
        question="Will X happen?",
        direction="up",
        prev_price=0.40,
        price=0.47,
        delta=0.07,
        volume_24h=1_234_567.0,
        liquidity=20_000.0,
        spread=0.024,
        url="https://polymarket.com/event/x-event",
    )
    base.update(overrides)
    return Signal(**base)


def test_message_carries_every_required_field():
    text = format_alert(signal(), window_seconds=300)
    assert "Will X happen?" in text
    assert "https://polymarket.com/event/x-event" in text
    assert "40.0% → 47.0%" in text
    assert "+7.0 pts" in text
    assert "5m" in text
    assert "2.4%" in text
    assert "$1,234,567" in text


def test_downward_move_is_signed_negative():
    text = format_alert(signal(direction="down", prev_price=0.50, price=0.42, delta=0.08), 300)
    assert "-8.0 pts" in text
    assert "50.0% → 42.0%" in text


def test_a_small_move_is_not_rounded_away():
    # 0.695 -> 0.705 renders as "70% -> 70%" at zero decimals, which reads as
    # nothing having happened.
    text = format_alert(signal(prev_price=0.695, price=0.705, delta=0.01), 300)
    assert "69.5% → 70.5%" in text
    assert "+1.0 pts" in text


def test_html_in_the_question_is_escaped():
    # Telegram rejects the whole message on malformed HTML, so an unescaped
    # ampersand in a market title would silently drop the alert.
    text = format_alert(signal(question="Tom & Jerry <b>win</b>?"), 300)
    assert "Tom &amp; Jerry &lt;b&gt;win&lt;/b&gt;?" in text
    assert "<b>win</b>" not in text


def test_unquoted_book_renders_as_not_available():
    assert "Spread n/a" in format_alert(signal(spread=None), 300)


def test_title_falls_back_when_the_question_is_missing():
    assert "will-x-happen" in format_alert(signal(question=""), 300)


def test_window_is_rendered_in_whole_minutes():
    assert "1m" in format_alert(signal(), window_seconds=60)
    assert "15m" in format_alert(signal(), window_seconds=900)


def test_sub_minute_window_still_reads_as_one_minute():
    assert "1m" in format_alert(signal(), window_seconds=30)
