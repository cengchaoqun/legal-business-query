$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath "$taskRoot\frontend"
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
