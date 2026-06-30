variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "contract-aggregator-tool"
}

variable "ecr_repository_name" {
  type        = string
  default     = null
  description = "Name of the existing ECR repository that contains the application image. Defaults to project_name."
}

variable "app_image_tag" {
  type        = string
  default     = "latest"
  description = "ECR image tag to deploy."
}
