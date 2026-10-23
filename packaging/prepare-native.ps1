param([string]$Destination)
$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!$Destination) { $Destination=Join-Path $repository 'build/native-host' }
$Destination=[IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $Destination,(Join-Path $repository 'build/downloads') -Force | Out-Null
$sdkVersion='1.0.4258.31'
$package=Join-Path $repository "build/downloads/microsoft.web.webview2.$sdkVersion.nupkg"
if (!(Test-Path -LiteralPath $package)) { Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$sdkVersion/microsoft.web.webview2.$sdkVersion.nupkg" -OutFile $package -UseBasicParsing }
$sdkHash='56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9'
if ((Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sdkHash) { throw 'Pinned Microsoft WebView2 SDK checksum mismatch.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$extract=Join-Path $repository ('build/webview2-sdk-'+[guid]::NewGuid().ToString('N'))
[IO.Compression.ZipFile]::ExtractToDirectory($package,$extract)
foreach ($name in @('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll')) { Copy-Item -LiteralPath (Join-Path $extract "lib/net462/$name") -Destination $Destination -Force }
Copy-Item -LiteralPath (Join-Path $extract 'runtimes/win-x64/native/WebView2Loader.dll') -Destination $Destination -Force
Copy-Item -LiteralPath (Join-Path $extract 'LICENSE.txt') -Destination (Join-Path $Destination 'Microsoft-WebView2-SDK-LICENSE.txt') -Force

# Draw the existing four-block product mark; no new icon package or font is needed.
Add-Type -AssemblyName System.Drawing
$bitmap=New-Object Drawing.Bitmap(64,64)
$graphics=[Drawing.Graphics]::FromImage($bitmap)
$blue=New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(46,98,217))
$graphics.Clear([Drawing.Color]::Transparent)
foreach ($rect in @(@(6,8,22,22),@(36,8,22,22),@(6,38,22,18),@(36,38,22,18))) { $graphics.FillRectangle($blue,$rect[0],$rect[1],$rect[2],$rect[3]) }
$graphics.FillRectangle([Drawing.Brushes]::White,14,16,6,6)
$graphics.FillRectangle([Drawing.Brushes]::White,44,16,6,6)
$icon=[Drawing.Icon]::FromHandle($bitmap.GetHicon())
$stream=[IO.File]::Create((Join-Path $Destination 'MultiGPU.ico'))
try { $icon.Save($stream) } finally { $stream.Dispose();$icon.Dispose();$blue.Dispose();$graphics.Dispose();$bitmap.Dispose() }
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (!(Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework compiler is unavailable. Install .NET Framework 4.8 development support to build the native host.' }
$references=@('/r:System.dll','/r:System.Core.dll','/r:System.Drawing.dll','/r:System.Windows.Forms.dll','/r:System.Web.Extensions.dll',('/r:'+(Join-Path $Destination 'Microsoft.Web.WebView2.Core.dll')),('/r:'+(Join-Path $Destination 'Microsoft.Web.WebView2.WinForms.dll')))
& $compiler /nologo /target:library /platform:x64 ('/out:'+(Join-Path $Destination 'MultiGPU.Native.dll')) @references (Join-Path $PSScriptRoot 'NativeWindow.cs')
if ($LASTEXITCODE) { throw 'The native WebView2 host did not compile.' }
& $compiler /nologo /target:winexe /platform:x64 '/r:System.dll' '/r:System.Windows.Forms.dll' ('/win32icon:'+(Join-Path $Destination 'MultiGPU.ico')) ('/out:'+(Join-Path $Destination 'MultiGPU.exe')) (Join-Path $PSScriptRoot 'AppLauncher.cs')
if ($LASTEXITCODE) { throw 'The Windows application launcher did not compile.' }
$bootstrap=Join-Path $repository 'build/downloads/MicrosoftEdgeWebview2Setup.exe'
if (!(Test-Path -LiteralPath $bootstrap)) { Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $bootstrap -UseBasicParsing }
$signature=Get-AuthenticodeSignature -LiteralPath $bootstrap
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:CN|O)=Microsoft Corporation(?:,|$)') { throw 'The WebView2 prerequisite installer is not signed by Microsoft Corporation.' }
Copy-Item -LiteralPath $bootstrap -Destination $Destination -Force
@{sdk_version=$sdkVersion;sdk_package_sha256=$sdkHash;evergreen_bootstrapper_sha256=(Get-FileHash -LiteralPath $bootstrap -Algorithm SHA256).Hash.ToLowerInvariant()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'native-build-info.json') -Encoding UTF8
Write-Output "Prepared native Windows host: $Destination"
