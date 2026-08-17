[CmdletBinding()]
param(
    [string]$Tag = "0.2.6"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$registry = "crpi-5zyp5pdzn7yrv5oo.cn-beijing.personal.cr.aliyuncs.com"
$image = "$registry/zcc_0811/chat_robot:$Tag"
$bundleName = "chat_robot-deploy-$Tag-amd64"
$distDir = Join-Path $projectRoot "dist"
$bundleDir = Join-Path $distDir $bundleName
$zipPath = Join-Path $distDir "$bundleName.zip"
$imageTarName = "chat_robot-$Tag-amd64.tar"
$imageTarPath = Join-Path $bundleDir $imageTarName

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker command was not found."
}
docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Local image was not found: $image"
}
if (Test-Path -LiteralPath $bundleDir) {
    throw "Bundle directory already exists: $bundleDir"
}
if (Test-Path -LiteralPath $zipPath) {
    throw "Bundle archive already exists: $zipPath"
}

New-Item -ItemType Directory -Path $bundleDir -Force | Out-Null

Write-Host "Exporting image to $imageTarName"
docker save --output $imageTarPath $image
if ($LASTEXITCODE -ne 0) {
    throw "docker save failed."
}

$composeSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "compose.yaml") -Encoding UTF8
$composeImagePattern = '(?m)^(\s*image:\s*)[^\r\n]+$'
if (-not [regex]::IsMatch($composeSource, $composeImagePattern)) {
    throw "compose.yaml does not contain an image declaration."
}
$packagedCompose = [regex]::Replace(
    $composeSource,
    $composeImagePattern,
    ('$1' + $image),
    1
)

$templateEnvPath = Join-Path $projectRoot ".env.docker.example"
if (-not (Test-Path -LiteralPath $templateEnvPath)) {
    throw "Configuration template was not found: $templateEnvPath"
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$composePath = Join-Path $bundleDir "compose.yaml"
[System.IO.File]::WriteAllText($composePath, $packagedCompose, $utf8NoBom)
$envExamplePath = Join-Path $bundleDir ".env.docker.example"
$envExample = Get-Content -Raw -LiteralPath $templateEnvPath -Encoding UTF8
[System.IO.File]::WriteAllText($envExamplePath, $envExample, $utf8NoBom)

$deploySh = @'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker version >/dev/null
docker compose version >/dev/null
if [ ! -f .env.docker ]; then
  echo "ERROR: .env.docker was not found in $(pwd)." >&2
  echo "Copy the .env.docker from the currently running 0.2.1 deployment here, then run deploy.sh again." >&2
  exit 1
fi
cp .env.docker ".env.docker.backup-$(date +%Y%m%d-%H%M%S)"
set_env() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" .env.docker; then
    sed -i "s/^${key}=.*$/${key}=${value}/" .env.docker
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env.docker
  fi
}
require_env() {
  key="$1"
  if ! grep -Eq "^${key}=.+" .env.docker; then
    echo "ERROR: ${key} is missing or empty in .env.docker." >&2
    exit 1
  fi
}
require_env JJT_CALLBACK_TOKEN
require_env JJT_ENCODING_AES_KEY
require_env LLM_API_KEY
require_env LLM_MODEL
require_env LLM_BASE_URL
set_env APP_ENV production
set_env ENABLE_MOCK_API false
set_env ENABLE_JJT_CALLBACK true
set_env ENABLE_REAL_RESPONSE_SEND true
set_env ENABLE_AUTO_CHAT_WORKFLOW true
set_env JJT_MESSAGE_DATA_DIR /app/data/messages
set_env DATABASE_URL sqlite:////app/data/jjt_bot.db
docker load -i "__IMAGE_TAR__"
docker compose up -d
docker compose ps
echo "Health URL: http://127.0.0.1:${HOST_PORT:-8000}/health"
'@.Replace("__IMAGE_TAR__", $imageTarName)
[System.IO.File]::WriteAllText((Join-Path $bundleDir "deploy.sh"), $deploySh, $utf8NoBom)

