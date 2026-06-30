resource "random_password" "db_password" {
  length  = 32
  special = true

  override_special = "!#$%^&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "db" {
  name = "contract-aggregator/dev/database"
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id

  secret_string = jsonencode({
    host     = aws_db_instance.postgres.address
    port     = aws_db_instance.postgres.port
    database = aws_db_instance.postgres.db_name

    username = aws_db_instance.postgres.username

    password = random_password.db_password.result
  })
}
