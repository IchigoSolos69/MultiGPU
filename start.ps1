param([int]$Port = 8765, [switch]$NoBrowser, [string]$DataDir)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'packaging/launcher-common.ps1')
$environmentRoot=Join-Path $PSScriptRoot '.venv'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime')) { $environmentRoot=Join-Path $env:LOCALAPPDATA 'MultiGPU/environment' }
$runtime=Get-MultiGPUEnvironment -AppRoot $PSScriptRoot -EnvironmentRoot $environmentRoot
$launchArgs = @('-m', 'multigpu', '--port', "$Port")
if ($NoBrowser) { $launchArgs += '--no-browser' }
if ($DataDir) { $launchArgs += @('--data-dir', $DataDir) }
& $runtime.Python @launchArgs
exit $LASTEXITCODE
