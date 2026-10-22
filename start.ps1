param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$launchArgs = @('-m', 'multigpu', '--port', "$Port")
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if ($NoBrowser) { $launchArgs += '--no-browser' }
if ($env:MULTIGPU_PYTHON) {
    & $env:MULTIGPU_PYTHON @launchArgs
} elseif (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
    & '.venv\Scripts\python.exe' @launchArgs
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @launchArgs
} elseif (Test-Path -LiteralPath $bundledPython) {
    & $bundledPython @launchArgs
} else {
    & python @launchArgs
}
exit $LASTEXITCODE
