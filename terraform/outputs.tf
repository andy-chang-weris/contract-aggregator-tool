output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "db_endpoint" {
  value = aws_db_instance.postgres.address
}

output "db_secret_name" {
  value = aws_secretsmanager_secret.db.name
}

output "app_url" {
  value = "http://${aws_lb.web.dns_name}"
}
