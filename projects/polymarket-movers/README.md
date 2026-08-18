# polymarket-movers

Prediction markets as a news feed. Every minute this checks ~160 Polymarket
markets in news-relevant categories and posts anything making a sharp,
volume-backed move to a Telegram channel.

The bet is that a market repricing 8 points in four minutes usually knows
something before the headline does.

```
⚡ NATO downs another Russian drone by August 31?
🟢 40.5% → 47.2%  (+6.7 pts in 5m)
⚖️ Spread 4.3%   💰 24h $18,220
```

## How it works

```
EventBridge (1 min)
   └─> Lambda
         ├─ Gamma  /events   discovery: which markets, their tags, volume    (every 5 min)
         ├─ CLOB   /midpoints live prices                                    (every run)
         ├─ DynamoDB         rolling 5-minute price window + alert claims
         └─ Telegram         send
```

**Two data sources, deliberately.** Gamma is edge-cached for 300 seconds, so it
drives *discovery* — which markets exist, their tags, volume, liquidity — all of
which change slowly. The CLOB is uncached and batched, so it drives *prices*.
Polling Gamma for prices returns byte-identical payloads and measures nothing;
this was found by measurement, and splitting the sources also cut a poll from
~15s to ~0.1s.

**Detection is max-minus-min across the window**, not a point-to-point delta, so
a market that spikes and falls back inside the window still registers.

**Alerts dedupe by price distance, not elapsed time.** A market grinding
40 → 50 → 60 is a story developing and reports three times. One parked at 50
after a jump stays silent however long it sits there.

## Tuning

Every threshold is an environment variable, set through the `env` Terraform
variable — no code change, no new release. In `terraform.tfvars` or the
variable's default:

```hcl
env = {
  PRICE_MOVE_THRESHOLD = "0.08"   # need a bigger move to alert
  MAX_SPREAD           = "0.05"   # only tightly-quoted books
}
```

| Variable | Default | What it controls |
|---|---|---|
| `PRICE_MOVE_THRESHOLD` | `0.05` | Size of move that alerts, in probability points (`0.05` = 5 points) |
| `WINDOW_SECONDS` | `300` | How long a move has to happen in |
| `MIN_VOLUME_24H_USD` | `5000` | Is anyone trading it *today* |
| `MIN_VOLUME_TOTAL_USD` | `50000` | Has it *ever* traded meaningfully |
| `MIN_LIQUIDITY_USD` | `1000` | Is the order book deep enough to matter |
| `MAX_SPREAD` | `0.10` | Is the printed price real, relative to mid |
| `REALERT_MOVE_THRESHOLD` | = `PRICE_MOVE_THRESHOLD` | Further move needed before a market speaks again |
| `ALERT_FLOOR_SECONDS` | = `WINDOW_SECONDS` | Minimum gap between two alerts on one market |
| `TAG_SLUGS` | 11 categories | What to watch, comma-separated |
| `BLOCK_TAG_SLUGS` | 17 tags | What to discard, comma-separated |

**Which knob to reach for first.** Too few alerts → lower
`PRICE_MOVE_THRESHOLD`. Too many → raise it before touching anything else. Too
much noise from thin markets → raise `MIN_VOLUME_TOTAL_USD`. A whole category
of junk → add its tag to `BLOCK_TAG_SLUGS` rather than inventing a keyword rule;
tags are exact and keywords are not (`"nfl"` is a substring of `"inflation"`).

`POLL_RATE_MINUTES` is a Terraform variable only — it builds the EventBridge
schedule and never reaches the Lambda.

## Reading the logs

```sh
export AWS_PROFILE=quang-admin AWS_REGION=eu-west-2
aws logs tail /aws/lambda/polymarket-movers --follow --format short
```

Every run prints one line:

```
poll complete: {'tracked': 161, 'quoted': 161, 'windowed': 160, 'signals': 0,
                'sent': 0, 'suppressed': 0, 'failed': 0, 'capped': 0,
                'snapshot_bytes': 2593, 'elapsed_s': 0.09}
```

| Field | Means |
|---|---|
| `tracked` | Markets in the universe after every gate |
| `quoted` | How many the CLOB returned a live price for — a gap here means the CLOB dropped requests |
| `windowed` | Markets with a rolling window this run |
| `signals` | Cleared the move threshold |
| `sent` | Actually posted to Telegram |
| `suppressed` | Signalled, but hadn't moved far enough since their last alert |
| `failed` | Telegram rejected the send — check the bot is still a channel admin |
| `capped` | Signals beyond the 10-per-run cap; they alert on a later poll if still moving |
| `elapsed_s` | ~4s on a run that refreshes discovery, ~0.1s otherwise |

`signals` is the number to watch over time: it tells you whether the threshold
is set somewhere useful, and it accumulates whether or not anything sends.

**Errors are loud by design.** A missing or placeholder credential raises
`SecretsError` naming the parameter and the fix. Note that a failing invocation
appears **three times** — EventBridge invokes Lambda asynchronously, and async
invocations retry twice.

## Operating it

**Secrets** live only in SSM as SecureString, set out of band so they never
enter the repo or Terraform state. Terraform creates them holding `REPLACE_ME`
with `ignore_changes = [value]`, and the Lambda refuses to start on that
placeholder rather than failing later on a 401.

```sh
aws ssm put-parameter --name /polymarket-movers/telegram/bot-token \
  --type SecureString --value "<token>" --overwrite --region eu-west-2
aws ssm put-parameter --name /polymarket-movers/telegram/chat-id \
  --type SecureString --value "<chat id>" --overwrite --region eu-west-2
```

No redeploy needed — the next tick picks them up, because the bootstrap caches
only on success and so retries every invocation until it works. The bot must be
an **admin of the channel** or sends 401 with correct credentials.

**Pausing it** without destroying anything:

```sh
aws events disable-rule --name polymarket-movers-schedule --region eu-west-2
```

**Forcing an alert** to check the pipeline end to end — set the threshold
directly on the function rather than through two PR-and-apply cycles. The change
never enters Terraform state, so the next apply restores the declared config
regardless. Note the API **replaces the whole environment map**: omit a key and
you delete it.

```sh
aws lambda update-function-configuration --function-name polymarket-movers \
  --region eu-west-2 --environment 'Variables={DDB_TABLE=polymarket-movers-state,\
SSM_CHAT_ID_PARAM=/polymarket-movers/telegram/chat-id,\
SSM_BOT_TOKEN_PARAM=/polymarket-movers/telegram/bot-token,PRICE_MOVE_THRESHOLD=0.003}'
```

Re-run without the last key to restore.

**Don't `terraform apply` this project locally.** `archive_file` packages the
zip differently on Windows than on CI's Ubuntu, so a local plan always reports
`1 to change` on `source_code_hash` even when the source is identical. CI plans
are clean. Deploy through the pipeline.

## Cost

Under a dollar a month, dominated by DynamoDB writes — one compressed ~2.5KB
snapshot per minute. Lambda stays inside the perpetual free tier (~19k GB-seconds
against 400k), and SSM standard parameters are free.

## Layout

```
src/          config, gamma, clob, detector, state, telegram, ssm, handler
tests/        87 unit tests, run in CI with no AWS credentials
*.tf          the 11 resources
```

`detector.evaluate`, `handler.build_signals`, `state.encode_history` and
`telegram.format_alert` are pure functions, which is what makes the thresholds
testable without touching Polymarket or AWS.
