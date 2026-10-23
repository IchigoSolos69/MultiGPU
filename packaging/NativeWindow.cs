using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace MultiGPU {
    public sealed class NativeWindow : Form {
        [DllImport("kernel32", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern IntPtr LoadLibrary(string path);
        [DllImport("shell32", CharSet=CharSet.Unicode)]
        private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
        private Uri origin;
        private readonly string statePath, acceptanceDirectory, userDataFolder;
        private readonly bool startVisible;
        private WebView2 view;
        private Panel recovery;
        private Label recoveryMessage;
        private bool initializing, shown;
        private int viewGeneration;
        private readonly HashSet<ulong> cancelledNavigations=new HashSet<ulong>();
        private Timer acceptanceExitTimer;
        public event EventHandler ReconnectRequested;
        public event EventHandler ExitRequested;
        public event EventHandler DiagnosticsRequested;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private bool acceptanceStarted, loaded;
        private string error, externalUri, downloadPath, downloadState;
        private string permission, diagnostic;
        private int blocked, external;
        public bool AllowExit { get; set; }

        public static void LoadNativeLoader(string path) {
            if (LoadLibrary(Path.GetFullPath(path)) == IntPtr.Zero)
                throw new InvalidOperationException("The bundled WebView2 x64 loader could not load.");
        }
        public static string RuntimeVersion() { return CoreWebView2Environment.GetAvailableBrowserVersionString(null); }
        public static bool LocalUri(string value, string address) {
            Uri candidate, expected;
            return Uri.TryCreate(value, UriKind.Absolute, out candidate) && Uri.TryCreate(address, UriKind.Absolute, out expected)
                && candidate.Scheme == "http" && candidate.Host == "127.0.0.1" && candidate.Port == expected.Port
                && candidate.UserInfo.Length == 0 && expected.Scheme == "http" && expected.Host == "127.0.0.1";
        }
        public static bool ExternalUri(string value) {
            Uri candidate;
            return Uri.TryCreate(value, UriKind.Absolute, out candidate) && candidate.Scheme == "https"
                && !candidate.IsLoopback && candidate.UserInfo.Length == 0;
        }
        public NativeWindow(string address, string userDataFolder, string iconPath, string stateFile, bool visible, string acceptanceRoot) {
            if (!LocalUri(address, address)) throw new ArgumentException("The desktop window requires the controller's exact loopback address.");
            SetCurrentProcessExplicitAppUserModelID("MultiGPU.Desktop");
            origin = new Uri(address); statePath = stateFile; startVisible = visible; this.userDataFolder=userDataFolder; acceptanceDirectory = String.IsNullOrWhiteSpace(acceptanceRoot)?null:Path.GetFullPath(acceptanceRoot);
            Text = "MultiGPU"; MinimumSize = new Size(900, 640); Size = new Size(1360, 900);
            StartPosition = FormStartPosition.CenterScreen; BackColor = Color.FromArgb(238,241,245);
            if (File.Exists(iconPath)) Icon = new Icon(iconPath);
            recovery=new Panel { Dock=DockStyle.Fill, BackColor=BackColor, Visible=false, Padding=new Padding(40) };
            var content=new FlowLayoutPanel { Dock=DockStyle.Fill, FlowDirection=FlowDirection.TopDown, WrapContents=false, AutoScroll=true };
            recoveryMessage=new Label { AutoSize=true, MaximumSize=new Size(760,0), Font=new Font("Segoe UI",12), Margin=new Padding(0,0,0,24) };
            content.Controls.Add(recoveryMessage);
            var reload=new Button { Text="Reload interface", AutoSize=true }; reload.Click+=async delegate { await InitializeView(); }; content.Controls.Add(reload);
            var reconnect=new Button { Text="Reconnect controller", AutoSize=true }; reconnect.Click+=delegate { if(ReconnectRequested!=null) ReconnectRequested(this,EventArgs.Empty); }; content.Controls.Add(reconnect);
            var diagnostics=new Button { Text="Open workspace diagnostics", AutoSize=true }; diagnostics.Click+=delegate { if(DiagnosticsRequested!=null) DiagnosticsRequested(this,EventArgs.Empty); }; content.Controls.Add(diagnostics);
            var exit=new Button { Text="Exit MultiGPU", AutoSize=true }; exit.Click+=delegate { if(ExitRequested!=null) ExitRequested(this,EventArgs.Empty); }; content.Controls.Add(exit);
            recovery.Controls.Add(content); Controls.Add(recovery);
        }
        protected override async void OnShown(EventArgs e) {
            base.OnShown(e);
            if(shown) return; shown=true;
            if (!startVisible) { Hide(); ShowInTaskbar = false; }
            await InitializeView();
        }
        private async Task InitializeView() {
            if(initializing || IsDisposed) return;
            initializing=true; loaded=false;
            int generation=++viewGeneration;
            try {
                if(view!=null) { Controls.Remove(view); view.Dispose(); }
                view=new WebView2 { Dock=DockStyle.Fill, CreationProperties=new CoreWebView2CreationProperties { UserDataFolder=userDataFolder } };
                Controls.Add(view); recovery.BringToFront();
                await view.EnsureCoreWebView2Async(null);
                var core = view.CoreWebView2;
                core.Settings.AreHostObjectsAllowed = false;
                core.Settings.IsWebMessageEnabled = false;
                core.Settings.AreDevToolsEnabled = false;
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.AreBrowserAcceleratorKeysEnabled = false;
                core.Settings.IsStatusBarEnabled = false;
                core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs args) {
                    if (!LocalUri(args.Uri, origin.AbsoluteUri)) { args.Cancel = true; cancelledNavigations.Add(args.NavigationId); blocked++; if (args.IsUserInitiated) OpenExternal(args.Uri); SaveState(); }
                };
                core.FrameNavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs args) {
                    if (!LocalUri(args.Uri, origin.AbsoluteUri)) { args.Cancel = true; blocked++; SaveState(); }
                };
                core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs args) {
                    args.Handled = true;
                    if (LocalUri(args.Uri, origin.AbsoluteUri)) core.Navigate(args.Uri);
                    else if (args.IsUserInitiated) OpenExternal(args.Uri);
                    else { blocked++; SaveState(); }
                };
                core.PermissionRequested += delegate(object sender, CoreWebView2PermissionRequestedEventArgs args) {
                    permission=args.PermissionKind.ToString();
                    // Every accepted download still receives its own native Save dialog.
                    args.State=LocalUri(args.Uri,origin.AbsoluteUri) && permission=="MultipleAutomaticDownloads" ? CoreWebView2PermissionState.Allow : CoreWebView2PermissionState.Deny;
                    SaveState();
                };
                if(acceptanceDirectory!=null) {
                    await core.CallDevToolsProtocolMethodAsync("Log.enable","{}");
                    core.GetDevToolsProtocolEventReceiver("Log.entryAdded").DevToolsProtocolEventReceived += delegate(object sender,CoreWebView2DevToolsProtocolEventReceivedEventArgs args) {diagnostic=args.ParameterObjectAsJson;SaveState();};
                }
                core.LaunchingExternalUriScheme += delegate(object sender, CoreWebView2LaunchingExternalUriSchemeEventArgs args) { args.Cancel = true; blocked++; SaveState(); };
                core.DownloadStarting += Download;
                core.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs args) {
                    if(generation!=viewGeneration) return;
                    if(cancelledNavigations.Remove(args.NavigationId)) return;
                    if (args.IsSuccess && LocalUri(core.Source, origin.AbsoluteUri)) {
                        loaded = true; error=null; recovery.Visible=false; SaveState();
                        if (acceptanceDirectory != null && !acceptanceStarted) { acceptanceStarted = true; RunAcceptance(); }
                    } else if(!args.IsSuccess && args.WebErrorStatus.ToString()!="OperationCanceled") ShowRecovery("The local interface could not load. Reconnect to the controller, or reload the interface. Saved jobs are kept; remote compute may still be running.");
                };
                core.ProcessFailed += delegate(object sender, CoreWebView2ProcessFailedEventArgs args) {
                    string kind=args.ProcessFailedKind.ToString();
                    if(kind=="BrowserProcessExited" || kind=="RenderProcessExited" || kind=="RenderProcessUnresponsive")
                        BeginInvoke(new Action(delegate { if(generation==viewGeneration && !IsDisposed) ShowRecovery("The embedded browser stopped responding. Reload the interface to reconnect. The controller and saved jobs are independent of this window."); }));
                };
                core.Navigate(origin.GetLeftPart(UriPartial.Authority)+"/?desktop=1");
            } catch (Exception ex) {
                ShowRecovery("The native interface could not initialize ("+ex.GetType().Name+"). Retry Reload interface, or repair Microsoft Edge WebView2 Runtime. Saved jobs are kept.");
            } finally { initializing=false; }
        }
        public void ShowRecovery(string message) {
            loaded=false; error=message; recoveryMessage.Text=message; recovery.Visible=true; recovery.BringToFront(); SaveState();
        }
        public async void Connect(string address) {
            if(!LocalUri(address,address)) throw new ArgumentException("The controller requires a local address.");
            origin=new Uri(address); await InitializeView();
        }
        protected override void OnFormClosing(FormClosingEventArgs e) {
            if (!AllowExit) { e.Cancel=true; Hide(); ShowInTaskbar=false; SaveState(); }
            base.OnFormClosing(e);
        }
        public void RestoreWindow() { ShowInTaskbar=true; Show(); WindowState=FormWindowState.Normal; Activate(); BringToFront(); SaveState(); }
        private void OpenExternal(string uri) {
            if (!ExternalUri(uri)) { blocked++; return; }
            externalUri=uri; external++;
            // Acceptance records the routing decision without opening a user's browser.
            if (acceptanceDirectory == null) Process.Start(new ProcessStartInfo(uri) { UseShellExecute=true });
            SaveState();
        }
        private void Download(object sender, CoreWebView2DownloadStartingEventArgs e) {
            string uri=e.DownloadOperation.Uri;
            bool sameOriginBlob=uri.StartsWith("blob:"+origin.GetLeftPart(UriPartial.Authority)+"/", StringComparison.OrdinalIgnoreCase);
            if (!LocalUri(view.CoreWebView2.Source, origin.AbsoluteUri) || (!LocalUri(uri, origin.AbsoluteUri) && !sameOriginBlob)) { e.Cancel=true; return; }
            e.Handled=true;
            var deferral=e.GetDeferral();
            BeginInvoke(new Action(delegate {
                try {
                    string name=Path.GetFileName(e.ResultFilePath);
                    if (String.IsNullOrEmpty(name)) name="multigpu-download";
                    string target;
                    if (acceptanceDirectory != null) { Directory.CreateDirectory(acceptanceDirectory); target=Path.Combine(acceptanceDirectory, name); if(File.Exists(target)) target=Path.Combine(acceptanceDirectory, Guid.NewGuid().ToString("N")+"-"+name); }
                    else {
                        using(var dialog=new SaveFileDialog { FileName=name, Title="Save MultiGPU file", OverwritePrompt=true }) {
                            if(dialog.ShowDialog(this)!=DialogResult.OK) {e.Cancel=true; return;}
                            target=dialog.FileName;
                        }
                    }
                    e.ResultFilePath=Path.GetFullPath(target); downloadPath=e.ResultFilePath;
                    var operation=e.DownloadOperation;
                    operation.StateChanged += delegate { downloadState=operation.State.ToString(); SaveState(); };
                    downloadState=operation.State.ToString(); SaveState();
                } finally {deferral.Complete();}
            }));
        }
        private void SaveState() {
            try {
                var value=new Dictionary<string,object> { {"native_window",true},{"ready",loaded},{"visible",Visible},{"title",Text},
                    {"source",view==null || view.CoreWebView2 == null ? null : view.CoreWebView2.Source},{"runtime_version",RuntimeVersion()},{"recovery_visible",recovery.Visible},
                    {"blocked_navigations",blocked},{"external_routes",external},{"last_external_uri",externalUri},
                    {"download_path",downloadPath},{"download_state",downloadState},{"permission",permission},{"diagnostic",diagnostic},{"error",error} };
                File.WriteAllText(statePath+".tmp",json.Serialize(value));
                if(File.Exists(statePath)) File.Delete(statePath);
                File.Move(statePath+".tmp",statePath);
            } catch { }
        }
        private async Task DownloadAcceptance(string script, string expectedName) {
            downloadPath=null; downloadState=null;
            await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate",json.Serialize(new Dictionary<string,object>{{"expression",script},{"userGesture",true}}));
            DateTime deadline=DateTime.UtcNow.AddSeconds(10);
            while(downloadState!="Completed" && DateTime.UtcNow<deadline) await Task.Delay(100);
            if(downloadState!="Completed" || downloadPath==null || !File.Exists(downloadPath) || !Path.GetFileName(downloadPath).EndsWith(expectedName,StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Native download did not complete: "+expectedName+" ("+downloadState+")");
        }
        private async Task WaitForReady() {
            DateTime deadline=DateTime.UtcNow.AddSeconds(25);
            while(!loaded && DateTime.UtcNow<deadline) await Task.Delay(100);
            if(!loaded) throw new InvalidOperationException("Native recovery did not restore the interface: "+error);
        }
        private async Task StopAcceptanceController() {
            string code="(async()=>{const b=await fetch('/api/bootstrap');const t=b.headers.get('X-MultiGPU-Token');const r=await fetch('/api/shutdown',{method:'POST',headers:{'Content-Type':'application/json','X-MultiGPU-Token':t},body:'{}'});if(!r.ok)throw Error('Shutdown rejected');return true})()";
            await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate",json.Serialize(new Dictionary<string,object>{{"expression",code},{"awaitPromise",true},{"returnByValue",true}}));
            DateTime deadline=DateTime.UtcNow.AddSeconds(15);
            while(!recovery.Visible && DateTime.UtcNow<deadline) await Task.Delay(100);
            if(!recovery.Visible) throw new InvalidOperationException("Dead-controller recovery panel was not shown.");
        }
        private async void RunAcceptance() {
            var result=new Dictionary<string,object>();
            try {
                RestoreWindow(); view.Focus(); await Task.Delay(600);
                if(await view.CoreWebView2.ExecuteScriptAsync("new URLSearchParams(location.search).get('desktop') === '1'")!="true") throw new InvalidOperationException("Native desktop marker was absent.");
                result["native_desktop_marker"]=true;
                string dom=await view.CoreWebView2.ExecuteScriptAsync("document.readyState === 'complete' && document.querySelector('main') !== null");
                if(dom!="true") throw new InvalidOperationException("The application DOM did not render inside WebView2.");
                await DownloadAcceptance("(()=>{const a=document.createElement('a');a.href='/static/styles.css';a.download='native-local-download.css';a.click()})()", "native-local-download.css");
                result["http_file_download"]=true;
                await DownloadAcceptance("(()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['MultiGPU native blob download'],{type:'text/plain'}));a.download='native-blob-download.txt';a.click()})()", "native-blob-download.txt");
                if(File.ReadAllText(downloadPath)!="MultiGPU native blob download") throw new InvalidOperationException("Blob download content differs.");
                result["blob_download"]=true;
                IDataObject previous=Clipboard.GetDataObject();
                try {
                    string copied=await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate", "{\"expression\":\"navigator.clipboard.writeText('MultiGPU clipboard acceptance')\",\"userGesture\":true,\"awaitPromise\":true}");
                    if(Clipboard.GetText()!="MultiGPU clipboard acceptance") throw new InvalidOperationException("Clipboard write failed.");
                    result["clipboard_write"]=true;
                } finally { if(Clipboard.ContainsText() && Clipboard.GetText()=="MultiGPU clipboard acceptance") {if(previous!=null) Clipboard.SetDataObject(previous,true); else Clipboard.Clear();} }
                string specification=json.Serialize(new Dictionary<string,object> {{"name","Native notebook acceptance"},{"recipe","starter"},{"recipe_version",2},{"providers",new string[]{"colab"}},{"guided",true},{"steps",3},{"free_only",true},{"output_dir",Path.Combine(acceptanceDirectory,"exports")}});
                string expression="(async()=>{const b=await fetch('/api/bootstrap');const t=b.headers.get('X-MultiGPU-Token');const call=async(p,x)=>{const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-MultiGPU-Token':t},body:JSON.stringify(x)});const v=await r.json();if(!r.ok)throw Error(v.error||'Request failed');return v};const s="+specification+";const p=await call('/api/jobs/preview',s);s.preview_digest=p.preview_digest;const j=await call('/api/jobs',s);return j.id})()";
                string created=await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate",json.Serialize(new Dictionary<string,object>{{"expression",expression},{"awaitPromise",true},{"returnByValue",true}}));
                var createdValue=(Dictionary<string,object>)json.DeserializeObject(created);
                var evaluated=(Dictionary<string,object>)createdValue["result"];
                string jobId=evaluated.ContainsKey("value")?evaluated["value"] as string:null;
                if(String.IsNullOrEmpty(jobId)) throw new InvalidOperationException("The native notebook test could not prepare its assisted job.");
                await DownloadAcceptance("(()=>{const a=document.createElement('a');a.href='/api/jobs/"+jobId+"/notebook';a.download='training.ipynb';a.click()})()", ".ipynb");
                var notebook=(Dictionary<string,object>)json.DeserializeObject(File.ReadAllText(downloadPath));
                if(!notebook.ContainsKey("cells") || Convert.ToInt32(notebook["nbformat"])!=4) throw new InvalidOperationException("The downloaded notebook is invalid.");
                result["training_notebook_download"]=true;
                int before=blocked;
                view.CoreWebView2.Navigate("https://example.com/native-navigation-must-be-blocked"); await Task.Delay(700);
                view.CoreWebView2.Navigate("file:///C:/Windows/win.ini"); await Task.Delay(700);
                if(blocked<before+2 || !LocalUri(view.CoreWebView2.Source,origin.AbsoluteUri)) throw new InvalidOperationException("Off-origin navigation was not blocked.");
                result["navigation_guard"]=true;
                if(LocalUri("http://127.0.0.1:"+(origin.Port==65535?origin.Port-1:origin.Port+1)+"/",origin.AbsoluteUri)
                    || LocalUri("javascript:alert(1)",origin.AbsoluteUri) || LocalUri("file:///C:/Windows/win.ini",origin.AbsoluteUri)
                    || ExternalUri("file:///C:/Windows/win.ini") || ExternalUri("multigpu-untrusted://command")
                    || !ExternalUri("https://modal.com/")) throw new InvalidOperationException("Origin policy edge cases failed.");
                result["origin_policy_edges"]=true;
                int prior=external;
                await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate", "{\"expression\":\"window.open('https://modal.com/','_blank')\",\"userGesture\":true}"); await Task.Delay(300);
                if(external!=prior+1 || externalUri!="https://modal.com/") throw new InvalidOperationException("Provider link did not route to the external browser handler.");
                result["external_browser_routing"]=true;
                await view.CoreWebView2.ExecuteScriptAsync("location.hash='#providers'"); await Task.Delay(350);
                await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-action=\"provider\"][data-id=\"colab\"]')?.click()"); await Task.Delay(600);
                Close(); if(Visible) throw new InvalidOperationException("Closing the native window did not hide it to the tray.");
                RestoreWindow(); if(!Visible) throw new InvalidOperationException("The native window could not be restored.");
                result["hide_restore"]=true;
                using(var browser=Process.GetProcessById((int)view.CoreWebView2.BrowserProcessId)) { browser.Kill(); browser.WaitForExit(5000); }
                DateTime recoveryDeadline=DateTime.UtcNow.AddSeconds(10);
                while(!recovery.Visible && DateTime.UtcNow<recoveryDeadline) await Task.Delay(100);
                if(!recovery.Visible) throw new InvalidOperationException("Browser failure had no native recovery panel.");
                result["browser_failure_panel"]=true;
                await InitializeView(); await WaitForReady();
                result["browser_recreated"]=true;
                await StopAcceptanceController(); result["dead_controller_panel"]=true;
                if(ReconnectRequested==null) throw new InvalidOperationException("Native reconnect control is not wired.");
                ReconnectRequested(this,EventArgs.Empty); await WaitForReady();
                string saved=await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate",json.Serialize(new Dictionary<string,object>{{"expression","fetch('/api/jobs/"+jobId+"').then(r=>r.ok)"},{"awaitPromise",true},{"returnByValue",true}}));
                var savedValue=(Dictionary<string,object>)json.DeserializeObject(saved);
                var savedResult=(Dictionary<string,object>)savedValue["result"];
                if(!savedResult.ContainsKey("value") || !(bool)savedResult["value"]) throw new InvalidOperationException("Saved run was lost after controller restart.");
                result["controller_restart"]=true;
                await view.CoreWebView2.ExecuteScriptAsync("location.hash='#providers'"); await Task.Delay(300);
                await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-action=\"provider\"][data-id=\"colab\"]')?.click()"); await Task.Delay(400);
                result["passed"]=true;
            } catch(Exception ex) { result["passed"]=false; result["error"]=ex.GetType().Name+": "+ex.Message; }
            try { using(var preview=File.Create(Path.Combine(acceptanceDirectory,"native-setup-window.png"))) await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,preview); } catch { }
            result["runtime_version"]=RuntimeVersion(); result["source"]=view.CoreWebView2.Source;
            Directory.CreateDirectory(acceptanceDirectory); File.WriteAllText(Path.Combine(acceptanceDirectory,"native-acceptance-result.json"),json.Serialize(result));
            if(result.ContainsKey("passed") && (bool)result["passed"]) {
                Close();
                acceptanceExitTimer=new Timer { Interval=250 };
                acceptanceExitTimer.Tick+=async delegate {
                    if(!Visible) return;
                    acceptanceExitTimer.Stop();
                    var exitResult=new Dictionary<string,object>();
                    try {
                        await StopAcceptanceController();
                        if(ExitRequested==null) throw new InvalidOperationException("Native exit control is not wired.");
                        exitResult["passed"]=true;
                        File.WriteAllText(Path.Combine(acceptanceDirectory,"native-exit-result.json"),json.Serialize(exitResult));
                        ExitRequested(this,EventArgs.Empty);
                    } catch(Exception ex) {
                        exitResult["passed"]=false; exitResult["error"]=ex.Message;
                        File.WriteAllText(Path.Combine(acceptanceDirectory,"native-exit-result.json"),json.Serialize(exitResult));
                    }
                };
                acceptanceExitTimer.Start();
            }
            SaveState();
        }
        protected override void Dispose(bool disposing) {
            if(disposing && acceptanceExitTimer!=null) acceptanceExitTimer.Dispose();
            base.Dispose(disposing);
        }
    }
}
