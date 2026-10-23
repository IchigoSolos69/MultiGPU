function Get-MultiGPUEnvironment {
    param([string]$AppRoot, [string]$EnvironmentRoot)
    $uv=Join-Path $AppRoot 'uv.exe'
    $managed=Join-Path $AppRoot 'runtime'
    $bundled=$null
    if (Test-Path -LiteralPath $managed) {
        $bundled=Get-ChildItem -LiteralPath $managed -Filter python.exe -Recurse -File | Where-Object { $_.Directory.Name -ne 'Scripts' } | Select-Object -First 1
    }
    $sourceEnvironment=Join-Path $AppRoot '.venv'
    if (!$bundled -and (Test-Path -LiteralPath (Join-Path $sourceEnvironment 'Scripts/python.exe'))) { $EnvironmentRoot=$sourceEnvironment }
    $python=Join-Path $EnvironmentRoot 'Scripts/python.exe'
    if (!(Test-Path -LiteralPath $python)) {
        New-Item -ItemType Directory -Path (Split-Path $EnvironmentRoot) -Force | Out-Null
        if ($bundled -and (Test-Path -LiteralPath $uv)) {
            & $uv venv $EnvironmentRoot --python $bundled.FullName --no-config --no-python-downloads --cache-dir (Join-Path (Split-Path $EnvironmentRoot) 'uv-cache')
        } else {
            $basePython=$env:MULTIGPU_PYTHON
            if (!$basePython) {
                $codexPython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
                if (Test-Path -LiteralPath $codexPython) { $basePython=$codexPython }
                elseif (Get-Command py.exe -ErrorAction SilentlyContinue) { $basePython=(& py.exe -3 -c 'import sys; print(sys.executable)').Trim() }
                elseif (Get-Command python.exe -ErrorAction SilentlyContinue) { $basePython=(Get-Command python.exe).Source }
            }
            if (!$basePython -or !(Test-Path -LiteralPath $basePython)) { throw 'No usable Python was found. Install the bundled Windows release, or set MULTIGPU_PYTHON to Python 3.11 or newer.' }
            & $basePython -m venv $EnvironmentRoot
        }
        if ($LASTEXITCODE -or !(Test-Path -LiteralPath $python)) { throw 'The application environment could not be prepared.' }
    }
    $check=(& $python -c 'import sys; print("managed" if sys.prefix != sys.base_prefix and sys.version_info >= (3,11) else "invalid")').Trim()
    if ($LASTEXITCODE -or $check -ne 'managed') { throw 'The application requires its own virtual environment with Python 3.11 or newer.' }
    $env:PYTHONPATH=$AppRoot
    $env:PYTHONNOUSERSITE='1'
    $env:PYTHONDONTWRITEBYTECODE='1'
    $env:PYTHONUNBUFFERED='1'
    if (Test-Path -LiteralPath $uv) { $env:MULTIGPU_UV=$uv }
    return @{ Python=$python; Environment=$EnvironmentRoot; Uv=$uv }
}

function ConvertTo-MultiGPUArgument {
    param([string]$Value)
    # Standard Windows argv quoting, including a trailing backslash before a quote.
    return '"' + (($Value -replace '(\\*)"','$1$1\"') -replace '(\\+)$','$1$1') + '"'
}

