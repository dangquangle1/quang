import sys
from dataclasses import replace
from pathlib import Path

# src/ is zipped flat into the Lambda package, so modules import each other by
# bare name (`from config import Config`). Mirror that layout for the tests.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import DEFAULT_BLOCK_TAG_SLUGS, DEFAULT_TAG_SLUGS, Config  # noqa: E402

_BASE = Config(
    gamma_api_base="https://gamma-api.polymarket.com",
    clob_api_base="https://clob.polymarket.com",
    gamma_refresh_seconds=300,
    tag_slugs=DEFAULT_TAG_SLUGS,
    block_tag_slugs=DEFAULT_BLOCK_TAG_SLUGS,
    window_seconds=300,
    price_move_threshold=0.05,
    min_volume_24h=5_000.0,
    min_volume_total=50_000.0,
    min_liquidity=1_000.0,
    max_spread=0.10,
    realert_move_threshold=0.05,
    alert_floor_seconds=300,
    alert_ttl_seconds=86_400,
    ddb_table="polymarket-movers-state",
    ssm_bot_token_param="/polymarket-movers/telegram/bot-token",
    ssm_chat_id_param="/polymarket-movers/telegram/chat-id",
    log_level="INFO",
)


def make_config(**overrides) -> Config:
    """A Config with production defaults, so adding a field doesn't break tests."""
    return replace(_BASE, **overrides)
