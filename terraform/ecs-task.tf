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
        },
        {
          name  = "RAG_DATA_SOURCE"
          value = "auto"
        },
        {
          name  = "RAG_EMBEDDING_PROVIDER"
          value = "bedrock"
        },
        {
          name  = "RAG_EMBEDDING_MODEL"
          value = "cohere.embed-english-v3"
        },
        {
          name  = "RAG_MIN_SCORE"
          value = "0.20"
        },
        {
          name  = "RAG_TOP_K"
          value = "5"
        },
        {
          name  = "RAG_LLM_PROVIDER"
          value = "bedrock"
        },
        {
          name  = "RAG_LLM_MODEL"
          value = "deepseek.v3.2"
        },
        {
          name  = "RAG_MAX_TOKENS"
          value = "1200"
        },
        {
          name  = "AWS_DEFAULT_REGION"
          value = var.aws_region
        },
        {
          name  = "EVA_URL"
          value = "https://mvendor.cgieva.com/Vendor/public/AllOpportunities.jsp"
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
