output "function_name" {
  description = "Lambda function name. Use with `aws logs tail /aws/lambda/<name> --follow`."
  value       = aws_lambda_function.poller.function_name
}

output "log_group" {
  description = "CloudWatch log group for the poller."
  value       = aws_cloudwatch_log_group.lambda.name
}

output "state_table" {
  description = "DynamoDB table holding the price-history snapshot and per-market alert claims."
  value       = aws_dynamodb_table.state.name
}

output "schedule" {
  description = "How often the poller runs."
  value       = aws_cloudwatch_event_rule.schedule.schedule_expression
}

output "telegram_parameters" {
  description = "SSM parameters to fill in after the first apply; they hold a placeholder until then."
  value = [
    aws_ssm_parameter.telegram_bot_token.name,
    aws_ssm_parameter.telegram_chat_id.name,
  ]
}
