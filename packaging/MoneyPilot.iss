; Inno Setup script for MoneyPilot — builds dist\MoneyPilot-Setup.exe.
; Installs to %LOCALAPPDATA%\Programs\MoneyPilot: no admin prompt AND a location
; OneDrive never syncs, which avoids the pythonnet/.NET "Failed to resolve
; Python.Runtime.Loader.Initialize" crash that hits zips unpacked into OneDrive.
; Build via scripts\build_installer.ps1 (passes /DMyAppVersion from version.py).

#define MyAppName "MoneyPilot"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExeName "MoneyPilot.exe"
#define MyAppPublisher "MoneyPilot"
#define MyAppURL "https://github.com/OrwaKb/MoneyPilot"

[Setup]
AppId={{A3F1C2E4-7B9D-4E5A-8C1F-2D6B0A9E4F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
CloseApplications=yes
OutputDir=..\dist
OutputBaseFilename=MoneyPilot-Setup
SetupIconFile=..\app\ui\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller one-folder bundle (app + UI + Claude runtime).
Source: "..\dist\MoneyPilot\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; Note: the user's ledger lives in %LOCALAPPDATA%\MoneyPilot (separate from {app}),
; so uninstalling removes only the program — never their financial data.
