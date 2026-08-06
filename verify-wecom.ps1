param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [switch]$SkipStorageCheck
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ActivateScript = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
$EnvFile = Join-Path $ProjectRoot ".env"
$VerifyScript = Join-Path $ProjectRoot "scripts\verify_wecom_callback.py"

if (-not (Test-Path -LiteralPath $ActivateScript -PathType Leaf)) {
    Write-Error "未找到 .venv，请先运行: python -m venv .venv"
    exit 1
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Write-Error "未找到 .env，请先运行: Copy-Item .env.example .env"
    exit 1
}

Set-Location -LiteralPath $ProjectRoot
. $ActivateScript

$Arguments = @($VerifyScript, "--base-url", $BaseUrl)
if ($SkipStorageCheck) {
    $Arguments += "--skip-storage-check"
}
python @Arguments
exit $LASTEXITCODE

