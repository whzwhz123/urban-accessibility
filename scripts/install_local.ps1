$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js LTS first.' }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.12 first.' }
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. No existing data was changed.' }
}
& $python -m pip install -r (Join-Path $root 'Back_end/requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& npm.cmd ci --legacy-peer-deps
if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
Write-Host 'Installation complete. Open Start.cmd to launch. This installer did not run experiments or collect data.'
