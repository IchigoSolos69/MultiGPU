param([string]$Workspace, [string]$ConfigRoot, [switch]$NoBrowser)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$appRoot=$PSScriptRoot
if (!$ConfigRoot) { $ConfigRoot=Join-Path $env:LOCALAPPDATA 'MultiGPU' }
New-Item -ItemType Directory -Path $ConfigRoot -Force | Out-Null
$settingsFile=Join-Path $ConfigRoot 'desktop.json'
if (!$Workspace -and (Test-Path -LiteralPath $settingsFile)) {
    try { $Workspace=(Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json).workspace } catch {}
}
if (!$Workspace) { $Workspace=Join-Path $ConfigRoot 'workspace' }
$Workspace=[IO.Path]::GetFullPath($Workspace)
New-Item -ItemType Directory -Path $Workspace -Force | Out-Null
. (Join-Path $appRoot 'packaging/launcher-common.ps1')
$newMutex=$false
$mutex=New-Object Threading.Mutex($true,'MultiGPU.Desktop',[ref]$newMutex)
if (!$newMutex) {
    [Windows.Forms.MessageBox]::Show('MultiGPU is already running. Open its tray icon to return to the dashboard.','MultiGPU') | Out-Null
    $mutex.Dispose()
    return
}
$tray=$null
$controller=$null
$connection=$null
$script:exiting=$false
try {
    $runtime=Get-MultiGPUEnvironment -AppRoot $appRoot -EnvironmentRoot (Join-Path $ConfigRoot 'environment')
    $connectionFile=Join-Path $Workspace 'controller-connection.json'
    function Read-Connection {
        if (!(Test-Path -LiteralPath $connectionFile)) { return $null }
        try {
            $candidate=Get-Content -LiteralPath $connectionFile -Raw | ConvertFrom-Json
            if ($candidate.url -notmatch '^http://127\.0\.0\.1:[0-9]+$' -or [IO.Path]::GetFullPath($candidate.workspace_dir) -ne $Workspace) { return $null }
            $response=Invoke-WebRequest -Uri ($candidate.url+'/api/bootstrap') -UseBasicParsing -TimeoutSec 2
            $bootstrap=$response.Content | ConvertFrom-Json
            if ([IO.Path]::GetFullPath($bootstrap.app.workspace_dir) -ne $Workspace) { return $null }
            $candidate | Add-Member -NotePropertyName token -NotePropertyValue $response.Headers['X-MultiGPU-Token'] -Force
            return $candidate
        } catch { return $null }
    }
    $connection=Read-Connection
    if (!$connection) {
        Remove-Item -LiteralPath $connectionFile -ErrorAction SilentlyContinue
        $arguments=@('-m','multigpu','--port','0','--no-browser','--data-dir',$Workspace,'--connection-file',$connectionFile)
        $commandLine=($arguments | ForEach-Object { ConvertTo-MultiGPUArgument $_ }) -join ' '
        $controller=Start-Process -FilePath $runtime.Python -ArgumentList $commandLine -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Workspace 'controller.stdout.log') -RedirectStandardError (Join-Path $Workspace 'controller.stderr.log')
        $deadline=[DateTime]::UtcNow.AddSeconds(40)
        do {
            if ($controller.HasExited) { throw "Controller startup failed. Inspect $Workspace\controller.stderr.log" }
            Start-Sleep -Milliseconds 200
            $connection=Read-Connection
        } until ($connection -or [DateTime]::UtcNow -gt $deadline)
        if (!$connection) { throw "Controller startup timed out. Inspect $Workspace\controller.stderr.log" }
    }
    function Open-Dashboard { Start-Process -FilePath $connection.url | Out-Null }
    function Stop-Controller {
        if ($script:exiting) { return }
        $answer=[Windows.Forms.MessageBox]::Show('Pause training and exit MultiGPU? Remote jobs may continue until the provider confirms stopping. Kaggle requires its own Stop button. Native billing limits still apply. You can reconnect when you reopen MultiGPU.','Pause training and exit','YesNo','Warning')
        if ($answer -ne 'Yes') { return }
        $script:exiting=$true
        try {
            $headers=@{'X-MultiGPU-Token'=$connection.token}
            $jobs=Invoke-RestMethod -Uri ($connection.url+'/api/jobs') -TimeoutSec 5
            foreach ($job in $jobs) {
                if ($job.status -in @('queued','running','pausing') -and $job.provider_id -ne 'kaggle') {
                    try { Invoke-RestMethod -Uri ($connection.url+'/api/jobs/'+$job.id+'/pause') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 10 | Out-Null } catch {}
                }
            }
            Invoke-RestMethod -Uri ($connection.url+'/api/shutdown') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 10 | Out-Null
            $deadline=[DateTime]::UtcNow.AddSeconds(20)
            do { Start-Sleep -Milliseconds 200; [Windows.Forms.Application]::DoEvents(); $alive=Read-Connection } while ($alive -and [DateTime]::UtcNow -lt $deadline)
            if ($alive) { throw 'The controller is still saving progress. Leave the tray running and try again after it finishes.' }
            [Windows.Forms.Application]::ExitThread()
        } catch {
            [Windows.Forms.MessageBox]::Show($_.Exception.Message,'MultiGPU','OK','Warning') | Out-Null
            $script:exiting=$false
        }
    }
    $tray=New-Object Windows.Forms.NotifyIcon
    $tray.Icon=[Drawing.SystemIcons]::Application
    $tray.Text='MultiGPU - training controller'
    $menu=New-Object Windows.Forms.ContextMenuStrip
    $open=$menu.Items.Add('Open dashboard')
    $open.add_Click({ Open-Dashboard })
    $choose=$menu.Items.Add('Choose workspace for next start...')
    $choose.add_Click({
        $dialog=New-Object Windows.Forms.FolderBrowserDialog
        $dialog.Description='Choose the MultiGPU workspace for the next start. Existing runs stay in their current workspace.'
        if ($dialog.ShowDialog() -eq 'OK') {
            @{workspace=$dialog.SelectedPath} | ConvertTo-Json | Set-Content -LiteralPath $settingsFile -Encoding UTF8
            [Windows.Forms.MessageBox]::Show('Workspace saved. Exit MultiGPU from the tray, then reopen it to use this workspace.','MultiGPU') | Out-Null
        }
        $dialog.Dispose()
    })
    $folder=$menu.Items.Add('Open current workspace')
    $folder.add_Click({ Start-Process -FilePath explorer.exe -ArgumentList (ConvertTo-MultiGPUArgument $Workspace) | Out-Null })
    $menu.Items.Add('-') | Out-Null
    $quit=$menu.Items.Add('Pause training and exit')
    $quit.add_Click({ Stop-Controller })
    $tray.ContextMenuStrip=$menu
    $tray.add_DoubleClick({ Open-Dashboard })
    $tray.Visible=$true
    if (!$NoBrowser) { Open-Dashboard }
    [Windows.Forms.Application]::Run((New-Object Windows.Forms.ApplicationContext))
} catch {
    [Windows.Forms.MessageBox]::Show($_.Exception.Message,'MultiGPU could not start','OK','Error') | Out-Null
} finally {
    if ($tray) { $tray.Visible=$false; $tray.Dispose() }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
