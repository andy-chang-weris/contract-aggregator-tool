variable "aws_region" {
  type    = string
  default = "us-east-2"
}

variable "github_owner" {
  type    = string
  default = "wincvh"
}

variable "github_repo" {
  type    = string
  default = "agent"
}

variable "bucket_name" {
  type = string
}

variable "app_name" {
  type    = string
  default = "agent"
}
