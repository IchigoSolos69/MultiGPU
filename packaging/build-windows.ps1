param([string]$Version = '0.2.0', [string]$Compiler, [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRoot = Join-Path $repository 'build'
$downloads = Join-Path $buildRoot 'downloads'
$stage = Join-Path $buildRoot ('windows-' + [guid]::NewGuid().ToString('N'))
$payload = Join-Path $stage 'payload'
New-Item -ItemType Directory -Path $downloads,$payload -Force | Out-Null
$uvVersion = '0.12.22'
$pythonVersion = '3.12.15'
$uvHash = 'ea1397797a0ca15f63516dd0f49c2dde9776db9be5861cab152ebe8ad199894d'
$uvArchive = Join-Path $downloads 'uv-0.12.22-windows-x64.zip'
if (!(Test-Path -LiteralPath $uvArchive)) {
    Invoke-WebRequest -Uri "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $uvArchive -UseBasicParsing
}
if ((Get-FileHash -LiteralPath $uvArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvHash) { throw 'Pinned uv archive checksum mismatch.' }
Expand-Archive -LiteralPath $uvArchive -DestinationPath (Join-Path $stage 'uv') -Force
$uvSource = Get-ChildItem -LiteralPath (Join-Path $stage 'uv') -Filter uv.exe -Recurse -File | Select-Object -First 1
if (!$uvSource) { throw 'The verified uv release contains no uv.exe.' }
Copy-Item -LiteralPath $uvSource.FullName -Destination (Join-Path $payload 'uv.exe')
$uv = Join-Path $payload 'uv.exe'
& $uv python install $pythonVersion --install-dir (Join-Path $payload 'runtime') --no-bin --no-registry --no-config --cache-dir (Join-Path $buildRoot 'uv-cache')
if ($LASTEXITCODE) { throw 'Pinned managed Python preparation failed.' }
$python = Get-ChildItem -LiteralPath (Join-Path $payload 'runtime') -Filter python.exe -Recurse -File | Where-Object { $_.Directory.Name -notin @('Scripts','venv') } | Select-Object -First 1
if (!$python) { throw 'The managed runtime contains no python.exe.' }
$actualVersion = (& $python.FullName -c 'import platform; print(platform.python_version())').Trim()
if ($actualVersion -ne $pythonVersion) { throw "Expected Python $pythonVersion; found $actualVersion." }

# An explicit allowlist prevents credentials, exports, .env, tests and local state from entering the installer.
$appFiles = @('__init__.py','__main__.py','artifacts.py','benchmark.py','catalog.py','connectors.py','datasets.py','desktop.py','environment.py','imports.py','planner.py','prediction.py','recipes.py','server.py','service.py','store.py','worker.py')
$appRoot = Join-Path $payload 'multigpu'
New-Item -ItemType Directory -Path (Join-Path $appRoot 'static') -Force | Out-Null
foreach ($name in $appFiles) { Copy-Item -LiteralPath (Join-Path $repository "multigpu/$name") -Destination $appRoot }
foreach ($name in @('app.js','index.html','styles.css')) { Copy-Item -LiteralPath (Join-Path $repository "multigpu/static/$name") -Destination (Join-Path $appRoot 'static') }
foreach ($name in @('start-desktop.ps1','start.ps1','start.cmd','README.md')) { Copy-Item -LiteralPath (Join-Path $repository $name) -Destination $payload }
New-Item -ItemType Directory -Path (Join-Path $payload 'packaging') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launcher-common.ps1') -Destination (Join-Path $payload 'packaging')
$manifest = [ordered]@{ version=$Version; uv_version=$uvVersion; uv_archive_sha256=$uvHash; python_version=$pythonVersion; created_utc=[DateTime]::UtcNow.ToString('o'); files=@() }
$manifest.files = @(Get-ChildItem -LiteralPath $payload -Recurse -File | ForEach-Object { @{ path=$_.FullName.Substring($payload.Length+1).Replace('\','/'); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); bytes=$_.Length } })
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $payload 'build-manifest.json') -Encoding UTF8
Set-Content -LiteralPath (Join-Path $buildRoot 'latest-windows-payload.txt') -Value $payload -Encoding UTF8
Write-Output "Prepared Windows payload: $payload"
if ($PrepareOnly) { return }
if (!$Compiler) {
    $localCompiler = Join-Path $buildRoot 'tools/InnoSetup/ISCC.exe'
    if (Test-Path -LiteralPath $localCompiler) { $Compiler=$localCompiler }
    elseif (Get-Command ISCC.exe -ErrorAction SilentlyContinue) { $Compiler=(Get-Command ISCC.exe).Source }
    else { throw 'Inno Setup 6.7.3 is required. Run packaging/prepare-compiler.ps1 or pass -Compiler. The payload is ready.' }
}
$compilerVersion = (Get-Item -LiteralPath $Compiler).VersionInfo.ProductVersion
if ($compilerVersion -notlike '6.7.3*') { throw "Expected Inno Setup 6.7.3; found $compilerVersion." }
& $Compiler "/DAppVersion=$Version" "/DPayloadDir=$payload" "/DOutputRoot=$(Join-Path $repository 'dist')" (Join-Path $PSScriptRoot 'multigpu.iss')
if ($LASTEXITCODE) { throw 'Installer compilation failed.' }

