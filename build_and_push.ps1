param(
    [string]$Tag,
    [string]$Region = "us-east-1",
    [string]$RepositoryName = "contract-aggregator-tool",
    [string]$Dockerfile = "Dockerfile",
    [switch]$PushLatest
)

$ErrorActionPreference = "Stop"

function Get-GitTag {
    try {
        $sha = git rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $sha) { return $sha.Trim() }
    } catch {
    }
    return "latest"
}

function Get-AwsAccountId {
    $accountId = aws sts get-caller-identity --query Account --output text
    if (-not $accountId) {
        throw "Unable to determine AWS account id. Make sure AWS credentials are configured."
    }
    return $accountId.Trim()
}

function Assert-CommandExists {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

Assert-CommandExists aws
Assert-CommandExists docker

if (-not $Tag) {
    $Tag = Get-GitTag
}

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerfilePath = Join-Path $ProjectDir $Dockerfile
if (-not (Test-Path $DockerfilePath)) {
    throw "Dockerfile was not found: $DockerfilePath"
}

$RepoUrl = "$((Get-AwsAccountId)).dkr.ecr.$Region.amazonaws.com/$RepositoryName"
$ImageTag = "${RepoUrl}:$Tag"

Write-Host "Using repository: $RepoUrl"
Write-Host "Using tag: $Tag"

Write-Host "Logging Docker into ECR..."
$registry = ($RepoUrl -split "/")[0]
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registry

Write-Host "Building production image..."
docker build --file $DockerfilePath --tag $ImageTag $ProjectDir

Write-Host "Pushing image tag $Tag..."
docker push $ImageTag

if ($PushLatest) {
    Write-Host "Tagging and pushing latest..."
    docker tag $ImageTag "${RepoUrl}:latest"
    docker push "${RepoUrl}:latest"
}
