resource "aws_security_group" "rds" {
  name        = "contract-aggregator-rds"
  description = "Allow PostgreSQL from ECS"
  vpc_id      = aws_default_vpc.default.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "main" {
  name = "contract-aggregator-db"

  subnet_ids = [
    aws_default_subnet.default_a.id,
    aws_default_subnet.default_b.id
  ]
}
