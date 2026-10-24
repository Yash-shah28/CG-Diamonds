[Setup]
AppName=Chintamani Gems
AppVersion=0.5.0
DefaultDirName={localappdata}\Programs\DRCInventorySync
PrivilegesRequired=lowest
OutputBaseFilename=ChintamaniGemsSetup
SetupIconFile=..\drc_sync\assets\chintamani-gems.ico
[Files]
Source: "..\dist\DRCInventorySync\*"; DestDir: "{app}"; Flags: recursesubdirs
[Icons]
Name: "{userprograms}\Chintamani Gems"; Filename: "{app}\ChintamaniGems.exe"
[Tasks]
Name: startup; Description: "Launch when signing in"
[Icons]
Name: "{userstartup}\Chintamani Gems"; Filename: "{app}\ChintamaniGems.exe"; Parameters: "--startup"; Tasks: startup




