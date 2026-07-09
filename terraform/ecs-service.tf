resource "aws_ecs_service" "web" {
  name                              = "contract-aggregator-web"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.web.arn
  desired_count                     = 1
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 120

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8000
  }

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

  depends_on = [
    aws_lb_listener.web
  ]
}
