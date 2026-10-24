using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Windows.Forms;
[assembly: AssemblyTitle("MultiGPU")]
[assembly: AssemblyProduct("MultiGPU")]
[assembly: AssemblyDescription("MultiGPU native Windows application")]
[assembly: AssemblyVersion("0.4.4.0")]
[assembly: AssemblyFileVersion("0.4.4.0")]
class MultiGPULauncher {
    static string Quote(string value) { return "\""+Regex.Replace(Regex.Replace(value,@"(\\*)""", "$1$1\\\""),@"(\\+)$", "$1$1")+"\""; }
    [STAThread] static void Main(string[] args) {
        string root=AppDomain.CurrentDomain.BaseDirectory;
        string powershell=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),@"WindowsPowerShell\v1.0\powershell.exe");
        try {
            string arguments="-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "+Quote(Path.Combine(root,"start-desktop.ps1"));
            string config=null;
            for(int i=0;i<args.Length;i++) {
                if(args[i]=="-NoWindow" || args[i]=="-NoBrowser") arguments+=" -NoWindow";
                else if(args[i]=="-ConfigRoot" || args[i]=="-Workspace" || args[i]=="-NativeAcceptanceDirectory") {
                    string name=args[i]; if(++i>=args.Length) throw new ArgumentException("Missing application option value.");
                    string value=Path.GetFullPath(args[i]); arguments+=" "+name+" "+Quote(value); if(name=="-ConfigRoot") config=value;
                } else throw new ArgumentException("Unsupported application option.");
            }
            var process=Process.Start(new ProcessStartInfo(powershell,arguments) { UseShellExecute=false, CreateNoWindow=true, WorkingDirectory=root });
            if(config!=null) {Directory.CreateDirectory(config);File.WriteAllText(Path.Combine(config,"launcher-child.pid"),process.Id.ToString());}
        }
        catch(Exception) { MessageBox.Show("MultiGPU could not start its Windows host. Repair the installation and try again.","MultiGPU",MessageBoxButtons.OK,MessageBoxIcon.Error); }
    }
}
