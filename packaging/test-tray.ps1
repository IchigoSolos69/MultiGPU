param([string]$Payload)
$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!$Payload) { $Payload=(Get-Content -LiteralPath (Join-Path $repository 'build/latest-windows-payload.txt') -Raw).Trim() }
$existing=$null
if ([Threading.Mutex]::TryOpenExisting('MultiGPU.Desktop',[ref]$existing)) { $existing.Dispose(); throw 'Close the existing MultiGPU tray before running the isolated tray test.' }
$testRoot=Join-Path $repository ('build/tray acceptance '+[guid]::NewGuid().ToString('N'))
$app=Join-Path $testRoot 'app'
$config=Join-Path $testRoot 'isolated state'
New-Item -ItemType Directory -Path $app,$config -Force | Out-Null
Get-ChildItem -LiteralPath $Payload -Force | Copy-Item -Destination $app -Recurse -Force
# Exercise the exact packaged launcher, without replacing files with working sources.
. (Join-Path $app 'packaging/launcher-common.ps1')
$arguments=@('-NoProfile','-STA','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',(Join-Path $app 'start-desktop.ps1'),'-ConfigRoot',$config,'-NoBrowser')
$line=($arguments | ForEach-Object { ConvertTo-MultiGPUArgument $_ }) -join ' '
$powershell=Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$trayProcess=Start-Process -FilePath $powershell -ArgumentList $line -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $testRoot 'stdout.log') -RedirectStandardError (Join-Path $testRoot 'stderr.log')
$connection=$null
try {
    $ready=Join-Path $config 'desktop-state.json'
    $deadline=[DateTime]::UtcNow.AddSeconds(45)
    do {
        Start-Sleep -Milliseconds 100
        if ($trayProcess.HasExited) { throw ('Native tray exited before readiness: '+(Get-Content -LiteralPath (Join-Path $testRoot 'stderr.log') -Raw)) }
        if (Test-Path -LiteralPath $ready) { $connection=Get-Content -LiteralPath $ready -Raw | ConvertFrom-Json }
    } until ($connection -or [DateTime]::UtcNow -gt $deadline)
    if (!$connection) { throw 'The tray did not confirm that its icon and controller were ready.' }
    $bootstrap=Invoke-WebRequest -Uri ($connection.url+'/api/bootstrap') -UseBasicParsing -TimeoutSec 5
    $headers=@{'X-MultiGPU-Token'=$bootstrap.Headers['X-MultiGPU-Token']}
    Invoke-RestMethod -Uri ($connection.url+'/api/shutdown') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    $controllerConnection=Join-Path $config 'workspace/controller-connection.json'
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while ((Test-Path -LiteralPath $controllerConnection) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (Test-Path -LiteralPath $controllerConnection) { throw 'The isolated tray controller did not stop cleanly.' }
    $result=@{passed=$true;native_tray_ready=$true;controller_shutdown=$true;provider_jobs_launched=$false;state=$config}
    $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $testRoot 'tray-acceptance-result.json') -Encoding UTF8
    Write-Output ($result | ConvertTo-Json)
} finally {
    # Only terminate this test's own tray process after the API stopped its empty controller.
    if (!$trayProcess.HasExited) { Stop-Process -Id $trayProcess.Id }
}
