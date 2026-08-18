data "aws_region" "current" {}

locals {
  # AWS requires the singular form for rate(1 minute).
  schedule = "rate(${var.poll_rate_minutes} ${var.poll_rate_minutes == 1 ? "minute" : "minutes"})"
}

# ---------------------------------------------------------------------------
# State: one table, two kinds of item
#
#   pk = "price-history"   the compressed rolling-window snapshot
#   pk = "alert#<market>"  the last alerted price, expired by TTL
#
# The snapshot is a single item so a poll costs one read and one write however
# many markets are tracked.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "state" {
  name         = "${var.name}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# ---------------------------------------------------------------------------
# Telegram credentials
#
# Created with a placeholder and then left alone: the real values are set out
# of band with `aws ssm put-parameter --overwrite`, so they never enter the
# repo or the Terraform state. ssm.py refuses to start if it still reads
# REPLACE_ME, rather than failing later on a 401 from Telegram.
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "telegram_bot_token" {
  name  = "/${var.name}/telegram/bot-token"
  type  = "SecureString"
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "telegram_chat_id" {
  name  = "/${var.name}/telegram/chat-id"
  type  = "SecureString"
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------------------
# Execution role - the table it owns, its own two parameters, nothing else
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name}-lambda"
  description        = "Execution role for the ${var.name} poller."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "StateTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.state.arn]
  }

  statement {
    sid     = "TelegramCredentials"
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.telegram_bot_token.arn,
      aws_ssm_parameter.telegram_chat_id.arn,
    ]
  }

  # Reading a SecureString decrypts it. ViaService keeps this from unlocking
  # anything outside SSM.
  statement {
    sid       = "DecryptCredentials"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.name}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "state-and-credentials"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# ---------------------------------------------------------------------------
# The poller
# ---------------------------------------------------------------------------
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/src"
  output_path = "${path.module}/build/lambda.zip"
}

# Declared explicitly so retention is managed. Lambda would otherwise create
# this on first invocation with no expiry, and Terraform would never own it.
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "poller" {
  function_name = var.name
  description   = "Alerts on fast-moving Polymarket news markets."
  role          = aws_iam_role.lambda.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  filename = data.archive_file.lambda.output_path
  # Redeploys whenever the zipped source changes, so a code-only edit still
  # ships through the normal plan/apply flow.
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = merge(
      {
        DDB_TABLE           = aws_dynamodb_table.state.name
        SSM_BOT_TOKEN_PARAM = aws_ssm_parameter.telegram_bot_token.name
        SSM_CHAT_ID_PARAM   = aws_ssm_parameter.telegram_chat_id.name
      },
      var.env,
    )
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.name}-schedule"
  description         = "Runs the ${var.name} poller."
  schedule_expression = local.schedule
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = var.name
  arn       = aws_lambda_function.poller.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.poller.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
