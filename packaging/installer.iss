[Setup]
#ifdef SmokeTest
AppId=ChintamaniGems-InstallerSmoke
Uninstallable=no
CreateUninstallRegKey=no
#else
AppId=Chintamani Gems
#endif
AppName=Chintamani Gems
AppVersion=0.8.0
AppPublisher=Chintamani Gems
AppPublisherURL=https://chintamani-updates.vercel.app/
AppUpdatesURL=https://chintamani-updates.vercel.app/
DefaultDirName={localappdata}\Programs\ChintamaniGems
PrivilegesRequired=lowest
OutputDir=..\release
#ifdef SmokeTest
OutputBaseFilename=ChintamaniGemsSetup-Smoke
#else
OutputBaseFilename=ChintamaniGemsSetup-0.8.0
#endif
SetupIconFile=..\drc_sync\assets\chintamani-gems.ico
UninstallDisplayIcon={app}\ChintamaniGems.ico
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#ifdef SmokeTest
Compression=none
SolidCompression=no
#else
Compression=lzma2
SolidCompression=yes
#endif
#ifdef SmokeTest
AppMutex=Local\ChintamaniGemsInstallerSmoke
#else
AppMutex=Local\ChintamaniGemsInventory
#endif
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
UsePreviousTasks=yes
UsePreviousAppDir=no
[Files]
Source: "..\dist-next\ChintamaniGems\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Tasks]
Name: startup; Description: "Start Chintamani Gems when I sign in"; Flags: checkedonce
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: checkedonce
[InstallDelete]
Type: files; Name: "{app}\DRCInventorySync.exe"
#ifndef SmokeTest
[Icons]
Name: "{userprograms}\Chintamani Gems"; Filename: "{app}\ChintamaniGems.exe"; IconFilename: "{app}\ChintamaniGems.ico"
Name: "{userdesktop}\Chintamani Gems"; Filename: "{app}\ChintamaniGems.exe"; IconFilename: "{app}\ChintamaniGems.ico"; Tasks: desktopicon
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "DRCInventorySync"; Flags: deletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Chintamani Gems"; ValueData: """{app}\ChintamaniGems.exe"" --startup"; Tasks: startup; Flags: uninsdeletevalue
[InstallDelete]
Type: files; Name: "{userprograms}\DRCInventorySync.lnk"
Type: files; Name: "{userdesktop}\DRCInventorySync.lnk"
Type: files; Name: "{userstartup}\DRCInventorySync.lnk"
Type: files; Name: "{userstartup}\Chintamani Gems.lnk"
[Run]
Filename: "{app}\ChintamaniGems.exe"; Flags: nowait; Check: IsUpdate
Filename: "{app}\ChintamaniGems.exe"; Description: "Open Chintamani Gems"; Flags: nowait postinstall skipifsilent; Check: NotUpdate
#endif
[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;
function NotUpdate: Boolean;
begin
  Result := not IsUpdate;
end;

procedure InitializeWizard;
var
  PreviousDir: String;
begin
  PreviousDir := WizardDirValue;
  while (Length(PreviousDir) > 3) and (PreviousDir[Length(PreviousDir)] = '\') do
    Delete(PreviousDir, Length(PreviousDir), 1);
  if CompareText(ExtractFileName(PreviousDir), 'DRCInventorySync') = 0 then
    WizardForm.DirEdit.Text := AddBackslash(ExtractFileDir(PreviousDir)) + 'ChintamaniGems';
end;
