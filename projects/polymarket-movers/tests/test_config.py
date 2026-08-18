import pytest

from config import DEFAULT_BLOCK_TAG_SLUGS, DEFAULT_TAG_SLUGS, ConfigError, load

REQUIRED = {
    "DDB_TABLE": "polymarket-movers-state",
    "SSM_BOT_TOKEN_PARAM": "/polymarket-movers/telegram/bot-token",
    "SSM_CHAT_ID_PARAM": "/polymarket-movers/telegram/chat-id",
}


def env(**overrides):
    e = dict(REQUIRED)
    e.update(overrides)
    return e


def test_defaults_are_applied_when_only_required_vars_are_set():
    cfg = load(env())
    assert cfg.gamma_api_base == "https://gamma-api.polymarket.com"
    assert cfg.tag_slugs == DEFAULT_TAG_SLUGS
    assert cfg.block_tag_slugs == DEFAULT_BLOCK_TAG_SLUGS
    assert cfg.window_seconds == 300
    assert cfg.price_move_threshold == 0.05
    assert cfg.min_volume_24h == 5_000.0
    assert cfg.min_volume_total == 50_000.0
    assert cfg.min_liquidity == 1_000.0
    assert cfg.max_spread == 0.10
    assert cfg.alert_ttl_seconds == 86_400
    assert cfg.log_level == "INFO"


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_each_required_var_is_enforced(missing):
    e = env()
    del e[missing]
    with pytest.raises(ConfigError, match=missing):
        load(e)


def test_overrides_are_read_from_the_environment():
    cfg = load(env(PRICE_MOVE_THRESHOLD="0.02", MIN_VOLUME_24H_USD="250", LOG_LEVEL="debug"))
    assert cfg.price_move_threshold == 0.02
    assert cfg.min_volume_24h == 250.0
    assert cfg.log_level == "DEBUG"


def test_unparseable_number_names_the_offending_variable():
    with pytest.raises(ConfigError, match="PRICE_MOVE_THRESHOLD"):
        load(env(PRICE_MOVE_THRESHOLD="loads"))


def test_unparseable_integer_names_the_offending_variable():
    with pytest.raises(ConfigError, match="WINDOW_SECONDS"):
        load(env(WINDOW_SECONDS="five minutes"))


def test_trailing_slash_is_stripped_from_the_api_base():
    assert load(env(GAMMA_API_BASE="https://example.com/")).gamma_api_base == "https://example.com"


# --- comma-separated lists ------------------------------------------------


def test_tag_slugs_are_split_and_trimmed():
    assert load(env(TAG_SLUGS=" politics , crypto ")).tag_slugs == ("politics", "crypto")


def test_block_tag_slugs_are_lowercased():
    assert load(env(BLOCK_TAG_SLUGS="Sports, Recurring")).block_tag_slugs == (
        "sports",
        "recurring",
    )


def test_blank_list_falls_back_to_the_defaults():
    assert load(env(TAG_SLUGS="   ")).tag_slugs == DEFAULT_TAG_SLUGS
    assert load(env(BLOCK_TAG_SLUGS="")).block_tag_slugs == DEFAULT_BLOCK_TAG_SLUGS


def test_list_of_only_separators_is_rejected():
    with pytest.raises(ConfigError, match="TAG_SLUGS"):
        load(env(TAG_SLUGS=",,,"))


# --- derived defaults -----------------------------------------------------


def test_realert_threshold_defaults_to_the_detection_threshold():
    # Re-alerting should cost as much movement as the first alert did, so
    # retuning one number retunes both.
    assert load(env(PRICE_MOVE_THRESHOLD="0.08")).realert_move_threshold == 0.08


def test_realert_threshold_can_be_set_independently():
    cfg = load(env(PRICE_MOVE_THRESHOLD="0.08", REALERT_MOVE_THRESHOLD="0.03"))
    assert (cfg.price_move_threshold, cfg.realert_move_threshold) == (0.08, 0.03)


def test_alert_floor_defaults_to_the_window():
    assert load(env(WINDOW_SECONDS="600")).alert_floor_seconds == 600
    assert load(env(WINDOW_SECONDS="600", ALERT_FLOOR_SECONDS="60")).alert_floor_seconds == 60
