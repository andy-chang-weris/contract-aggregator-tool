resource "aws_ecs_service" "web" {
  name            = "contract-aggregator-web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    assign_public_ip = true

    security_groups = [
      aws_security_group.web.id
    ]

    subnets = [
      aws_default_subnet.default_a.id,
      aws_default_subnet.default_b.id
    ]
  }
}
