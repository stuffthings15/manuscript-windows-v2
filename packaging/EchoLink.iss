#define MyAppName "Manuscript Windows Version 2"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Manuscript"
#define MyAppExeName "ManuscriptV2.exe"
#define MyAppUserModelID "Manuscript.WindowsV2"
#define MyAppMutex "Manuscript.WindowsV2.App.65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E"
#define BrowserExtensionId "dhekcbbhlaacmmdilbkpbakelachaojl"

[Setup]
AppId={{65BD5BC9-6E5E-4E2D-9BB1-B49EA46CB73E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppMutex={#MyAppMutex}
DefaultDirName={autopf}\Manuscript Windows Version 2
DefaultGroupName=Manuscript Windows Version 2
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\installer-output
OutputBaseFilename=ManuscriptV2Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\resources\manuscript.ico
LicenseFile=..\resources\EULA.txt
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\ManuscriptV2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKLM; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.manuscript.v2.polish"; ValueType: string; ValueName: ""; ValueData: "{app}\browser-extension\com.manuscript.v2.polish.json"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.manuscript.v2.polish"; ValueType: string; ValueName: ""; ValueData: "{app}\browser-extension\com.manuscript.v2.polish.json"; Flags: uninsdeletekey

[Icons]
Name: "{autoprograms}\Manuscript Windows Version 2"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelID}"
Name: "{autodesktop}\Manuscript Windows Version 2"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "{#MyAppUserModelID}"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Manuscript Windows Version 2"; Flags: nowait postinstall skipifsilent

[Code]
function JsonPath(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ManifestPath: String;
  HostPath: String;
  Payload: String;
begin
  if CurStep <> ssPostInstall then
    exit;
  ManifestPath := ExpandConstant('{app}\browser-extension\com.manuscript.v2.polish.json');
  HostPath := ExpandConstant('{app}\browser-host\ManuscriptV2BrowserHost.exe');
  Payload :=
    '{' + #13#10 +
    '  "name": "com.manuscript.v2.polish",' + #13#10 +
    '  "description": "Manuscript local writing correction host",' + #13#10 +
    '  "path": "' + JsonPath(HostPath) + '",' + #13#10 +
    '  "type": "stdio",' + #13#10 +
    '  "allowed_origins": ["chrome-extension://{#BrowserExtensionId}/"]' + #13#10 +
    '}' + #13#10;
  if not SaveStringToFile(ManifestPath, Payload, False) then
    RaiseException('Could not write the Manuscript browser host manifest.');
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if (not UninstallSilent) and DirExists(ExpandConstant('{localappdata}\ManuscriptWindowsV2')) then
    MsgBox(
      'Manuscript will keep your local private data. To remove it, first use Settings > Privacy > Delete all private data.',
      mbInformation,
      MB_OK
    );
end;
