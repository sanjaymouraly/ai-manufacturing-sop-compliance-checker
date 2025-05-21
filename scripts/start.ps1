<#
.SYNOPSIS
    Starts the API and the web UI on Windows.

.DESCRIPTION
    Runs the FastAPI backend and the Vite dev server together, waits for the API
    health check before opening the UI, and shuts both down cleanly on Ctrl+C.

    Native PowerShell - no WSL required.

.PARAMETER BackendOnly
    Start only the API.

.PARAMETER Prod
    Build the frontend and serve it from the API as a single process on one port.

.PARAMETER ApiPort
    API port (default 8000).

.PARAMETER WebPort
    Vite dev server port (default 5173).

.EXAMPLE
    .\scripts\start.ps1

.EXAMPLE
    .\scripts\start.ps1 -Prod -ApiPort 8080
#>
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$Prod,
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'

function Write-Step { param([string]$Text) Write-Host "`n==> $Text" -ForegroundColor Cyan }

if (-not (Test-Path $VenvPython)) {
    Write-Error "No virtual environment found at $VenvPython. Run .\scripts\setup.ps1 first."
}

$script:Jobs = @()

function Stop-All {
    foreach ($p in $script:Jobs) {
        if ($p -and -not $p.HasExited) {
            try {
                # Kill the whole tree: uvicorn and vite both spawn children.
                & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null
            } catch {
                # Already gone, or taskkill is unavailable - fall back to the PID.
                Write-Verbose "taskkill failed for PID $($p.Id): $_"
                try { $p.Kill() } catch { Write-Verbose "PID $($p.Id) already exited." }
            }
        }
    }
}

try {
    if ($Prod -and -not $BackendOnly) {
        Write-Step 'Building the frontend for production'
        Push-Location $Frontend
        try {
            npm run build
            if ($LASTEXITCODE -ne 0) { Write-Error 'Frontend build failed.' }
        } finally { Pop-Location }
        $env:SERVE_FRONTEND = 'true'
        $env:FRONTEND_DIST = Join-Path $Frontend 'dist'
    }

    Write-Step "Starting the API on http://localhost:$ApiPort"
    $uvicornArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', "$ApiPort")
    if (-not $Prod) { $uvicornArgs += @('--reload', '--reload-dir', 'app') }
    $api = Start-Process -FilePath $VenvPython -ArgumentList $uvicornArgs `
        -WorkingDirectory $Backend -NoNewWindow -PassThru
    $script:Jobs += $api

    Write-Host '    waiting for the API to become healthy...' -NoNewline
    $healthy = $false
    foreach ($i in 1..60) {
        Start-Sleep -Milliseconds 500
        if ($api.HasExited) { Write-Host ''; Write-Error 'The API exited during startup. See the log above.' }
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/health" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $healthy = $true; break }
        } catch { Write-Host '.' -NoNewline }
    }
    Write-Host ''
    if (-not $healthy) { Write-Error 'The API did not become healthy in 30 seconds.' }
    Write-Host '    API is up' -ForegroundColor Green

    if ($Prod -and -not $BackendOnly) {
        Write-Host ''
        Write-Host "  Application: http://localhost:$ApiPort" -ForegroundColor Green
        Write-Host "  API docs:    http://localhost:$ApiPort/api/docs"
    } elseif (-not $BackendOnly) {
        Write-Step "Starting the web UI on http://localhost:$WebPort"
        # Keep the Vite proxy pointed at whichever API port was requested.
        $env:API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"
        $web = Start-Process -FilePath 'npm.cmd' -ArgumentList @('run', 'dev', '--', '--port', "$WebPort") `
            -WorkingDirectory $Frontend -NoNewWindow -PassThru
        $script:Jobs += $web
        Write-Host ''
        Write-Host "  Web UI:   http://localhost:$WebPort" -ForegroundColor Green
        Write-Host "  API docs: http://localhost:$ApiPort/api/docs"
    } else {
        Write-Host ''
        Write-Host "  API docs: http://localhost:$ApiPort/api/docs" -ForegroundColor Green
    }

    Write-Host ''
    Write-Host '  Sign in with  admin / Admin#12345  (synthetic demo data)'
    Write-Host '  Decision support only - this tool does not certify compliance.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Press Ctrl+C to stop.' -ForegroundColor DarkGray

    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($p in $script:Jobs) {
            if ($p.HasExited) {
                Write-Host "`nA process exited (code $($p.ExitCode)); shutting the rest down." -ForegroundColor Yellow
                return
            }
        }
    }
} finally {
    Write-Step 'Stopping'
    Stop-All
}
