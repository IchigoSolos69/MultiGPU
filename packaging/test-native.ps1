param([string]$Payload,[switch]$Source)
$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$testRoot=Join-Path $repository ('build/native acceptance '+[guid]::NewGuid().ToString('N'))
$state=Join-Path $testRoot 'isolated state'
$reports=Join-Path $testRoot 'native reports'
New-Item -ItemType Directory -Path $state,$reports -Force | Out-Null
if ($Source) { $app=$repository } else {
    if (!$Payload) { $Payload=(Get-Content -LiteralPath (Join-Path $repository 'build/latest-windows-payload.txt') -Raw).Trim() }
    $app=Join-Path $testRoot 'app'
    New-Item -ItemType Directory -Path $app -Force | Out-Null
    Get-ChildItem -LiteralPath $Payload -Force | Copy-Item -Destination $app -Recurse -Force
}
. (Join-Path $app 'packaging/launcher-common.ps1')
$arguments=@('-ConfigRoot',$state,'-NativeAcceptanceDirectory',$reports)
$executable=Join-Path $app 'MultiGPU.exe'
if ($Source) { $executable=Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'; $arguments=@('-NoProfile','-STA','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',(Join-Path $app 'start-desktop.ps1'))+$arguments }
$line=($arguments | ForEach-Object { ConvertTo-MultiGPUArgument $_ }) -join ' '
$process=Start-Process -FilePath $executable -ArgumentList $line -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $testRoot 'stdout.log') -RedirectStandardError (Join-Path $testRoot 'stderr.log')
$connection=$null
try {
    $report=Join-Path $reports 'native-acceptance-result.json'
    $deadline=[DateTime]::UtcNow.AddSeconds(120)
    do {
        Start-Sleep -Milliseconds 150
        if(!$hostProcess -and (Test-Path -LiteralPath (Join-Path $state 'desktop-state.json'))) {
            $startingDesktop=Get-Content -LiteralPath (Join-Path $state 'desktop-state.json') -Raw | ConvertFrom-Json
            $hostProcess=Get-Process -Id ([int]$startingDesktop.pid) -ErrorAction SilentlyContinue
        }
        if ($Source -and $process.HasExited) { throw ('The native app exited before acceptance: '+(Get-Content -LiteralPath (Join-Path $testRoot 'stderr.log') -Raw)) }
        $nativeState=Join-Path $state 'native-window.json'
        if (Test-Path -LiteralPath $nativeState) {
            try { $health=Get-Content -LiteralPath $nativeState -Raw | ConvertFrom-Json; $lastNativeError=$health.error } catch {}
        }
    } until ((Test-Path -LiteralPath $report) -or [DateTime]::UtcNow -gt $deadline)
    if (!(Test-Path -LiteralPath $report)) { throw ('The native app did not finish acceptance: '+$lastNativeError) }
    $result=Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    if (!$result.passed) { throw ('Native acceptance failed: '+$result.error) }
    $desktop=Get-Content -LiteralPath (Join-Path $state 'desktop-state.json') -Raw | ConvertFrom-Json
    $hostProcess=Get-Process -Id ([int]$desktop.pid)
    # The self-test hides its own window; a second launch must activate that host.
    $second=Start-Process -FilePath $executable -ArgumentList $line -WindowStyle Hidden -PassThru
    $exitReport=Join-Path $reports 'native-exit-result.json'
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    while (!(Test-Path -LiteralPath $exitReport) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (!(Test-Path -LiteralPath $exitReport)) { throw 'Second launch did not restore the existing native window.' }
    $exitResult=Get-Content -LiteralPath $exitReport -Raw | ConvertFrom-Json
    if(!$exitResult.passed) { throw ('Native dead-controller exit failed: '+$exitResult.error) }
    if(!$hostProcess.WaitForExit(10000)) { throw 'Native host could not exit with its controller unavailable.' }
    if (Test-Path -LiteralPath (Join-Path $state 'workspace/controller-connection.json')) { throw 'The native test controller did not stop cleanly.' }
    $result | Add-Member -NotePropertyName entry_point -NotePropertyValue $(if($Source){'start-desktop.ps1'}else{'MultiGPU.exe'}) -Force
    $result | Add-Member -NotePropertyName controller_shutdown -NotePropertyValue $true -Force
    $result | Add-Member -NotePropertyName second_launch_restores -NotePropertyValue $true -Force
    $result | Add-Member -NotePropertyName dead_controller_exit -NotePropertyValue $true -Force
    $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $report -Encoding UTF8
    Write-Output "Native acceptance passed: $report"
    Write-Output ($result | ConvertTo-Json -Depth 4)
} finally {
    $controllerFile=Join-Path $state 'workspace/controller-connection.json'
    if (Test-Path -LiteralPath $controllerFile) {
        try { $own=Get-Content -LiteralPath $controllerFile -Raw | ConvertFrom-Json; $b=Invoke-WebRequest -Uri ($own.url+'/api/bootstrap') -UseBasicParsing -TimeoutSec 3; Invoke-RestMethod -Uri ($own.url+'/api/shutdown') -Method Post -Headers @{'X-MultiGPU-Token'=[string]($b.Headers['X-MultiGPU-Token'] | Select-Object -First 1)} -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null } catch { Write-Warning 'The isolated native test controller still needs shutdown.' }
    }
    if (!$process.HasExited) { Stop-Process -Id $process.Id }
    if ($hostProcess -and !$hostProcess.HasExited) { Stop-Process -Id $hostProcess.Id -ErrorAction SilentlyContinue }
}
