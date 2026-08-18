"""Telegram delivery, on urllib so the Lambda has no dependencies."""

from __future__ import annotations

import html
import logging
import urllib.error
import urllib.parse
import urllib.request

from detector import Signal

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 10


def format_alert(signal: Signal, window_seconds: int) -> str:
    """Build the HTML message body.

    The question is user-facing text from Polymarket, so it must be escaped -
    an unescaped '&' or '<' makes Telegram reject the whole message.
    """
    arrow = "\U0001f7e2" if signal.direction == "up" else "\U0001f534"
    spread = "n/a" if signal.spread is None else "{:.1f}%".format(signal.spread * 100)
    minutes = max(1, round(window_seconds / 60))
    title = html.escape(signal.question or signal.slug or signal.market_id)

    lines = [
        '⚡ <b><a href="{}">{}</a></b>'.format(html.escape(signal.url, quote=True), title),
        # One decimal, not zero: a 1-point move rounds to "70% -> 70%" and
        # reads as though nothing happened.
        "{} {:.1f}% → {:.1f}%  ({:+.1f} pts in {}m)".format(
            arrow,
            signal.prev_price * 100,
            signal.price * 100,
            signal.delta * 100 if signal.direction == "up" else -signal.delta * 100,
            minutes,
        ),
        "⚖️ Spread {}   \U0001f4b0 24h ${:,.0f}".format(spread, signal.volume_24h),
    ]
    return "\n".join(lines)


def send_alert(signal: Signal, token: str, chat_id: str, window_seconds: int) -> bool:
    """Post one alert. Returns False on failure rather than raising.

    One market failing to send shouldn't abort the rest of the run - the alert
    claim is already spent either way.
    """
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": format_alert(signal, window_seconds),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    request = urllib.request.Request(API_URL.format(token=token), data=payload)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        # Never log the response body verbatim; on auth failures Telegram
        # echoes the request, which would put the token in CloudWatch.
        log.error("telegram send failed for %s: HTTP %s", signal.market_id, exc.code)
    except (urllib.error.URLError, TimeoutError) as exc:
        log.error("telegram send failed for %s: %s", signal.market_id, exc)
    return False
