using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
[assembly: AssemblyTitle("MultiGPU")]
[assembly: AssemblyProduct("MultiGPU")]
[assembly: AssemblyDescription("MultiGPU native Windows application")]
[assembly: AssemblyVersion("0.3.0.0")]
[assembly: AssemblyFileVersion("0.3.0.0")]
class MultiGPULauncher {
    [STAThread] static void Main() {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        string powershell=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),@"WindowsPowerShell\v1.0\powershell.exe");
        try { Process.Start(new ProcessStartInfo(powershell, "-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File \""+Path.Combine(root,"start-desktop.ps1")+"\"") { UseShellExecute=false, CreateNoWindow=true, WorkingDirectory=root }); }
        catch(Exception) { MessageBox.Show("MultiGPU could not start its Windows host. Repair the installation and try again.","MultiGPU",MessageBoxButtons.OK,MessageBoxIcon.Error); }
    }
}
