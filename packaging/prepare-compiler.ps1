$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$download=Join-Path $repository 'build/downloads/innosetup-6.7.3.exe'
$destination=Join-Path $repository 'build/tools/InnoSetup'
New-Item -ItemType Directory -Path (Split-Path $download),$destination -Force | Out-Null
if (!(Test-Path -LiteralPath $download)) {
    Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $download -UseBasicParsing
}
$signature=Get-AuthenticodeSignature -LiteralPath $download
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:CN|O)=Pyrsys B\.V\.') { throw 'Inno Setup signature is not valid for the official publisher Pyrsys B.V.' }
if ((Get-Item -LiteralPath $download).VersionInfo.ProductVersion -notlike '6.7.3*') { throw 'Unexpected Inno Setup installer version.' }
$process=Start-Process -FilePath $download -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/NOICONS',('/DIR="'+$destination+'"')) -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode) { throw "Compiler setup failed ($($process.ExitCode))." }
if (!(Test-Path -LiteralPath (Join-Path $destination 'ISCC.exe'))) { throw 'Compiler setup did not produce ISCC.exe.' }
Write-Output "Verified Inno Setup 6.7.3 compiler: $destination"

