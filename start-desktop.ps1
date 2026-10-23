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
if($NativeAcceptanceDirectory -and ([IO.Path]::GetFullPath($ConfigRoot).TrimEnd('\') -eq [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'MultiGPU')).TrimEnd('\') -or !$Workspace.StartsWith([IO.Path]::GetFullPath($ConfigRoot).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase))) { throw 'Native acceptance requires an isolated ConfigRoot and a workspace inside it.' }
New-Item -ItemType Directory -Path $Workspace -Force | Out-Null
. (Join-Path $appRoot 'packaging/launcher-common.ps1')
$instance=Get-MultiGPUInstanceIdentity $ConfigRoot
$newMutex=$false
$mutex=New-Object Threading.Mutex($true,$instance.MutexName,[ref]$newMutex)
if (!$newMutex) {
    try {
        $existingActivation=[Threading.EventWaitHandle]::OpenExisting($instance.ActivationName)
        try { $existingActivation.Set() | Out-Null } finally { $existingActivation.Dispose() }
    } catch { [Windows.Forms.MessageBox]::Show('MultiGPU is already running. Open its tray icon to return to the dashboard.','MultiGPU') | Out-Null }
    $mutex.Dispose()
    return
}
$tray=$null
$controller=$null
$connection=$null
$window=$null
$activation=$null
$activationTimer=$null
$notificationTimer=$null
$script:polling=$false
$script:operationBusy=$false
$script:controllerUnavailable=$false
$script:notificationBaseline=$null
$desktopStateFile=Join-Path $ConfigRoot 'desktop-state.json'
$script:exiting=$false
try {
    $eventSecurity=New-Object Security.AccessControl.EventWaitHandleSecurity
    $eventSecurity.AddAccessRule((New-Object Security.AccessControl.EventWaitHandleAccessRule([Security.Principal.WindowsIdentity]::GetCurrent().User,[Security.AccessControl.EventWaitHandleRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow)))
    $createdEvent=$false
    $activation=New-Object Threading.EventWaitHandle($false,[Threading.EventResetMode]::AutoReset,$instance.ActivationName,[ref]$createdEvent,$eventSecurity)
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
    $runtime=Get-MultiGPUEnvironment -AppRoot $appRoot -EnvironmentRoot (Join-Path $ConfigRoot 'environment') -ConfirmRepair {
        param($path)
        return [Windows.Forms.MessageBox]::Show("The app's Python environment is missing or incompatible. Repair it now? The old environment will be kept as a backup. Optional training libraries will need preparation again. Workspace, model exports and provider credentials will be kept.`n`n$path",'Repair MultiGPU environment','YesNo','Warning') -eq 'Yes'
    }
    $connectionFile=Join-Path $Workspace 'controller-connection.json'
    function Read-Connection {
        if (!(Test-Path -LiteralPath $connectionFile)) { return $null }
        try {
            $candidate=Get-Content -LiteralPath $connectionFile -Raw | ConvertFrom-Json
            if ($candidate.url -notmatch '^http://127\.0\.0\.1:[0-9]+$' -or [IO.Path]::GetFullPath($candidate.workspace_dir) -ne $Workspace) { return $null }
            $response=Invoke-WebRequest -Uri ($candidate.url+'/api/bootstrap') -UseBasicParsing -TimeoutSec 2
            $bootstrap=$response.Content | ConvertFrom-Json
            if ([IO.Path]::GetFullPath($bootstrap.app.workspace_dir) -ne $Workspace) { return $null }
            $candidate | Add-Member -NotePropertyName token -NotePropertyValue ([string]($response.Headers['X-MultiGPU-Token'] | Select-Object -First 1)) -Force
            $candidate | Add-Member -NotePropertyName compatibility_reason -NotePropertyValue (Test-MultiGPUControllerIdentity $bootstrap $Workspace $runtime) -Force
            return $candidate
        } catch { return $null }
    }
    function Confirm-Unconfirmed {
        return [Windows.Forms.MessageBox]::Show('Some runs have not confirmed a checkpoint or stopped. Kaggle needs its own Stop control. Remote compute may keep running, and local work since the last checkpoint may be lost. Disconnect anyway?','Stopping is unconfirmed','YesNo','Warning') -eq 'Yes'
    }
    function Connect-Controller {
        $current=Read-Connection
        if($current -and $current.compatibility_reason) {
            $answer=[Windows.Forms.MessageBox]::Show("A controller already owns this workspace, but $($current.compatibility_reason). Request pauses and restart it using this installation? Remote work may continue until its provider confirms stopping.",'Update the workspace controller','YesNo','Warning')
            if($answer -ne 'Yes' -or !(Stop-MultiGPUController $current ${function:Confirm-Unconfirmed})) { throw 'The existing controller was left running. Reconnect after it has been paused and closed.' }
            $deadline=[DateTime]::UtcNow.AddSeconds(20)
            do { Start-Sleep -Milliseconds 200; [Windows.Forms.Application]::DoEvents(); $current=Read-Connection } while($current -and [DateTime]::UtcNow -lt $deadline)
            if($current) { throw 'The previous controller is still saving progress. Try reconnecting after it finishes.' }
        }
        if($current) { $script:connection=$current; return }
        $arguments=@('-m','multigpu','--port','0','--no-browser','--data-dir',$Workspace,'--connection-file',$connectionFile)
        $commandLine=($arguments | ForEach-Object { ConvertTo-MultiGPUArgument $_ }) -join ' '
        $script:controller=Start-Process -FilePath $runtime.Python -ArgumentList $commandLine -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Workspace 'controller.stdout.log') -RedirectStandardError (Join-Path $Workspace 'controller.stderr.log')
        $deadline=[DateTime]::UtcNow.AddSeconds(40)
        do {
            if ($controller.HasExited) {
                $recovered=Read-Connection
                if($recovered -and !$recovered.compatibility_reason) { $script:connection=$recovered; return }
                throw "Controller startup failed. Inspect $Workspace\controller.stderr.log"
            }
            Start-Sleep -Milliseconds 200
            $current=Read-Connection
        } until (($current -and $current.pid -eq $controller.Id) -or [DateTime]::UtcNow -gt $deadline)
        if (!$current -or $current.compatibility_reason) { throw "Controller startup timed out or returned an incompatible identity. Inspect $Workspace\controller.stderr.log" }
        $script:connection=$current
    }
    Connect-Controller
    $acceptance=$null
    if ($NativeAcceptanceDirectory) { $acceptance=[IO.Path]::GetFullPath($NativeAcceptanceDirectory) }
    $window=New-Object MultiGPU.NativeWindow($connection.url,(Join-Path $ConfigRoot 'webview-profile'),(Join-Path $nativeRoot 'MultiGPU.ico'),(Join-Path $ConfigRoot 'native-window.json'),(!$NoWindow),$acceptance)
    function Open-Dashboard { $window.RestoreWindow() }
    function Close-Desktop {
        $script:exiting=$true
        $window.AllowExit=$true
        $window.Close()
        [Windows.Forms.Application]::ExitThread()
    }
    function Confirm-DisconnectedExit {
        if($acceptance -or [Windows.Forms.MessageBox]::Show('The local controller cannot be reached. Exit this window anyway? Saved runs are kept. This does not confirm that local workers or remote compute stopped; reconnect later and check the provider account.','Controller unavailable','YesNo','Warning') -eq 'Yes') { Close-Desktop }
    }
    function Reconnect-Controller {
        if($script:exiting -or $script:operationBusy) { return }
        $script:operationBusy=$true
        try {
            Connect-Controller
            $script:controllerUnavailable=$false
            $script:notificationBaseline=$null
            $window.Connect($connection.url)
            $tray.Text='MultiGPU - training controller'
            Write-DesktopState
        } catch { $window.ShowRecovery($_.Exception.Message+' Reconnect again, inspect workspace diagnostics, or exit this window. Remote compute may still be running.') }
        finally { $script:operationBusy=$false }
    }
    function Stop-Controller {
        if ($script:exiting -or $script:operationBusy) { return }
        if(!(Read-Connection)) { Confirm-DisconnectedExit; return }
        $answer=[Windows.Forms.MessageBox]::Show('Pause training and exit MultiGPU? Remote jobs may continue until the provider confirms stopping. Kaggle requires its own Stop button. Native billing limits still apply. You can reconnect when you reopen MultiGPU.','Pause training and exit','YesNo','Warning')
        if ($answer -ne 'Yes') { return }
        $script:exiting=$true
        try {
            if(!(Stop-MultiGPUController $connection ${function:Confirm-Unconfirmed})) { $script:exiting=$false; return }
            $deadline=[DateTime]::UtcNow.AddSeconds(20)
            do { Start-Sleep -Milliseconds 200; [Windows.Forms.Application]::DoEvents(); $alive=Read-Connection } while ($alive -and [DateTime]::UtcNow -lt $deadline)
            if ($alive) { throw 'The controller is still saving progress. Leave the tray running and try again after it finishes.' }
            Close-Desktop
        } catch {
            $script:exiting=$false
            if(!(Read-Connection)) { Confirm-DisconnectedExit }
            else { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'MultiGPU','OK','Warning') | Out-Null }
        }
    }
    $tray=New-Object Windows.Forms.NotifyIcon
    $tray.Icon=$window.Icon
    $tray.Text='MultiGPU - training controller'
    $menu=New-Object Windows.Forms.ContextMenuStrip
    $open=$menu.Items.Add('Open MultiGPU')
    $open.add_Click({ Open-Dashboard })
    $reconnect=$menu.Items.Add('Reconnect controller')
    $reconnect.add_Click({ Reconnect-Controller })
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
    $tray.add_BalloonTipClicked({ Open-Dashboard })
    $tray.Visible=$true
    $window.add_ReconnectRequested({ Reconnect-Controller })
    $window.add_ExitRequested({ Stop-Controller })
    $window.add_DiagnosticsRequested({ Start-Process -FilePath explorer.exe -ArgumentList (ConvertTo-MultiGPUArgument $Workspace) | Out-Null })
    function Write-DesktopState {
        @{pid=$PID;url=$connection.url;workspace_dir=$Workspace;controller_unavailable=$script:controllerUnavailable;activation_name=$instance.ActivationName} | ConvertTo-Json | Set-Content -LiteralPath $desktopStateFile -Encoding UTF8
    }
    $activationTimer=New-Object Windows.Forms.Timer
    $activationTimer.Interval=250
    $activationTimer.add_Tick({ if($activation.WaitOne(0)) { Open-Dashboard } })
    $activationTimer.Start()
    $notificationTimer=New-Object Windows.Forms.Timer
    $notificationTimer.Interval=5000
    $notificationTimer.add_Tick({
        if($script:polling -or $script:exiting -or $script:operationBusy) { return }
        $script:polling=$true
        try {
            $snapshot=Invoke-RestMethod -Uri ($connection.url+'/api/bootstrap') -TimeoutSec 2
            if(Test-MultiGPUControllerIdentity $snapshot $Workspace $runtime) { throw 'Controller identity changed. Reconnect to review it.' }
            $next=@{}
            foreach($job in $snapshot.jobs) {
                $next[$job.id]=$job.status
            }
            foreach($job in @(Get-MultiGPUNotificationChanges $snapshot.jobs $script:notificationBaseline ([bool]$snapshot.settings.desktop_notifications))) {
                $text=[string]$job.name
                if($text.Length -gt 180) { $text=$text.Substring(0,180) }
                $tray.ShowBalloonTip(7000,('MultiGPU - '+$job.status),$text,[Windows.Forms.ToolTipIcon]::Info)
            }
            $script:notificationBaseline=$next
            if($script:controllerUnavailable) { $script:controllerUnavailable=$false; $tray.Text='MultiGPU - training controller'; Write-DesktopState }
        } catch {
            if(!$script:controllerUnavailable) {
                $script:controllerUnavailable=$true
                $tray.Text='MultiGPU - controller unavailable'
                $window.ShowRecovery('The local controller is unavailable or incompatible. Reconnect to restore saved runs, inspect workspace diagnostics, or exit this window. Remote compute may still be running.')
                Write-DesktopState
            }
        } finally { $script:polling=$false }
    })
    $notificationTimer.Start()
    Write-DesktopState
    $window.Show()
    [Windows.Forms.Application]::Run((New-Object Windows.Forms.ApplicationContext))
} catch {
    $startupError=$_.Exception.Message
    $startupError | Set-Content -LiteralPath (Join-Path $ConfigRoot 'desktop-error.log') -Encoding UTF8
    if ($controller -and !$controller.HasExited) {
        try {
            $failedConnection=Read-Connection
            if ($failedConnection) { Invoke-RestMethod -Uri ($failedConnection.url+'/api/shutdown') -Method Post -Headers @{'X-MultiGPU-Token'=$failedConnection.token} -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null }
        } catch {}
    }
    if(!$NativeAcceptanceDirectory) { [Windows.Forms.MessageBox]::Show($startupError,'MultiGPU could not start','OK','Error') | Out-Null }
} finally {
    if ($notificationTimer) { $notificationTimer.Stop(); $notificationTimer.Dispose() }
    if ($activationTimer) { $activationTimer.Stop(); $activationTimer.Dispose() }
    if ($activation) { $activation.Dispose() }
    if ($tray) { $tray.Visible=$false; $tray.Dispose() }
    if ($window) { $window.AllowExit=$true; $window.Dispose() }
    if (Test-Path -LiteralPath $desktopStateFile) {
        try { if ((Get-Content -LiteralPath $desktopStateFile -Raw | ConvertFrom-Json).pid -eq $PID) { Remove-Item -LiteralPath $desktopStateFile } } catch {}
    }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
