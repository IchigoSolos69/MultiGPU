param([string]$Payload,[switch]$Source)
$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$existing=$null
if ([Threading.Mutex]::TryOpenExisting('MultiGPU.Desktop',[ref]$existing)) { $existing.Dispose(); throw 'Close the existing MultiGPU desktop app before the isolated native test.' }
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
$arguments=@('-NoProfile','-STA','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',(Join-Path $app 'start-desktop.ps1'),'-ConfigRoot',$state,'-NativeAcceptanceDirectory',$reports)
$line=($arguments | ForEach-Object { ConvertTo-MultiGPUArgument $_ }) -join ' '
$powershell=Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$process=Start-Process -FilePath $powershell -ArgumentList $line -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $testRoot 'stdout.log') -RedirectStandardError (Join-Path $testRoot 'stderr.log')
$connection=$null
try {
    $report=Join-Path $reports 'native-acceptance-result.json'
    $deadline=[DateTime]::UtcNow.AddSeconds(75)
    do {
        Start-Sleep -Milliseconds 150
        if ($process.HasExited) { throw ('The native app exited before acceptance: '+(Get-Content -LiteralPath (Join-Path $testRoot 'stderr.log') -Raw)) }
        $nativeState=Join-Path $state 'native-window.json'
        if (Test-Path -LiteralPath $nativeState) {
            try { $health=Get-Content -LiteralPath $nativeState -Raw | ConvertFrom-Json; if($health.error){throw $health.error} } catch { if($_.Exception.Message -notmatch 'JSON|Unexpected end'){ throw } }
        }
    } until ((Test-Path -LiteralPath $report) -or [DateTime]::UtcNow -gt $deadline)
    if (!(Test-Path -LiteralPath $report)) { throw 'The native app did not finish acceptance; inspect isolated state/native-window.json.' }
    $result=Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    if (!$result.passed) { throw ('Native acceptance failed: '+$result.error) }
    $connection=Get-Content -LiteralPath (Join-Path $state 'workspace/controller-connection.json') -Raw | ConvertFrom-Json
    $bootstrap=Invoke-WebRequest -Uri ($connection.url+'/api/bootstrap') -UseBasicParsing -TimeoutSec 5
    $headers=@{'X-MultiGPU-Token'=$bootstrap.Headers['X-MultiGPU-Token']}
    Invoke-RestMethod -Uri ($connection.url+'/api/shutdown') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while ((Test-Path -LiteralPath (Join-Path $state 'workspace/controller-connection.json')) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (Test-Path -LiteralPath (Join-Path $state 'workspace/controller-connection.json')) { throw 'The native test controller did not stop cleanly.' }
    Write-Output "Native acceptance passed: $report"
    Write-Output ($result | ConvertTo-Json -Depth 4)
} finally {
    $controllerFile=Join-Path $state 'workspace/controller-connection.json'
    if (Test-Path -LiteralPath $controllerFile) {
        try { $own=Get-Content -LiteralPath $controllerFile -Raw | ConvertFrom-Json; $b=Invoke-WebRequest -Uri ($own.url+'/api/bootstrap') -UseBasicParsing -TimeoutSec 3; Invoke-RestMethod -Uri ($own.url+'/api/shutdown') -Method Post -Headers @{'X-MultiGPU-Token'=$b.Headers['X-MultiGPU-Token']} -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null } catch { Write-Warning 'The isolated native test controller still needs shutdown.' }
    }
    if (!$process.HasExited) { Stop-Process -Id $process.Id }
}