$deployPs1 = @'
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
docker version | Out-Null
docker compose version | Out-Null
if (-not (Test-Path -LiteralPath ".env.docker")) {
    throw "Copy .env.docker from the currently running 0.2.1 deployment here, then run deploy.ps1 again."
}
Copy-Item -LiteralPath ".env.docker" -Destination (".env.docker.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath ".env.docker" -Encoding UTF8)
function Get-EnvValue([string]$Key) {
    $prefix = "$Key="
    foreach ($line in $lines) {
        if ($line.StartsWith($prefix)) { return $line.Substring($prefix.Length) }
    }
    return $null
}
function Set-EnvValue([string]$Key, [string]$Value) {
    $prefix = "$Key="
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].StartsWith($prefix)) {
            $lines[$index] = "$Key=$Value"
            return
        }
    }
    $lines.Add("$Key=$Value")
}
foreach ($key in @("JJT_CALLBACK_TOKEN", "JJT_ENCODING_AES_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")) {
    if ([string]::IsNullOrWhiteSpace((Get-EnvValue $key))) { throw "$key is missing or empty in .env.docker." }
}
Set-EnvValue "APP_ENV" "production"
Set-EnvValue "ENABLE_MOCK_API" "false"
Set-EnvValue "ENABLE_JJT_CALLBACK" "true"
Set-EnvValue "ENABLE_REAL_RESPONSE_SEND" "true"
Set-EnvValue "ENABLE_AUTO_CHAT_WORKFLOW" "true"
Set-EnvValue "JJT_MESSAGE_DATA_DIR" "/app/data/messages"
Set-EnvValue "DATABASE_URL" "sqlite:////app/data/jjt_bot.db"
[System.IO.File]::WriteAllLines((Join-Path $PSScriptRoot ".env.docker"), $lines, (New-Object System.Text.UTF8Encoding($false)))
docker load -i "__IMAGE_TAR__"
docker compose up -d
docker compose ps
Write-Host "Health URL: http://127.0.0.1:8000/health"
'@.Replace("__IMAGE_TAR__", $imageTarName)
[System.IO.File]::WriteAllText((Join-Path $bundleDir "deploy.ps1"), $deployPs1, $utf8NoBom)

$readme = @"
chat_robot $Tag - AMD64 offline deployment bundle

Requirements:
- Linux AMD64 or Windows AMD64 server
- Docker with Docker Compose v2

Linux deployment:
  unzip $bundleName.zip
  cd $bundleName
  cp /path/to/current-0.2.1/.env.docker ./.env.docker
  bash deploy.sh

Windows PowerShell deployment:
  Copy-Item C:\path\to\current-0.2.1\.env.docker .\.env.docker
  .\deploy.ps1

The deployment script backs up the existing configuration and enables:
  ENABLE_REAL_RESPONSE_SEND=true
  ENABLE_AUTO_CHAT_WORKFLOW=true

It stops before changing the container if callback or LLM configuration is missing.

Health check:
  curl http://127.0.0.1:8000/health

Service callback path:
  /jjt-robot/callback

Operations:
  docker compose ps
  docker compose logs -f --tail=200
  docker compose restart
  docker compose down

Data is stored in the Docker named volume chat-robot-data. Do not run
docker compose down -v unless the database and message data may be deleted.

SECURITY: this bundle contains no deployment credentials. Keep the copied
.env.docker private and do not commit or upload it publicly.
"@
[System.IO.File]::WriteAllText((Join-Path $bundleDir "README-DEPLOY.txt"), $readme, $utf8NoBom)

$digest = docker image inspect $image --format '{{.Id}}'
[System.IO.File]::WriteAllText(
    (Join-Path $bundleDir "IMAGE.txt"),
    "$image`nLocal image ID: $digest`nPlatform: linux/amd64`n",
    $utf8NoBom
)

$checksumLines = Get-ChildItem -LiteralPath $bundleDir -File | ForEach-Object {
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
    "$($hash.Hash.ToLowerInvariant())  $($_.Name)"
}
[System.IO.File]::WriteAllLines(
    (Join-Path $bundleDir "SHA256SUMS.txt"),
    $checksumLines,
    $utf8NoBom
)

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $bundleDir,
    $zipPath,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $true
)

Write-Host "Created: $zipPath"
