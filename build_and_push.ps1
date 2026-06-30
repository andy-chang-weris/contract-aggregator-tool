param(
    [string]$Tag,
    [string]$Region = "us-east-1",
    [string]$RepositoryName = "contract-aggregator-tool",
    [string]$ComposeFile = "docker-compose.yml",
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
$ComposePath = Join-Path $ProjectDir $ComposeFile
if (-not (Test-Path $ComposePath)) {
    throw "Compose file was not found: $ComposePath"
}

$RepoUrl = "$((Get-AwsAccountId)).dkr.ecr.$Region.amazonaws.com/$RepositoryName"
$ImageTag = "${RepoUrl}:$Tag"

Write-Host "Using repository: $RepoUrl"
Write-Host "Using tag: $Tag"

Write-Host "Logging Docker into ECR..."
$registry = ($RepoUrl -split "/")[0]
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registry

Write-Host "Building image with Docker Compose..."
$env:APP_IMAGE_NAME = $ImageTag
try {
    docker compose -f $ComposePath build app
} finally {
    Remove-Item Env:APP_IMAGE_NAME -ErrorAction SilentlyContinue
}

Write-Host "Pushing image tag $Tag..."
docker push $ImageTag

if ($PushLatest) {
    Write-Host "Tagging and pushing latest..."
    docker tag $ImageTag "${RepoUrl}:latest"
    docker push "${RepoUrl}:latest"
}