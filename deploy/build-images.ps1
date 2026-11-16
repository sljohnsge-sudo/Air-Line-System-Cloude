# Tick and Book - build the two Docker images on this PC and save each as a
# file to carry to the server (16/11/2026).
#
#   powershell -ExecutionPolicy Bypass -File deploy\build-images.ps1
#
# Two images, each with its frontend and backend inside (separate folders
# /app/frontend and /app/backend), each run as two containers:
#   airline-site   -> airline-site.tar    airline_site_frontend (5176) + airline_site_backend
#   airline-admin  -> airline-admin.tar   airline_admin_frontend (5177) + airline_admin_backend
#
# Makes deploy\out\tickandbook-<date-time>-<commit>\ with:
#   airline-site.tar, airline-admin.tar   the two images
#   docker-compose.yml                    starts the four containers on the server
#   load-on-server.sh                     loads both files and starts them
#   SHA256.txt, BUILT-FROM.txt            checksums and the commit they came from
# backend\.env is NOT included (it holds passwords): the server has its own.
#
# By default the images are built from the COMMITTED code (a clean copy of the
# current git commit); uncommitted changes in this folder are left out and
# listed. -WorkingCopy builds from this folder as it is.
# Needs Docker Desktop running (it needs WSL - deploy\README.md, step 0).
param(
    [string]$Prefix = 'tickbook',
    [switch]$WorkingCopy
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'

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

$out = Join-Path $repo "deploy\out\tickandbook-$stamp-$commit"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$images = @(
    @{ Name = "$Prefix/airline-site";  Site = 'public'; File = 'airline-site.tar' },
    @{ Name = "$Prefix/airline-admin"; Site = 'admin';  File = 'airline-admin.tar' }
)
$sums = @()
foreach ($img in $images) {
    Write-Host "== Building $($img.Name):latest (SITE=$($img.Site))" -ForegroundColor Cyan
    & docker build --platform linux/amd64 --build-arg "SITE=$($img.Site)" --build-arg "REVISION=$commit" `
        -t "$($img.Name):latest" $src
    if ($LASTEXITCODE -ne 0) { Write-Host "Build failed: $($img.Name)" -ForegroundColor Red; exit 1 }
    $tar = Join-Path $out $img.File
    Write-Host "== Saving $($img.Name) to $tar" -ForegroundColor Cyan
    & docker save -o $tar "$($img.Name):latest"
    if ($LASTEXITCODE -ne 0) { Write-Host "docker save failed" -ForegroundColor Red; exit 1 }
    $sums += "$((Get-FileHash $tar -Algorithm SHA256).Hash.ToLower())  $($img.File)"
}

Copy-Item (Join-Path $src 'docker-compose.yml') $out
Copy-Item (Join-Path $src 'deploy\load-on-server.sh') $out
$sums | Set-Content -Path (Join-Path $out 'SHA256.txt') -Encoding ascii
"Built from commit $commit on $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Set-Content -Path (Join-Path $out 'BUILT-FROM.txt') -Encoding ascii
if (-not $WorkingCopy) { Remove-Item -Recurse -Force $src }

Write-Host ""
Write-Host "Done. Copy this folder to the server:" -ForegroundColor Green
Get-ChildItem $out | ForEach-Object { Write-Host ("  {0,-22} {1,8:N0} MB" -f $_.Name, ($_.Length / 1MB)) }
Write-Host "  $out"
Write-Host "Then on the server, in the folder with docker-compose.yml and backend/.env:"
Write-Host "  bash load-on-server.sh"
