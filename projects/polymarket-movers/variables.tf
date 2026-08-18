variable "name" {
  description = "Base name for every resource in this project. Changing it requires the matching rename in bootstrap/main.tf, where the CI apply role is scoped to these ARNs."
  type        = string
  default     = "polymarket-movers"
}

variable "poll_rate_minutes" {
  description = "How often the poller runs. The rolling window needs several samples to span it, so this should stay well below window_seconds."
  type        = number
  default     = 1
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 14
}

variable "lambda_memory_mb" {
  description = "Lambda memory. Also buys CPU, which matters on the 5-minutely Gamma refresh: that response is several MB of JSON to parse."
  type        = number
  default     = 512
}

variable "lambda_timeout_seconds" {
  description = "Lambda timeout. A price-only poll takes ~1s; the Gamma refresh takes ~20s, so this is headroom for the slow path."
  type        = number
  default     = 120
}

variable "env" {
  description = "Extra environment variables for the Lambda, merged over the defaults. Use this to retune thresholds (PRICE_MOVE_THRESHOLD, MAX_SPREAD, ...) without editing main.tf."
  type        = map(string)
  default     = {}
}
