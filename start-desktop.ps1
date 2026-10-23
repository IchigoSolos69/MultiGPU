param([string]$Workspace, [string]$ConfigRoot, [Alias('NoBrowser')][switch]$NoWindow, [string]$NativeAcceptanceDirectory)
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
$window=$null
$desktopStateFile=Join-Path $ConfigRoot 'desktop-state.json'
$script:exiting=$false
try {
    $nativeRoot=Join-Path $appRoot 'native'
    if (!(Test-Path -LiteralPath (Join-Path $nativeRoot 'MultiGPU.Native.dll'))) { $nativeRoot=Join-Path $appRoot 'build/native-host' }
    if (!(Test-Path -LiteralPath (Join-Path $nativeRoot 'MultiGPU.Native.dll'))) { throw 'The native window component is missing. Install the Windows release, or prepare the source host with packaging/prepare-native.ps1.' }
    [Reflection.Assembly]::LoadFrom((Join-Path $nativeRoot 'Microsoft.Web.WebView2.Core.dll')) | Out-Null
    [Reflection.Assembly]::LoadFrom((Join-Path $nativeRoot 'Microsoft.Web.WebView2.WinForms.dll')) | Out-Null
    [Reflection.Assembly]::LoadFrom((Join-Path $nativeRoot 'MultiGPU.Native.dll')) | Out-Null
    [MultiGPU.NativeWindow]::LoadNativeLoader((Join-Path $nativeRoot 'WebView2Loader.dll'))
    try { $webviewVersion=[MultiGPU.NativeWindow]::RuntimeVersion() } catch { $webviewVersion=$null }
    if (!$webviewVersion) {
        if ($NoWindow) { throw 'Microsoft Edge WebView2 Runtime is missing. Open MultiGPU normally to review and install its Microsoft prerequisite.' }
        $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
        $principal=New-Object Security.Principal.WindowsPrincipal($identity)
        if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Microsoft Edge WebView2 Runtime is required. Reopen MultiGPU without Run as administrator to review a normal-user installation, or install it directly from Microsoft: https://developer.microsoft.com/microsoft-edge/webview2/' }
        $consent=[Windows.Forms.MessageBox]::Show('MultiGPU needs Microsoft Edge WebView2 Runtime to display its Windows interface. Install Microsoft''s signed Evergreen Runtime now? It downloads from Microsoft and updates automatically. An existing shared Microsoft Edge Updater may manage it. No administrator elevation is requested.','Set up the MultiGPU window','YesNo','Information')
        if ($consent -ne 'Yes') { return }
        $bootstrap=Join-Path $nativeRoot 'MicrosoftEdgeWebview2Setup.exe'
        $signature=Get-AuthenticodeSignature -LiteralPath $bootstrap
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:CN|O)=Microsoft Corporation(?:,|$)') { throw 'The bundled WebView2 installer could not be verified as Microsoft signed. Repair the MultiGPU installation.' }
        $setup=Start-Process -FilePath $bootstrap -ArgumentList @('/silent','/install') -WindowStyle Hidden -Wait -PassThru
        try { $webviewVersion=[MultiGPU.NativeWindow]::RuntimeVersion() } catch { $webviewVersion=$null }
        if (!$webviewVersion) { throw 'Microsoft WebView2 setup did not finish. Check your network or organization policy, install the Runtime from Microsoft, and reopen MultiGPU.' }
    }
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
    $acceptance=$null
    if ($NativeAcceptanceDirectory) { $acceptance=[IO.Path]::GetFullPath($NativeAcceptanceDirectory) }
    $window=New-Object MultiGPU.NativeWindow($connection.url,(Join-Path $ConfigRoot 'webview-profile'),(Join-Path $nativeRoot 'MultiGPU.ico'),(Join-Path $ConfigRoot 'native-window.json'),(!$NoWindow),$acceptance)
    function Open-Dashboard { $window.RestoreWindow() }
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
            # Give cooperative pauses and dedicated-Studio cleanup time to finish
            # before disconnecting the controller that performs that cleanup.
            $pauseDeadline=[DateTime]::UtcNow.AddSeconds(30)
            do {
                $currentJobs=Invoke-RestMethod -Uri ($connection.url+'/api/jobs') -TimeoutSec 5
                $remaining=@($currentJobs | Where-Object { $_.provider_id -ne 'kaggle' -and ($_.status -in @('queued','running','pausing') -or ($_.provider_id -eq 'lightning' -and $_.handle -and !$_.handle.studio_stopped -and $_.cleanup_status -ne 'verified')) })
                if ($remaining.Count) { Start-Sleep -Milliseconds 250; [Windows.Forms.Application]::DoEvents() }
            } while ($remaining.Count -and [DateTime]::UtcNow -lt $pauseDeadline)
            if ($remaining.Count) {
                $continue=[Windows.Forms.MessageBox]::Show('Some runs have not confirmed a final checkpoint or stopped. Remote compute can keep running, and local work since the last checkpoint may be lost. Exit anyway and reconnect later?','Stopping is unconfirmed','YesNo','Warning')
                if ($continue -ne 'Yes') { $script:exiting=$false; return }
            }
            Invoke-RestMethod -Uri ($connection.url+'/api/shutdown') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 10 | Out-Null
            $deadline=[DateTime]::UtcNow.AddSeconds(20)
            do { Start-Sleep -Milliseconds 200; [Windows.Forms.Application]::DoEvents(); $alive=Read-Connection } while ($alive -and [DateTime]::UtcNow -lt $deadline)
            if ($alive) { throw 'The controller is still saving progress. Leave the tray running and try again after it finishes.' }
            $window.AllowExit=$true
            $window.Close()
            [Windows.Forms.Application]::ExitThread()
        } catch {
            [Windows.Forms.MessageBox]::Show($_.Exception.Message,'MultiGPU','OK','Warning') | Out-Null
            $script:exiting=$false
        }
    }
    $tray=New-Object Windows.Forms.NotifyIcon
    $tray.Icon=$window.Icon
    $tray.Text='MultiGPU - training controller'
    $menu=New-Object Windows.Forms.ContextMenuStrip
    $open=$menu.Items.Add('Open MultiGPU')
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
    @{pid=$PID;url=$connection.url;workspace_dir=$Workspace} | ConvertTo-Json | Set-Content -LiteralPath $desktopStateFile -Encoding UTF8
    $window.Show()
    [Windows.Forms.Application]::Run((New-Object Windows.Forms.ApplicationContext))
} catch {
    $startupError=$_.Exception.Message
    if ($controller -and !$controller.HasExited) {
        try {
            $failedConnection=Read-Connection
            if ($failedConnection) { Invoke-RestMethod -Uri ($failedConnection.url+'/api/shutdown') -Method Post -Headers @{'X-MultiGPU-Token'=$failedConnection.token} -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null }
        } catch {}
    }
    [Windows.Forms.MessageBox]::Show($startupError,'MultiGPU could not start','OK','Error') | Out-Null
} finally {
    if ($tray) { $tray.Visible=$false; $tray.Dispose() }
    if ($window) { $window.AllowExit=$true; $window.Dispose() }
    if (Test-Path -LiteralPath $desktopStateFile) {
        try { if ((Get-Content -LiteralPath $desktopStateFile -Raw | ConvertFrom-Json).pid -eq $PID) { Remove-Item -LiteralPath $desktopStateFile } } catch {}
    }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
