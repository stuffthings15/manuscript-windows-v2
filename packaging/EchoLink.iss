#define MyAppName "Manuscript"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Manuscript"
#define MyAppExeName "Manuscript.exe"
#define MyAppUserModelID "EchoLink.Desktop"
#define MyAppMutex "EchoLink.App.71099095-BCB5-4E9C-A623-D0C474335542"
#define BrowserExtensionId "amjackdejmlebghkojdddodaapfeipam"

[Setup]
AppId={{71099095-BCB5-4E9C-A623-D0C474335542}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppMutex={#MyAppMutex}
DefaultDirName={localappdata}\Programs\Manuscript
DefaultGroupName=Manuscript
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\installer-output
OutputBaseFilename=ManuscriptSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\resources\manuscript.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\Manuscript\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\EchoLink.exe"
Type: files; Name: "{autoprograms}\Echo-Link.lnk"
Type: files; Name: "{autodesktop}\Echo-Link.lnk"

[Registry]
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.manuscript.polish"; ValueType: string; ValueName: ""; ValueData: "{app}\browser-extension\com.manuscript.polish.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.manuscript.polish"; ValueType: string; ValueName: ""; ValueData: "{app}\browser-extension\com.manuscript.polish.json"; Flags: uninsdeletekey

[Icons]
Name: "{autoprograms}\Manuscript"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelID}"
Name: "{autodesktop}\Manuscript"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "{#MyAppUserModelID}"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Manuscript"; Flags: nowait postinstall skipifsilent

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
  ManifestPath := ExpandConstant('{app}\browser-extension\com.manuscript.polish.json');
  HostPath := ExpandConstant('{app}\browser-host\ManuscriptBrowserHost.exe');
  Payload :=
    '{' + #13#10 +
    '  "name": "com.manuscript.polish",' + #13#10 +
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
  if (not UninstallSilent) and DirExists(ExpandConstant('{localappdata}\Echo-Link')) then
    MsgBox(
      'Manuscript will keep your local private data. To remove it, first use Settings > Privacy > Delete all private data.',
      mbInformation,
      MB_OK
    );
end;
