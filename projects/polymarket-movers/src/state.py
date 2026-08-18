"""DynamoDB state: the rolling price snapshot and per-market alert claims.

Price history is a *single* compressed item rather than one item per market.
At ~487 markets that's one read and one write per invocation instead of ~487,
and compression keeps it around 7KB - which matters both for cost (writes are
billed per KB) and for staying well under the 400KB item ceiling.
"""

from __future__ import annotations

import json
import logging
import zlib
from decimal import Decimal
from typing import Dict, List, Mapping

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

HISTORY_KEY = "price-history"
_COMPRESSION_LEVEL = 6


def table(name: str):
    return boto3.resource("dynamodb").Table(name)


# --- serialisation (pure) -------------------------------------------------


def encode_history(history: Mapping) -> bytes:
    """Compact JSON, then zlib. Separators matter: they cut ~15% before zlib."""
    payload = json.dumps(history, separators=(",", ":")).encode("utf-8")
    return zlib.compress(payload, _COMPRESSION_LEVEL)


def decode_history(raw) -> Dict[str, List]:
    """Inverse of encode_history, tolerant of anything unreadable.

    A corrupt or truncated snapshot is not worth failing a run over - the worst
    case is that we rebuild the window over the next few polls.
    """
    if not raw:
        return {}
    data = getattr(raw, "value", raw)  # boto3 hands back a Binary wrapper
    try:
        parsed = json.loads(zlib.decompress(data).decode("utf-8"))
    except (zlib.error, ValueError, TypeError, UnicodeDecodeError):
        log.warning("price history unreadable, starting from an empty window")
        return {}
    if not isinstance(parsed, dict):
        return {}

    history: Dict[str, List] = {}
    for market_id, samples in parsed.items():
        try:
            history[str(market_id)] = [(float(ts), float(price)) for ts, price in samples]
        except (TypeError, ValueError):
            continue  # drop just the malformed market, keep the rest
    return history


# --- snapshot -------------------------------------------------------------


def load_history(tbl) -> Dict[str, List]:
    try:
        response = tbl.get_item(Key={"pk": HISTORY_KEY})
    except ClientError as exc:
        log.warning("could not read price history, starting fresh: %s", exc)
        return {}
    return decode_history((response.get("Item") or {}).get("data"))


def save_history(tbl, history: Mapping, now: float) -> int:
    """Persist the snapshot. Returns the compressed byte size, for logging."""
    blob = encode_history(history)
    tbl.put_item(Item={"pk": HISTORY_KEY, "data": blob, "updated_at": int(now)})
    return len(blob)


# --- alert claims ---------------------------------------------------------


def claim_key(market_id: str) -> str:
    return "alert#{}".format(market_id)


def try_claim(
    tbl,
    market_id: str,
    price: float,
    now: float,
    move_threshold: float,
    floor_seconds: int,
    ttl_seconds: int,
) -> bool:
    """Atomically claim the right to alert on this market at this price.

    Dedupe is by *distance from the last alerted price*, not elapsed time. A
    market grinding 40 -> 50 -> 60 is a story developing, and each leg is worth
    reporting; a market sitting at 50 after a 6-point jump is the same news
    still inside the window, and stays silent however long the window runs.

    Direction isn't in the key. The reference is the last alerted price
    whichever way it moved, so a genuine reversal clears the threshold on its
    own and a whipsaw back to an already-alerted price does not.
    """
    reference = Decimal(str(price))
    delta = Decimal(str(move_threshold))
    try:
        tbl.update_item(
            Key={"pk": claim_key(market_id)},
            UpdateExpression=(
                "SET last_alert_at = :now, last_alert_price = :price, expires_at = :exp"
            ),
            ConditionExpression=(
                "attribute_not_exists(pk) OR (last_alert_at < :floor "
                "AND (last_alert_price < :low OR last_alert_price > :high))"
            ),
            ExpressionAttributeValues={
                ":now": int(now),
                ":price": reference,
                ":exp": int(now + ttl_seconds),  # TTL - the table self-cleans
                ":floor": int(now - floor_seconds),
                ":low": reference - delta,
                ":high": reference + delta,
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
