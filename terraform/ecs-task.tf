resource "aws_ecs_task_definition" "web" {
  family                   = "contract-aggregator-tool"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  cpu    = 256
  memory = 512

  # Pull Docker Image & Write CloudWatch Logs
  execution_role_arn = aws_iam_role.ecs_execution.arn

  # Application Permissions & Secrets
  task_role_arn = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "web"
      image = "${aws_ecr_repository.app.repository_url}:${var.app_image_tag}"

      essential = true

      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
        }
      ]

      environment = [
        {
          name  = "DB_SECRET_NAME"
          value = aws_secretsmanager_secret.db.name
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"

        options = {
          awslogs-group         = aws_cloudwatch_log_group.web.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}
