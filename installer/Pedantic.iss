; Inno Setup definition for Pedantic.
;
; Compile with:
;   & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\Pedantic.iss
;
; The installer is deliberately per-user (PrivilegesRequired=lowest):
;   * No administrator prompt, so it works on locked-down corporate machines.
;   * Pedantic must run at the same integrity level as the applications it
;     injects keystrokes into, and those are normal user applications. An
;     elevated install that led to an elevated Pedantic would break copy and
;     paste in every non-elevated window.

#define MyAppName "Pedantic"
#define MyAppVersion "0.1.2"
#define MyAppPublisher "Pedantic"
#define MyAppExeName "Pedantic.exe"
#define MyAppSourceExeName "Pedantic-0.1.2.exe"

[Setup]
AppId={{7C2F9A54-6E1B-4C39-9E4D-2A7B5D1F0C83}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
; The build is unsigned, so this is display metadata only.
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} text transformation utility
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startupicon"; Description: "Start {#MyAppName} when I sign in to Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\dist\{#MyAppSourceExeName}"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
Source: "INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} instructions"; Filename: "{app}\INSTALL.txt"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The per-user data directory holds the configuration, log, history, and usage
; journals. It is intentionally left in place so that reinstalling or
; upgrading keeps the user's profiles and hotkeys.
Type: files; Name: "{app}\INSTALL.txt"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { Nothing to migrate yet; the hook exists for future upgrade steps. }
end;
