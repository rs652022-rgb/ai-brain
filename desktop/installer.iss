; Windows installer for the AI Brain desktop app (Inno Setup, free). Built by .github/workflows/desktop.yml.
#define AppVersion GetEnv("APP_VERSION")
#if AppVersion == ""
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B4C9D2E-7A41-4E8B-9C1F-2D5A8E3B7F10}
AppName=AI Brain
AppVersion={#AppVersion}
AppPublisher=AI Brain (open source)
DefaultDirName={localappdata}\Programs\AI Brain
DefaultGroupName=AI Brain
PrivilegesRequired=lowest
OutputDir=..\out
OutputBaseFilename=AIBrain-Windows-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\AIBrain.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Files]
Source: "..\dist\AIBrain\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AI Brain"; Filename: "{app}\AIBrain.exe"
Name: "{userdesktop}\AI Brain"; Filename: "{app}\AIBrain.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\AIBrain.exe"; Description: "Start AI Brain now"; Flags: nowait postinstall skipifsilent
