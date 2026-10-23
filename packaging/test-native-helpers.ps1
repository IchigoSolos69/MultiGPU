$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'launcher-common.ps1')
$testRoot=Join-Path $repository ('build/native helper acceptance '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$first=Get-MultiGPUInstanceIdentity (Join-Path $testRoot 'one')
$same=Get-MultiGPUInstanceIdentity (Join-Path $testRoot 'one')
$other=Get-MultiGPUInstanceIdentity (Join-Path $testRoot 'two')
if($first.ActivationName -ne $same.ActivationName -or $first.MutexName -eq $other.MutexName -or $first.ActivationName -eq $other.ActivationName) { throw 'Instance isolation failed' }
$runtime=@{Python='C:\app\environment\Scripts\python.exe';BasePrefix='C:\app\runtime';AppVersion='0.4.0'}
$bootstrap=@{app=@{workspace_dir='C:\workspace';version='0.4.0';runtime_base_prefix='C:\app\runtime'};hardware=@{python=$runtime.Python}}
if(Test-MultiGPUControllerIdentity $bootstrap 'C:\workspace' $runtime) { throw 'Valid controller identity rejected' }
$bootstrap.app.version='old'
if((Test-MultiGPUControllerIdentity $bootstrap 'C:\workspace' $runtime) -notmatch 'version') { throw 'Old controller version accepted' }
$bootstrap.app.version='0.4.0'
$bootstrap.app.runtime_base_prefix='C:\another-runtime'
if((Test-MultiGPUControllerIdentity $bootstrap 'C:\workspace' $runtime) -notmatch 'runtime') { throw 'Wrong runtime accepted' }
$bootstrap.app.runtime_base_prefix='C:\app\runtime'
$bootstrap.hardware.python='C:\another-python.exe'
if((Test-MultiGPUControllerIdentity $bootstrap 'C:\workspace' $runtime) -notmatch 'environment') { throw 'Wrong controller Python accepted' }
$jobs=@(@{id='one';status='completed'},@{id='two';status='error'},@{id='three';status='paused'},@{id='four';status='interrupted'})
if(@(Get-MultiGPUNotificationChanges $jobs $null $true).Count) { throw 'Initial snapshot emitted notifications' }
if(@(Get-MultiGPUNotificationChanges $jobs @{} $false).Count) { throw 'Disabled notifications emitted alerts' }
if(@(Get-MultiGPUNotificationChanges $jobs @{} $true).Count -ne 3) { throw 'New completed runs or notification bound failed' }
if(@(Get-MultiGPUNotificationChanges @($jobs[0]) @{one='completed'} $true).Count) { throw 'Duplicate terminal-state notification emitted' }
$app=(Get-Content -LiteralPath (Join-Path $repository 'build/latest-windows-payload.txt') -Raw).Trim()
$environment=Join-Path $testRoot 'environment'
New-Item -ItemType Directory -Path $environment -Force | Out-Null
Set-Content -LiteralPath (Join-Path $environment 'original.txt') -Value 'preserve environment contents'
Set-Content -LiteralPath (Join-Path $testRoot 'workspace-marker.txt') -Value 'preserve workspace'
$declined=$false
try { Get-MultiGPUEnvironment $app $environment -ConfirmRepair { $false } | Out-Null } catch { $declined=$true }
if(!$declined -or !(Test-Path -LiteralPath (Join-Path $environment 'original.txt'))) { throw 'Declined repair changed the environment' }
$savedPython=$env:MULTIGPU_PYTHON
try {
    $env:MULTIGPU_PYTHON=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    $repaired=Get-MultiGPUEnvironment $app $environment -ConfirmRepair { $true }
    if(!(Test-Path -LiteralPath (Join-Path $repaired.RepairBackup 'original.txt')) -or !(Test-Path -LiteralPath $repaired.Python) -or !(Test-Path -LiteralPath (Join-Path $testRoot 'workspace-marker.txt'))) { throw 'Repair did not preserve data' }
    $broken=Join-Path $testRoot 'rollback-environment'
    New-Item -ItemType Directory -Path $broken -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $broken 'original.txt') -Value 'restore on failure'
    $env:MULTIGPU_PYTHON=Join-Path $testRoot 'missing-python.exe'
    $failed=$false
    $failureApp=Join-Path $testRoot 'failure app'
    New-Item -ItemType Directory -Path $failureApp -Force | Out-Null
    try { Get-MultiGPUEnvironment $failureApp $broken -ConfirmRepair { $true } | Out-Null } catch { $failed=$true }
    if(!$failed -or !(Test-Path -LiteralPath (Join-Path $broken 'original.txt'))) { throw 'Failed repair did not restore the old environment' }
} finally { $env:MULTIGPU_PYTHON=$savedPython }
$result=@{passed=$true;instance_isolation=$true;controller_identity=$true;notification_baseline_and_bound=$true;repair_decline_preserves_data=$true;repair_keeps_backup=$true;repair_failure_rolls_back=$true;test_root=$testRoot}
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $testRoot 'acceptance-result.json') -Encoding UTF8
$result | ConvertTo-Json
