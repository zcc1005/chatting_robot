$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VirtualEnv = Join-Path $ProjectRoot ".venv"
$ActivateScript = Join-Path $VirtualEnv "Scripts\Activate.ps1"

if (-not (Test-Path -LiteralPath $ActivateScript -PathType Leaf)) {
    Write-Error "未找到 .venv。请先运行: python -m venv .venv"
    exit 1
}

Set-Location -LiteralPath $ProjectRoot
. $ActivateScript
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
