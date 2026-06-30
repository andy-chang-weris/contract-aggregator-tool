# powershell -ExecutionPolicy Bypass -File .\start_local_chat.ps1
param(
    [string]$Model = "qwen2.5:1.5b",
    [switch]$RestartOllama,
    [int]$FrontendPort = 8000,
    [int]$ProxyPort = 5000,
    [string]$DbSecretName = "contract-aggregator/dev/database",
    [int]$ChatPort = 5055
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentDir = Join-Path $ProjectDir "agent"
$ModelDir = Join-Path $AgentDir "data\ollama_models"
$ProxyServer = Join-Path $ProjectDir "proxy.py"
$ChatServer = Join-Path $AgentDir "interactive-ui\chat_server.py"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Test-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    return $false
}

function Test-PortOpen {
    param([int]$Port)

    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $connection
}

if (-not (Test-Path $AgentDir)) {
    throw "Expected agent directory was not found: $AgentDir"
}

if (-not (Test-Path $ProxyServer)) {
    throw "Expected proxy server was not found: $ProxyServer"
}

if (-not (Test-Path $ChatServer)) {
    throw "Expected chat server was not found: $ChatServer"
}

Write-Step "Locating Ollama"
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCommand) {
    $OllamaExe = $ollamaCommand.Source
} else {
    $OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
}

if (-not (Test-Path $OllamaExe)) {
    throw "Ollama executable was not found. Install Ollama first, then rerun this script."
}

New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null
$env:OLLAMA_MODELS = $ModelDir
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:GIT_HTTP_PROXY = ""
$env:GIT_HTTPS_PROXY = ""

Write-Step "Starting Ollama"
$ollamaProcesses = Get-Process | Where-Object { $_.ProcessName -match '^ollama' }
if ($ollamaProcesses -and $RestartOllama) {
    $ollamaProcesses | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $ollamaProcesses = $null
}

if (-not $ollamaProcesses) {
    Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
} else {
    Write-Host "Ollama is already running."
}

if (-not (Test-HttpReady -Url "http://localhost:11434/api/tags" -TimeoutSeconds 60)) {
    throw "Ollama did not become ready at http://localhost:11434. Try closing Ollama and rerun with -RestartOllama."
}

Write-Step "Checking model $Model"
$models = & $OllamaExe list
if ($models -notmatch [regex]::Escape($Model)) {
    Write-Host "Model is missing. Pulling $Model. This can take several minutes on first run."
    & $OllamaExe pull $Model
} else {
    Write-Host "Model is already installed."
}

Write-Host ""
Write-Host "Ollama is ready:"
Write-Host "  Ollama: http://localhost:11434"
Write-Host "  Model:  $Model"

Write-Step "Starting frontend server"
if (Test-PortOpen -Port $FrontendPort) {
    Write-Host "Port $FrontendPort is already in use. Assuming frontend server is already running."
} else {
    $quotedProjectDir = "'" + ($ProjectDir -replace "'", "''") + "'"
    $frontendCommand = "python -m http.server $FrontendPort -d $quotedProjectDir"
    Start-Process powershell.exe -WorkingDirectory $ProjectDir -WindowStyle Normal -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $frontendCommand
    )
}

$frontendUrl = "http://localhost:$FrontendPort"

Write-Step "Starting proxy API"
if (Test-PortOpen -Port $ProxyPort) {
    Write-Host "Port $ProxyPort is already in use. Assuming proxy.py is already running."
} else {
    $quotedDbSecretName = "'" + ($DbSecretName -replace "'", "''") + "'"
    $proxyCommand = "`$env:PORT='$ProxyPort'; `$env:DB_SECRET_NAME=$quotedDbSecretName; if (-not `$env:AWS_DEFAULT_REGION) { `$env:AWS_DEFAULT_REGION='us-east-1' }; python proxy.py"
    Start-Process powershell.exe -WorkingDirectory $ProjectDir -WindowStyle Normal -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $proxyCommand
    )
}

$proxyUrl = "http://localhost:$ProxyPort"

Write-Host ""
Write-Host "Frontend is available at:"
Write-Host "  $frontendUrl"
Write-Host ""
Write-Host "Proxy API is available at:"
Write-Host "  $proxyUrl"
Write-Host "  $proxyUrl/health"
Write-Step "Starting chat API"
if (Test-PortOpen -Port $ChatPort) {
    Write-Host "Port $ChatPort is already in use. Assuming chat API is already running."
} else {
    $chatCommand = "`$env:CHAT_SERVER_PORT='$ChatPort'; python interactive-ui\chat_server.py"
    Start-Process powershell.exe -WorkingDirectory $AgentDir -WindowStyle Normal -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $chatCommand
    )
}

$chatUrl = "http://localhost:$ChatPort/api/chat"

Write-Host ""
Write-Host "In the web UI, set the proxy URL field to $proxyUrl."
Write-Host "Chat API is available at:"
Write-Host "  $chatUrl"
