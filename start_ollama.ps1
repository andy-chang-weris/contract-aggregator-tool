param(
    [string]$Model = "qwen2.5:1.5b",
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ModelDir = Join-Path $AgentDir "data\ollama_models"
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

$OllamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if ($OllamaCommand) {
    $OllamaExe = $OllamaCommand.Source
} else {
    $OllamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
}

if (-not (Test-Path $OllamaExe)) {
    throw "Ollama executable was not found. Install Ollama first, then rerun this script."
}

$env:OLLAMA_MODELS = $ModelDir
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:GIT_HTTP_PROXY = ""
$env:GIT_HTTPS_PROXY = ""

$Running = Get-Process | Where-Object { $_.ProcessName -match '^ollama' }
if ($Running -and $Restart) {
    $Running | Stop-Process -Force -ErrorAction SilentlyContinue
    $Running = $null
} elseif ($Running) {
    Write-Host "Ollama is already running. Use -Restart to restart it with this model directory."
}

if (-not $Running) {
    Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

& $OllamaExe list
Write-Host "Ollama is serving $Model from $ModelDir"
