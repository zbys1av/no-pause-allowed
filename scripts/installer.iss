[Setup]
AppId={{6C49F572-A3E2-4A1A-911D-D651ED8AE879}
AppName=Smart Video Cutter
AppVersion=1.0.0
DefaultDirName={localappdata}\Programs\SmartVideoCutter
DefaultGroupName=Smart Video Cutter
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=SmartVideoCutter-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\SmartVideoCutter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Smart Video Cutter"; Filename: "{app}\SmartVideoCutter.exe"
Name: "{group}\Uninstall Smart Video Cutter"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\SmartVideoCutter.exe"; Description: "Launch Smart Video Cutter"; Flags: nowait postinstall skipifsilent
