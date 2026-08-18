import json
import zlib
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from state import claim_key, decode_history, encode_history, try_claim


def test_history_round_trips():
    history = {"111": [(1000.0, 0.40), (1060.0, 0.47)], "222": [(1000.0, 0.10)]}
    assert decode_history(encode_history(history)) == history


def test_empty_history_round_trips():
    assert decode_history(encode_history({})) == {}


def test_samples_come_back_as_tuples_not_lists():
    # JSON has no tuple type, so decode has to restore them - detector.prune
    # unpacks `for ts, price in samples`.
    decoded = decode_history(encode_history({"1": [(1.0, 0.5)]}))
    assert decoded["1"] == [(1.0, 0.5)]


def test_missing_snapshot_yields_an_empty_window():
    assert decode_history(None) == {}
    assert decode_history(b"") == {}


def test_corrupt_snapshot_does_not_raise():
    assert decode_history(b"not zlib at all") == {}


def test_non_dict_payload_is_rejected():
    assert decode_history(zlib.compress(json.dumps([1, 2, 3]).encode())) == {}


def test_a_malformed_market_is_dropped_without_losing_the_rest():
    blob = zlib.compress(
        json.dumps({"good": [[1.0, 0.5]], "bad": [["not", "numbers"]]}).encode()
    )
    decoded = decode_history(blob)
    assert decoded == {"good": [(1.0, 0.5)]}


def test_boto3_binary_wrapper_is_unwrapped():
    class FakeBinary:
        def __init__(self, value):
            self.value = value

    blob = encode_history({"1": [(1.0, 0.5)]})
    assert decode_history(FakeBinary(blob)) == {"1": [(1.0, 0.5)]}


def test_compression_is_worth_the_trouble():
    # The reason for compressing at all: writes are billed per KB.
    history = {str(i): [(1000.0 + j, 0.5) for j in range(5)] for i in range(487)}
    raw = json.dumps(history, separators=(",", ":")).encode()
    assert len(encode_history(history)) < len(raw) / 4


# --- alert claims ---------------------------------------------------------


def test_claim_key_is_per_market_not_per_direction():
    # One reference price per market, whichever way it last moved: a reversal
    # clears the threshold on its own, and a whipsaw back to an already-alerted
    # price does not.
    assert claim_key("123") == "alert#123"


class FakeTable:
    """Captures the update_item call so the claim expression can be inspected."""

    def __init__(self):
        self.kwargs = None

    def update_item(self, **kwargs):
        self.kwargs = kwargs


def test_claim_brackets_the_alerted_price_by_the_threshold():
    tbl = FakeTable()
    assert try_claim(tbl, "123", 0.60, now=1_000.0, move_threshold=0.05,
                     floor_seconds=300, ttl_seconds=86_400)

    values = tbl.kwargs["ExpressionAttributeValues"]
    assert (values[":low"], values[":high"]) == (Decimal("0.55"), Decimal("0.65"))
    assert values[":floor"] == 700
    assert values[":exp"] == 87_400


def test_claim_prices_are_decimals_not_floats():
    # boto3's resource layer rejects floats outright, so this would fail at
    # runtime rather than compare wrongly.
    tbl = FakeTable()
    try_claim(tbl, "123", 0.60, now=1_000.0, move_threshold=0.05,
              floor_seconds=300, ttl_seconds=86_400)

    numeric = [v for k, v in tbl.kwargs["ExpressionAttributeValues"].items() if k != ":now"]
    assert not any(isinstance(v, float) for v in numeric)


def test_an_existing_claim_within_the_threshold_is_refused():
    class Refusing:
        def update_item(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
            )

    assert not try_claim(Refusing(), "123", 0.60, now=1_000.0, move_threshold=0.05,
                         floor_seconds=300, ttl_seconds=86_400)


def test_other_dynamodb_errors_are_not_swallowed():
    class Broken:
        def update_item(self, **kwargs):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceeded"}}, "UpdateItem")

    with pytest.raises(ClientError):
        try_claim(Broken(), "123", 0.60, now=1_000.0, move_threshold=0.05,
                  floor_seconds=300, ttl_seconds=86_400)
