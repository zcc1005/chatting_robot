[CmdletBinding()]
param(
    [string]$Tag = "0.2.8",
    [string]$Platform = "linux/amd64",
    [switch]$AlsoLatest
)

$ErrorActionPreference = "Stop"
$registry = "crpi-5zyp5pdzn7yrv5oo.cn-beijing.personal.cr.aliyuncs.com"
$repository = "$registry/zcc_0811/chat_robot"
$image = "${repository}:${Tag}"
$projectRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker command was not found. Install and start Docker Desktop first."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is not running. Start Docker Desktop first."
}

Push-Location $projectRoot
try {
    Write-Host "Building $image for $Platform"
    docker build --pull --platform $Platform --tag $image .
    if ($LASTEXITCODE -ne 0) {
        throw "Image build failed."
    }

    Write-Host "Pushing $image"
    docker push $image
    if ($LASTEXITCODE -ne 0) {
        throw "Image push failed. Run: docker login --username=alyzcc $registry"
    }

    if ($AlsoLatest) {
        $latestImage = "${repository}:latest"
        docker tag $image $latestImage
        docker push $latestImage
        if ($LASTEXITCODE -ne 0) {
            throw "The latest tag push failed."
        }
    }

    Write-Host "Done: $image"
}
finally {
    Pop-Location
}
