<#
.SYNOPSIS
    One-time (and re-runnable) setup for the AI Manufacturing SOP Compliance Checker on Windows.

.DESCRIPTION
    Creates the Python virtual environment, installs backend and frontend
    dependencies, writes a .env if one does not exist, migrates the database and
    seeds the synthetic dataset.

    Native PowerShell - no WSL, no Git Bash, no make. Safe to run repeatedly:
    nothing already present is overwritten or deleted unless you pass -Reset.

.PARAMETER NoSeed
    Skip dataset generation and seeding.

.PARAMETER Reset
    Delete the existing database, generated dataset and uploads first.

.PARAMETER BackendOnly
    Skip the npm install step.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

.EXAMPLE
    .\scripts\setup.ps1 -Reset
#>
[CmdletBinding()]
param(
    [switch]$NoSeed,
    [switch]$Reset,
    [switch]$BackendOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $Root '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "    $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "    $Text" -ForegroundColor Yellow }

# --------------------------------------------------------------------------
# Python: 3.11 or newer is required (the code uses modern typing syntax).
# --------------------------------------------------------------------------
function Resolve-Python {
    $candidates = @()
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($v in @('3.14', '3.13', '3.12', '3.11')) { $candidates += @{ Exe = 'py'; Args = @("-$v") } }
    }
    foreach ($name in @('python3.14', 'python3.13', 'python3.12', 'python3.11', 'python3', 'python')) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { $candidates += @{ Exe = $name; Args = @() } }
    }
    foreach ($c in $candidates) {
        try {
            # Avoid double quotes in the -c payload: Windows PowerShell 5.1
            # strips them when invoking a native executable, turning the
            # format string into invalid Python syntax.
            $out = & $c.Exe @($c.Args + @('-c', 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))')) 2>$null
        } catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
        $parts = $out.Trim().Split('.')
        if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) {
            return @{ Exe = $c.Exe; Args = $c.Args; Version = $out.Trim() }
        }
    }
    return $null
}

Write-Step 'Checking prerequisites'
$py = Resolve-Python
if (-not $py) {
    Write-Error @"
Python 3.11 or newer was not found.
Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH")
or run: winget install Python.Python.3.12
"@
}
Write-Ok "Python $($py.Version)"

$HasNode = [bool](Get-Command node -ErrorAction SilentlyContinue)
if ($HasNode) {
    Write-Ok "Node $(node --version)"
} elseif (-not $BackendOnly) {
    Write-Warn 'Node.js was not found - the frontend will be skipped. Install Node 18+ from https://nodejs.org/'
    $BackendOnly = $true
}

# --------------------------------------------------------------------------
if ($Reset) {
    Write-Step 'Resetting generated state'
    foreach ($p in @((Join-Path $Root 'var'), (Join-Path $Root 'data\synthetic'))) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Ok "removed $p" }
    }
}

Write-Step 'Creating the Python virtual environment'
if (Test-Path $VenvPython) {
    Write-Ok '.venv already exists - reusing it'
} else {
    & $py.Exe @($py.Args + @('-m', 'venv', $VenvDir))
    if ($LASTEXITCODE -ne 0) { Write-Error 'Failed to create the virtual environment.' }
    Write-Ok "created $VenvDir"
}

Write-Step 'Installing backend dependencies'
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r (Join-Path $Root 'backend\requirements-dev.txt') --quiet
if ($LASTEXITCODE -ne 0) { Write-Error 'pip install failed.' }
Write-Ok 'backend dependencies installed'

# --------------------------------------------------------------------------
# .env is never overwritten: it may hold a real API key.
# --------------------------------------------------------------------------
Write-Step 'Configuring environment'
$EnvFile = Join-Path $Root '.env'
$EnvExample = Join-Path $Root '.env.example'
if (Test-Path $EnvFile) {
    Write-Ok '.env already exists - left untouched'
} else {
    $secret = & $VenvPython -c 'import secrets; print(secrets.token_urlsafe(48))'
    (Get-Content $EnvExample -Raw) -replace 'SECRET_KEY=.*', "SECRET_KEY=$secret" |
        Set-Content -Path $EnvFile -Encoding UTF8 -NoNewline
    Write-Ok '.env created from .env.example with a generated SECRET_KEY'
}

Write-Step 'Applying database migrations'
& $VenvPython (Join-Path $Root 'scripts\migrate.py')
if ($LASTEXITCODE -ne 0) { Write-Error 'Database migration failed.' }

if (-not $NoSeed) {
    Write-Step 'Generating the synthetic dataset'
    & $VenvPython (Join-Path $Root 'scripts\generate_dataset.py')
    if ($LASTEXITCODE -ne 0) { Write-Error 'Dataset generation failed.' }

    Write-Step 'Seeding the database'
    & $VenvPython (Join-Path $Root 'scripts\seed.py')
    if ($LASTEXITCODE -ne 0) { Write-Error 'Seeding failed.' }
}

if (-not $BackendOnly) {
    Write-Step 'Installing frontend dependencies'
    Push-Location (Join-Path $Root 'frontend')
    try {
        if (Test-Path 'package-lock.json') { npm ci } else { npm install }
        if ($LASTEXITCODE -ne 0) { Write-Error 'npm install failed.' }
    } finally {
        Pop-Location
    }
    Write-Ok 'frontend dependencies installed'
}

Write-Host ''
Write-Host '------------------------------------------------------------------' -ForegroundColor DarkGray
Write-Host ' Setup complete.' -ForegroundColor Green
Write-Host ''
Write-Host ' Start the application:   .\scripts\start.ps1'
Write-Host ' Then open:               http://localhost:5173'
Write-Host ''
Write-Host ' Demo accounts (synthetic data only):'
Write-Host '   admin    / Admin#12345      administrator'
Write-Host '   auditor  / Auditor#12345    auditor'
Write-Host '   reviewer / Reviewer#12345   reviewer'
Write-Host ''
Write-Host ' This tool is decision support. It does not certify compliance,' -ForegroundColor Yellow
Write-Host ' approve product release, or replace a qualified human reviewer.' -ForegroundColor Yellow
Write-Host '------------------------------------------------------------------' -ForegroundColor DarkGray
