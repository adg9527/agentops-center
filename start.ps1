# AgentOps Center launcher (Windows PowerShell 5.1+)
#   .\start.ps1          build frontend if needed, serve everything at http://127.0.0.1:8000
#   .\start.ps1 -Dev     backend :8000 (new window) + Vite dev server with hot reload at http://127.0.0.1:5173
#   .\start.ps1 -Rebuild force a fresh frontend build
param([switch]$Dev, [switch]$Rebuild)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

Write-Host '[1/3] backend dependencies (uv sync)'
Push-Location "$root\backend"; uv sync | Out-Null; Pop-Location

Write-Host '[2/3] frontend dependencies'
if (-not (Test-Path "$root\frontend\node_modules")) { Push-Location "$root\frontend"; npm install; Pop-Location }

if ($Dev) {
    Write-Host '[3/3] dev mode: backend in a new window, Vite here -> http://127.0.0.1:5173'
    Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location '$root\backend'; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"
    Push-Location "$root\frontend"; npx vite; Pop-Location
} else {
    if ($Rebuild -or -not (Test-Path "$root\frontend\dist\.vite\manifest.json")) {
        Write-Host '[3/3] building frontend'
        Push-Location "$root\frontend"; npm run build; Pop-Location
    }
    Write-Host 'AgentOps Center -> http://127.0.0.1:8000  (Ctrl+C to stop)'
    Push-Location "$root\backend"; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000; Pop-Location
}
