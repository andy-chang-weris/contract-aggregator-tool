resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/contract-aggregator-tool"
  retention_in_days = 7
}
