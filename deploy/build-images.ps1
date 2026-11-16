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
#
# By default the images are built from the COMMITTED code (a clean copy of the
# current git commit), so what goes to the server is exactly what is in git;
# uncommitted changes in this folder are left out and listed. -WorkingCopy
# builds from this folder as it is, uncommitted changes included.
param(
    [string]$Tag = (Get-Date -Format 'yyyyMMdd-HHmm'),
    [string]$Prefix = 'tickbook',
    [switch]$WorkingCopy
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

$commit = (& git rev-parse --short HEAD).Trim()
if ($WorkingCopy) {
    $src = $repo
    $commit = "$commit-local"
    Write-Host "== Building from this folder AS IT IS (uncommitted changes included)" -ForegroundColor Yellow
} else {
    $src = Join-Path $env:TEMP "tickandbook-build-$commit"
    if (Test-Path $src) { Remove-Item -Recurse -Force $src }
    New-Item -ItemType Directory -Path $src | Out-Null
    $archive = "$src.tar"
    & git -c core.autocrlf=false archive --format=tar -o $archive HEAD
    if ($LASTEXITCODE -ne 0) { Write-Host "git archive failed" -ForegroundColor Red; exit 1 }
    & tar -xf $archive -C $src
    Remove-Item $archive
    Write-Host "== Building from commit $commit (a clean copy of what is in git)" -ForegroundColor Cyan
    $dirty = & git status --porcelain
    if ($dirty) {
        Write-Host "   NOT included (uncommitted in this folder):" -ForegroundColor Yellow
        $dirty | ForEach-Object { Write-Host "     $_" -ForegroundColor Yellow }
    }
}

$images = @(
    @{ Name = "$Prefix/airline-backend";        Context = 'backend';  Args = @() },
    @{ Name = "$Prefix/airline-frontend";       Context = 'frontend'; Args = @() },
    @{ Name = "$Prefix/airline-admin-frontend"; Context = 'frontend'; Args = @('--build-arg', 'SITE=admin', '--build-arg', 'NGINX_CONF=nginx.admin.conf') }
)
foreach ($img in $images) {
    Write-Host "== Building $($img.Name):$Tag" -ForegroundColor Cyan
    $cmd = @('build', '--platform', 'linux/amd64', '--label', "org.opencontainers.image.revision=$commit",
             '-t', "$($img.Name):latest", '-t', "$($img.Name):$Tag") + $img.Args + @((Join-Path $src $img.Context))
    & docker @cmd
    if ($LASTEXITCODE -ne 0) { Write-Host "Build failed: $($img.Name)" -ForegroundColor Red; exit 1 }
}

$out = Join-Path $repo "deploy\out\tickandbook-$Tag-$commit"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$tar = Join-Path $out 'tickandbook-images.tar'
Write-Host "== Saving images to $tar" -ForegroundColor Cyan
$tags = $images | ForEach-Object { "$($_.Name):latest"; "$($_.Name):$Tag" }
& docker save -o $tar @tags
if ($LASTEXITCODE -ne 0) { Write-Host "docker save failed" -ForegroundColor Red; exit 1 }

Copy-Item (Join-Path $src 'docker-compose.yml') $out
Copy-Item (Join-Path $src 'deploy\load-on-server.sh') $out
"Built from commit $commit on $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Set-Content -Path (Join-Path $out 'BUILT-FROM.txt') -Encoding ascii
if (-not $WorkingCopy) { Remove-Item -Recurse -Force $src }
$hash = (Get-FileHash $tar -Algorithm SHA256).Hash.ToLower()
"$hash  tickandbook-images.tar" | Set-Content -Path (Join-Path $out 'SHA256.txt') -Encoding ascii
$sizeMb = [math]::Round((Get-Item $tar).Length / 1MB)

Write-Host ""
Write-Host "Done. Copy this folder to the server:" -ForegroundColor Green
Write-Host "  $out  ($sizeMb MB)"
Write-Host "Then on the server, in the repo folder (where backend/.env is):"
Write-Host "  bash load-on-server.sh /path/to/tickandbook-images.tar"
