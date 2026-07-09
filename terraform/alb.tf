resource "aws_lb" "web" {
  name               = "contract-aggregator-web"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]

  subnets = [
    aws_default_subnet.default_a.id,
    aws_default_subnet.default_b.id
  ]
}

resource "aws_lb_target_group" "web" {
  name        = "contract-aggregator-web"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_default_vpc.default.id

  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200-399"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "web" {
  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}
