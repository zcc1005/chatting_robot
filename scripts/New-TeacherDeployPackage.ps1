[CmdletBinding()]
param(
    [string]$Tag = "0.2.0"
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

$currentEnvPath = Join-Path $projectRoot ".env"
$templateEnvPath = Join-Path $projectRoot ".env.docker.example"
if (-not (Test-Path -LiteralPath $currentEnvPath)) {
    throw "Current configuration was not found: $currentEnvPath"
}

$currentValues = @{}
foreach ($line in Get-Content -LiteralPath $currentEnvPath -Encoding UTF8) {
    if ($line -match '^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$') {
        $currentValues[$Matches[1]] = $Matches[2]
    }
}

$overrides = @{
    "JJT_MESSAGE_DATA_DIR" = "/app/data/messages"
    "DATABASE_URL" = "sqlite:////app/data/jjt_bot.db"
    "APP_ENV" = "production"
    "ENABLE_MOCK_API" = "false"
    "ENABLE_JJT_CALLBACK" = "true"
    "ENABLE_REAL_RESPONSE_SEND" = "true"
}

$envLines = foreach ($line in Get-Content -LiteralPath $templateEnvPath -Encoding UTF8) {
    if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
        $key = $Matches[1]
        $value = $Matches[2]
        if ($currentValues.ContainsKey($key)) {
            $value = $currentValues[$key]
        }
        if ($overrides.ContainsKey($key)) {
            $value = $overrides[$key]
        }
        "$key=$value"
    }
    else {
        $line
    }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$composePath = Join-Path $bundleDir "compose.yaml"
[System.IO.File]::WriteAllText($composePath, $packagedCompose, $utf8NoBom)
$envPath = Join-Path $bundleDir ".env.docker"
[System.IO.File]::WriteAllLines($envPath, $envLines, $utf8NoBom)

$deploySh = @'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker version >/dev/null
docker compose version >/dev/null
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
  bash deploy.sh

Windows PowerShell deployment:
  .\deploy.ps1

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

SECURITY: .env.docker contains deployment credentials. Send this bundle only
through a private trusted channel. Do not commit it or upload it publicly.
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
