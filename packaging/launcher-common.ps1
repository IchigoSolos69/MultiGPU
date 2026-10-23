function Get-MultiGPUInstanceIdentity {
    param([string]$ConfigRoot)
    $path=[IO.Path]::GetFullPath($ConfigRoot).TrimEnd('\').ToUpperInvariant()
    $hash=[Security.Cryptography.SHA256]::Create()
    try { $key=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($path)))).Replace('-','').Substring(0,20) } finally { $hash.Dispose() }
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $default=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'MultiGPU')).TrimEnd('\').ToUpperInvariant()
    return @{MutexName=$(if($path -eq $default){'MultiGPU.Desktop'}else{'MultiGPU.Desktop.'+$sid+'.'+$key});ActivationName=('Local\MultiGPU.Activate.'+$sid+'.'+$key)}
}

function Test-MultiGPUControllerIdentity {
    param($Bootstrap,[string]$Workspace,$Runtime)
    $reasons=@()
    if (!$Bootstrap.app -or [IO.Path]::GetFullPath($Bootstrap.app.workspace_dir) -ne [IO.Path]::GetFullPath($Workspace)) { $reasons+='workspace differs' }
    if ($Bootstrap.app.version -ne $Runtime.AppVersion) { $reasons+='application version differs' }
    if (!$Bootstrap.hardware.python -or [IO.Path]::GetFullPath($Bootstrap.hardware.python) -ne [IO.Path]::GetFullPath($Runtime.Python)) { $reasons+='Python environment differs' }
    if (!$Bootstrap.app.runtime_base_prefix -or [IO.Path]::GetFullPath($Bootstrap.app.runtime_base_prefix) -ne [IO.Path]::GetFullPath($Runtime.BasePrefix)) { $reasons+='base Python runtime differs or is unknown' }
    return ($reasons -join '; ')
}

function Stop-MultiGPUController {
    param($Connection,[scriptblock]$ConfirmUnconfirmed)
    $headers=@{'X-MultiGPU-Token'=[string]$Connection.token}
    $jobs=Invoke-RestMethod -Uri ($Connection.url+'/api/jobs') -TimeoutSec 3
    foreach($job in $jobs) {
        if($job.status -in @('queued','running','pausing') -and $job.provider_id -ne 'kaggle') {
            try { Invoke-RestMethod -Uri ($Connection.url+'/api/jobs/'+$job.id+'/pause') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 6 | Out-Null } catch {}
        }
    }
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    do {
        $jobs=Invoke-RestMethod -Uri ($Connection.url+'/api/jobs') -TimeoutSec 3
        $remaining=@($jobs | Where-Object { $_.status -in @('queued','running','pausing') -or ($_.provider_id -eq 'lightning' -and $_.handle -and !$_.handle.studio_stopped -and $_.cleanup_status -ne 'verified') })
        if($remaining.Count) { Start-Sleep -Milliseconds 250; [Windows.Forms.Application]::DoEvents() }
    } while($remaining.Count -and [DateTime]::UtcNow -lt $deadline)
    if($remaining.Count -and (!$ConfirmUnconfirmed -or !(& $ConfirmUnconfirmed))) { return $false }
    Invoke-RestMethod -Uri ($Connection.url+'/api/shutdown') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 8 | Out-Null
    return $true
}

function Get-MultiGPUNotificationChanges {
    param($Jobs,$Previous,[bool]$Enabled)
    if(!$Enabled -or $null -eq $Previous) { return }
    return @($Jobs | Where-Object { $_.status -in @('completed','paused','error','interrupted','awaiting_user') -and $Previous[$_.id] -ne $_.status } | Select-Object -First 3)
}

function Get-MultiGPUEnvironment {
    param([string]$AppRoot, [string]$EnvironmentRoot, [scriptblock]$ConfirmRepair)
    $env:PYTHONPATH=$AppRoot
    $env:PYTHONHOME=''
    $env:PYTHONNOUSERSITE='1'
    $env:PYTHONDONTWRITEBYTECODE='1'
    $env:PYTHONUNBUFFERED='1'
    $uv=Join-Path $AppRoot 'uv.exe'
    $managed=Join-Path $AppRoot 'runtime'
    $bundled=$null
    if (Test-Path -LiteralPath $managed) {
        $bundled=Get-ChildItem -LiteralPath $managed -Filter python.exe -Recurse -File | Where-Object { $_.Directory.Name -ne 'Scripts' } | Select-Object -First 1
    }
    $sourceEnvironment=Join-Path $AppRoot '.venv'
    if (!$bundled -and (Test-Path -LiteralPath (Join-Path $sourceEnvironment 'Scripts/python.exe'))) { $EnvironmentRoot=$sourceEnvironment }
    $EnvironmentRoot=[IO.Path]::GetFullPath($EnvironmentRoot)
    $python=Join-Path $EnvironmentRoot 'Scripts/python.exe'
    $identity=$null
    if (Test-Path -LiteralPath $python) {
        try {
            $raw=& $python -c 'import json,sys; assert sys.prefix != sys.base_prefix and sys.version_info >= (3,11); print(json.dumps(dict(base_prefix=sys.base_prefix)))' 2>$null
            if (!$LASTEXITCODE) { $identity=$raw | ConvertFrom-Json }
        } catch { $identity=$null }
        if ($identity -and $bundled -and [IO.Path]::GetFullPath($identity.base_prefix) -ne [IO.Path]::GetFullPath($bundled.Directory.FullName)) { $identity=$null }
    }
    $backup=$null
    if (!$identity -and (Test-Path -LiteralPath $EnvironmentRoot)) {
        if (!$ConfirmRepair -or !(& $ConfirmRepair $EnvironmentRoot)) { throw 'The app Python environment is missing or incompatible. Reopen the Windows app and choose Repair environment. Workspace, exports and provider credentials will be kept.' }
        $backup=$EnvironmentRoot+'.repair-'+[DateTime]::UtcNow.ToString('yyyyMMddHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6)
        Move-Item -LiteralPath $EnvironmentRoot -Destination $backup
    }
    if (!$identity) {
      try {
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
      } catch {
        if ($backup) {
            if (Test-Path -LiteralPath $EnvironmentRoot) { Move-Item -LiteralPath $EnvironmentRoot -Destination ($EnvironmentRoot+'.failed-'+[guid]::NewGuid().ToString('N')) }
            Move-Item -LiteralPath $backup -Destination $EnvironmentRoot
        }
        throw
      }
    }
    $check=& $python -c 'import json,sys,multigpu; assert sys.prefix != sys.base_prefix and sys.version_info >= (3,11); print(json.dumps(dict(base_prefix=sys.base_prefix,app_version=multigpu.__version__)))'
    if ($LASTEXITCODE) { throw 'The application requires its own virtual environment with Python 3.11 or newer.' }
    $identity=$check | ConvertFrom-Json
    if (Test-Path -LiteralPath $uv) { $env:MULTIGPU_UV=$uv }
    return @{ Python=$python; Environment=$EnvironmentRoot; Uv=$uv; BasePrefix=$identity.base_prefix; AppVersion=$identity.app_version; RepairBackup=$backup }
}

function ConvertTo-MultiGPUArgument {
    param([string]$Value)
    # Standard Windows argv quoting, including a trailing backslash before a quote.
    return '"' + (($Value -replace '(\\*)"','$1$1\"') -replace '(\\+)$','$1$1') + '"'
}

