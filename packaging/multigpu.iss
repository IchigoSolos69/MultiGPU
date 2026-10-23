#if (Ver >> 8) != (EncodeVer(6, 7, 3) >> 8)
  #error Inno Setup 6.7.3 is required for this pinned build.
#endif
#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif
#ifndef PayloadDir
  #error PayloadDir must point to an allowlisted prepared payload.
#endif
#ifndef OutputRoot
  #define OutputRoot "..\dist"
#endif
[Setup]
AppId={{98EDDB21-1903-4CC7-A45B-23161D11D59B}
AppName=MultiGPU
AppVersion={#AppVersion}
AppPublisher=MultiGPU
DefaultDirName={localappdata}\Programs\MultiGPU
DefaultGroupName=MultiGPU
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputRoot}
OutputBaseFilename=MultiGPU-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=MultiGPU
CloseApplications=no
RestartApplications=no
AppMutex=MultiGPU.Desktop
SetupIconFile={#PayloadDir}\native\MultiGPU.ico
UninstallDisplayIcon={app}\MultiGPU.exe

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MultiGPU"; Filename: "{app}\MultiGPU.exe"; WorkingDir: "{app}"; AppUserModelID: "MultiGPU.Desktop"
Name: "{userdesktop}\MultiGPU"; Filename: "{app}\MultiGPU.exe"; WorkingDir: "{app}"; AppUserModelID: "MultiGPU.Desktop"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\MultiGPU.exe"; Description: "Open MultiGPU"; Flags: nowait postinstall skipifsilent

; Workspaces, exports, native provider credentials and the managed environment
; live outside {app}. They are intentionally retained on upgrade and uninstall.

