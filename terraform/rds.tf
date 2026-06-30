resource "aws_db_instance" "postgres" {
  identifier = "contract-aggregator-db"

  engine         = "postgres"
  engine_version = "16"

  instance_class = "db.t4g.micro"

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = "govcontracts"
  username = "postgres"
  password = random_password.db_password.result

  db_subnet_group_name = aws_db_subnet_group.main.name

  vpc_security_group_ids = [
    aws_security_group.rds.id
  ]

  publicly_accessible = false

  skip_final_snapshot = true

  deletion_protection = false
}
