# Contract Tool Infrastructure Roadmap

## Goal

Deploy the ai-agent contract application as a user-accessible web service with persistent data, managed secrets, containerized runtime, and observable logs.

The staging principle is:

```text
Each stage should create stable interfaces and avoid unnecessary rework. Earlier stages can be changed only when a later requirement exposes that a piece was temporary scaffolding, unsafe, or incorrectly placed.
```

## Stage 1: Minimal User-Accessible Deployment

### Purpose

Prove the full runtime path works end to end:

```text
User -> app endpoint -> ECS Fargate container -> Secrets Manager -> RDS PostgreSQL
                              -> ECR image pull
                              -> CloudWatch logs
```

### Recommended Stage 1 Architecture

Use the diagram in `stage-1-minimal-architecture.svg`.

Stage 1 should include:

- ECR repository containing the app image.
- ECS Fargate cluster, task definition, and service.
- A user-facing endpoint.
- RDS PostgreSQL for persistent data.
- Secrets Manager for DB connection details.
- CloudWatch Logs for ECS/app logs.
- IAM execution role for ECR pulls and logs.
- IAM task role for app-level secret access.
- Security groups that permit only the required flows.
- Network ACLs/routes that allow ECS to reach AWS APIs and RDS.

### Minimal Endpoint Decision

There are two possible definitions of "minimal":

```text
Fastest minimal: ECS task with public IP and port 8000 exposed.
Stable minimal: ALB in front of ECS, even if HTTP-only at first.
```

For actual end users, prefer the stable minimal version with an ALB. It avoids teaching users to rely on a changing task public IP and gives a clean path to HTTPS, health checks, and scaling.

### Stage 1 Completion Criteria

Use `stage-1-component-test-map.svg` as the checklist.

Stage 1 is complete only when:

- `terraform validate` succeeds.
- `terraform plan` has no unexpected destroys or replacements.
- ECS service has `desired=1`, `running=1`, `pending=0`.
- ECS task pulls the image from ECR without `ResourceInitializationError`.
- CloudWatch receives logs from the running task.
- App can read the DB secret.
- App can connect to RDS.
- User endpoint returns a successful response.
- One real app workflow succeeds from the browser.

### Stage 1 Stable Interfaces

Treat these as contracts that later stages should preserve if possible:

- ECR repository name and image tag/deploy contract.
- App container port, unless the app itself changes.
- Secrets Manager secret name and JSON shape.
- RDS database name/user expectations.
- CloudWatch log group naming convention.
- Terraform resource naming convention.

### Stage 1 Temporary Scaffolding

These may be replaced or tightened later:

- Default VPC usage.
- Public ECS tasks.
- HTTP-only endpoint.
- Single ECS task desired count.
- Minimal alarms/metrics.
- Broad outbound security group rules.
- Manual image tagging/deployment flow.

## Stage 2: Production Ingress and Safer Runtime Boundary

### Purpose

Make the app safer and more stable for real users without changing the application contract.

### Additions

- Application Load Balancer if not already added in Stage 1.
- ALB target group and health check path.
- ECS service attached to the target group.
- HTTPS listener with ACM certificate.
- Route 53 DNS record for a stable domain.
- Security group hardening:
  - ALB accepts `80/443` from users.
  - ECS accepts app port only from the ALB security group.
  - RDS accepts `5432` only from ECS security group.
- ECS deployment circuit breaker.
- Explicit container health check if the app exposes a health endpoint.

### Stage 2 May Need To Touch Stage 1

Stage 2 may need to modify the ECS service to attach the ALB target group. This is expected. The stable contract is the app runtime, not the initial direct-public-networking shortcut.

### Completion Criteria

- User accesses the app through the domain name.
- HTTPS certificate is valid.
- ALB target health is healthy.
- ECS tasks no longer require direct public inbound access.
- Existing Stage 1 tests still pass.

## Stage 3: Reliability and Operations

### Purpose

Make the service easier to operate and recover.

### Additions

- ECS desired count 2 across two AZs.
- Autoscaling policy based on CPU/memory or request count.
- CloudWatch alarms:
  - ECS running task count below desired.
  - ALB 5xx errors.
  - ALB target unhealthy count.
  - RDS CPU/storage/connections.
- RDS automated backup retention policy.
- RDS deletion protection for non-dev environments.
- Dashboard for service health.
- Structured app logs if not already present.

### Completion Criteria

- One task can be stopped and service recovers automatically.
- Alarms trigger in controlled tests.
- Dashboard shows request, task, and DB health.
- RDS backup policy is visible and restorable.

## Stage 4: Private Networking and AWS Service Endpoints

### Purpose

Reduce public exposure and make network boundaries cleaner.

### Additions

- Purpose-built VPC if default VPC becomes a blocker.
- Public subnets for ALB.
- Private app subnets for ECS tasks.
- Private DB subnets for RDS.
- NAT Gateway for outbound internet or VPC endpoints for AWS services.
- VPC endpoints for ECR API, ECR Docker, CloudWatch Logs, Secrets Manager, and S3 gateway endpoint for ECR image layers.

### Stage 4 May Need Migration

Moving from default VPC/public ECS to a custom VPC/private ECS is a real architecture migration. It can require changing subnet groups, ECS service networking, route tables, security groups, and possibly RDS migration strategy.

### Completion Criteria

- ECS tasks have no public IP.
- ALB remains the only public ingress.
- ECS can pull images and write logs through NAT or VPC endpoints.
- RDS remains private and reachable only from ECS.
- Stage 1 and Stage 2 user-facing behavior remains unchanged.

## Stage 5: CI/CD and Release Safety

### Purpose

Make deployments repeatable and reversible.

### Additions

- Build and push Docker image from CI.
- Terraform plan in CI for infrastructure changes.
- ECS deploy workflow using immutable image tags.
- Rollback procedure.
- Separate dev/prod state and variables.
- Remote Terraform backend with locking.

### Completion Criteria

- A code change can be built, pushed, deployed, and rolled back through documented commands or CI.
- Terraform state is remote and locked.
- Production and development are separated.

## Immediate Next Decision

Before implementing Stage 1 completely, decide whether Stage 1 should use:

1. Direct public ECS task access on port 8000.
2. ALB in front of ECS from the start.

Recommendation: use ALB from the start if real users will touch it. Use direct ECS public IP only as a temporary smoke test.
