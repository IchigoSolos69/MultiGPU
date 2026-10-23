param([string]$Installer, [string]$TestRoot)
$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!$Installer) { $Installer=Join-Path $repository 'dist/MultiGPU-0.4.0-Setup.exe' }
if (!$TestRoot) { $TestRoot=Join-Path $repository ('build/installer acceptance '+[guid]::NewGuid().ToString('N')) }
$TestRoot=[IO.Path]::GetFullPath($TestRoot)
if (!$TestRoot.StartsWith((Join-Path $repository 'build')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Installer acceptance must use an isolated directory inside this project build folder.' }
$app=Join-Path $TestRoot 'installed app'
$state=Join-Path $TestRoot 'retained state'
$uninstallKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{98EDDB21-1903-4CC7-A45B-23161D11D59B}_is1'
if (Test-Path -LiteralPath $uninstallKey) { throw 'A user installation of MultiGPU is already registered. Use a clean Windows VM for installer acceptance; this test will not overwrite it.' }
New-Item -ItemType Directory -Path $TestRoot,$state -Force | Out-Null
. (Join-Path $PSScriptRoot 'launcher-common.ps1')
$uninstaller=$null
try {
    $arguments=@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS',('/DIR='+$app),('/LOG='+(Join-Path $TestRoot 'install.log')))
    $line=($arguments | ForEach-Object { ConvertTo-MultiGPUArgument $_ }) -join ' '
    $setup=Start-Process -FilePath $Installer -ArgumentList $line -WindowStyle Hidden -Wait -PassThru
    if ($setup.ExitCode) { throw "Installer failed ($($setup.ExitCode)); inspect install.log." }
    $uninstaller=Join-Path $app 'unins000.exe'
    if (!(Test-Path -LiteralPath $uninstaller)) { throw 'The installer did not create its uninstaller.' }
    & (Join-Path $PSScriptRoot 'test-payload.ps1') -Payload $app -TestRoot $state -UsePayloadInPlace
    if ($LASTEXITCODE) { throw 'Installed runtime acceptance failed.' }
    $acceptance=Get-Content -LiteralPath (Join-Path $state 'acceptance-result.json') -Raw | ConvertFrom-Json
    $uninstall=Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -Wait -PassThru
    if ($uninstall.ExitCode) { throw "Uninstall failed ($($uninstall.ExitCode))." }
    $uninstaller=$null
    if (!(Test-Path -LiteralPath (Join-Path $acceptance.export_path 'manifest.json')) -or !(Test-Path -LiteralPath (Join-Path $state 'environment/pyvenv.cfg'))) { throw 'Uninstall removed retained exports or environment.' }
    if (Test-Path -LiteralPath $uninstallKey) { throw 'Uninstall left the test application registration.' }
    $result=@{passed=$true;installer=(Get-Item -LiteralPath $Installer).FullName;installer_sha256=(Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant();install_path=$app;retained_workspace=$acceptance.workspace;retained_export=$acceptance.export_path;runtime_version=$acceptance.python.version;system_python_required=$false;uninstall_retained_data=$true}
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $TestRoot 'installer-acceptance-result.json') -Encoding UTF8
    Write-Output ($result | ConvertTo-Json -Depth 5)
} finally {
    if ($uninstaller -and (Test-Path -LiteralPath $uninstaller)) {
        Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -Wait | Out-Null
    }
}
