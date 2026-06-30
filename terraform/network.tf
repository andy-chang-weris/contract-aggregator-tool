data "aws_availability_zones" "available" {}

resource "aws_default_vpc" "default" {}

resource "aws_default_subnet" "default_a" {
  availability_zone = data.aws_availability_zones.available.names[0]
}

resource "aws_default_subnet" "default_b" {
  availability_zone = data.aws_availability_zones.available.names[1]
}

resource "aws_security_group" "web" {
  name        = "contract-aggregator-web"
  description = "Allow HTTP traffic"
  vpc_id      = aws_default_vpc.default.id

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_internet_gateway" "default" {
  vpc_id = aws_default_vpc.default.id
}

resource "aws_default_route_table" "default" {
  default_route_table_id = aws_default_vpc.default.default_route_table_id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.default.id
  }
}


resource "aws_network_acl" "app" {
  vpc_id = aws_default_vpc.default.id

  ingress {
    protocol   = "tcp"
    rule_no    = 100
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 8000
    to_port    = 8000
  }

  ingress {
    protocol   = "tcp"
    rule_no    = 110
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 1024
    to_port    = 65535
  }

  ingress {
    protocol   = "udp"
    rule_no    = 115
    action     = "allow"
    cidr_block = aws_default_vpc.default.cidr_block
    from_port  = 1024
    to_port    = 65535
  }

  ingress {
    protocol   = "udp"
    rule_no    = 120
    action     = "allow"
    cidr_block = aws_default_vpc.default.cidr_block
    from_port  = 53
    to_port    = 53
  }

  ingress {
    protocol   = "tcp"
    rule_no    = 130
    action     = "allow"
    cidr_block = aws_default_vpc.default.cidr_block
    from_port  = 5432
    to_port    = 5432
  }

  egress {
    protocol   = "tcp"
    rule_no    = 90
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 80
    to_port    = 80
  }

  egress {
    protocol   = "tcp"
    rule_no    = 100
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 443
    to_port    = 443
  }

  egress {
    protocol   = "udp"
    rule_no    = 110
    action     = "allow"
    cidr_block = aws_default_vpc.default.cidr_block
    from_port  = 53
    to_port    = 53
  }

  egress {
    protocol   = "tcp"
    rule_no    = 120
    action     = "allow"
    cidr_block = "0.0.0.0/0"
    from_port  = 1024
    to_port    = 65535
  }

  egress {
    protocol   = "tcp"
    rule_no    = 130
    action     = "allow"
    cidr_block = aws_default_vpc.default.cidr_block
    from_port  = 5432
    to_port    = 5432
  }
}

resource "aws_network_acl_association" "app_a" {
  network_acl_id = aws_network_acl.app.id
  subnet_id      = aws_default_subnet.default_a.id
}

resource "aws_network_acl_association" "app_b" {
  network_acl_id = aws_network_acl.app.id
  subnet_id      = aws_default_subnet.default_b.id
}
