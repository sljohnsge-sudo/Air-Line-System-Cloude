# Tick and Book - build the three Docker images on this PC and save them as one
# file to carry to the server (16/11/2026).
#
#   powershell -ExecutionPolicy Bypass -File deploy\build-images.ps1
#
# Makes deploy\out\tickandbook-<date-time>\ with:
#   tickandbook-images.tar   the 3 images (backend, website, admin site)
#   docker-compose.yml       the same file as in the repo
#   load-on-server.sh        loads the images and starts everything on the server
#   SHA256.txt               checksum, to confirm the copy on the server is complete
# backend\.env is NOT included (it holds passwords): copy the server's own .env
# by hand (deploy\README.md lists the settings it needs).
#
# Needs Docker Desktop running (it needs WSL - deploy\README.md, step 0).
param(
    [string]$Tag = (Get-Date -Format 'yyyyMMdd-HHmm'),
    [string]$Prefix = 'tickbook'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Write-Host "== Checking Docker" -ForegroundColor Cyan
& docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running. Start Docker Desktop and wait until it says 'Engine running'." -ForegroundColor Red
    Write-Host "If it never starts: WSL is missing - see deploy\README.md, step 0." -ForegroundColor Red
    exit 1
}

$images = @(
    @{ Name = "$Prefix/airline-backend";        Context = 'backend';  Args = @() },
    @{ Name = "$Prefix/airline-frontend";       Context = 'frontend'; Args = @() },
    @{ Name = "$Prefix/airline-admin-frontend"; Context = 'frontend'; Args = @('--build-arg', 'SITE=admin', '--build-arg', 'NGINX_CONF=nginx.admin.conf') }
)
foreach ($img in $images) {
    Write-Host "== Building $($img.Name):$Tag" -ForegroundColor Cyan
    $cmd = @('build', '--platform', 'linux/amd64', '-t', "$($img.Name):latest", '-t', "$($img.Name):$Tag") + $img.Args + @($img.Context)
    & docker @cmd
    if ($LASTEXITCODE -ne 0) { Write-Host "Build failed: $($img.Name)" -ForegroundColor Red; exit 1 }
}

$out = Join-Path $repo "deploy\out\tickandbook-$Tag"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$tar = Join-Path $out 'tickandbook-images.tar'
Write-Host "== Saving images to $tar" -ForegroundColor Cyan
$tags = $images | ForEach-Object { "$($_.Name):latest"; "$($_.Name):$Tag" }
& docker save -o $tar @tags
if ($LASTEXITCODE -ne 0) { Write-Host "docker save failed" -ForegroundColor Red; exit 1 }

Copy-Item (Join-Path $repo 'docker-compose.yml') $out
Copy-Item (Join-Path $repo 'deploy\load-on-server.sh') $out
$hash = (Get-FileHash $tar -Algorithm SHA256).Hash.ToLower()
"$hash  tickandbook-images.tar" | Set-Content -Path (Join-Path $out 'SHA256.txt') -Encoding ascii
$sizeMb = [math]::Round((Get-Item $tar).Length / 1MB)

Write-Host ""
Write-Host "Done. Copy this folder to the server:" -ForegroundColor Green
Write-Host "  $out  ($sizeMb MB)"
Write-Host "Then on the server, in the repo folder (where backend/.env is):"
Write-Host "  bash load-on-server.sh /path/to/tickandbook-images.tar"
