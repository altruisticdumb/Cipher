; Cipher installer (Inno Setup 6). Build with build_installer.bat, or open this file in
; the Inno Setup compiler after running "flet pack" (see HANDOFF.md).
;
; Rules this script keeps:
;  - the person chooses at the start: "for all users" (Program Files, asks Windows for
;    permission) or "only for me" (their own AppData\Programs folder, no permission prompt)
;  - registers the .cipher file type for whichever choice was made (HKA = all users or just
;    this user), so double-clicking a vault opens Cipher
;  - never deletes vaults, backups or settings: the uninstaller removes only the program,
;    its shortcuts and its own file-type entries. Vaults live in the user's own folders.
#define AppVersion "1.0.1"

[Setup]
AppId={{7B335C37-A55E-487F-BB8A-7AF7E196AEC1}
AppName=Cipher
AppVersion={#AppVersion}
AppPublisher=Danny Dickenson
DefaultDirName={autopf}\Cipher
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=cipher.ico
UninstallDisplayIcon={app}\cipher.ico
OutputDir=output
OutputBaseFilename=CipherSetup-{#AppVersion}

[Tasks]
Name: "associate"; Description: "Open Cipher vault files (.cipher) with Cipher"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Cipher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "cipher.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Cipher"; Filename: "{app}\Cipher.exe"; IconFilename: "{app}\cipher.ico"
Name: "{autodesktop}\Cipher"; Filename: "{app}\Cipher.exe"; IconFilename: "{app}\cipher.ico"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.cipher"; ValueType: string; ValueName: ""; ValueData: "Cipher.Vault"; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.cipher\OpenWithProgids"; ValueType: string; ValueName: "Cipher.Vault"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Cipher.Vault"; ValueType: string; ValueName: ""; ValueData: "Cipher vault"; Flags: uninsdeletekey; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Cipher.Vault\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\cipher.ico"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Cipher.Vault\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Cipher.exe"" ""%1"""; Tasks: associate

[Run]
Filename: "{app}\Cipher.exe"; Description: "Start Cipher"; Flags: nowait postinstall skipifsilent

; No [UninstallDelete] section on purpose: vaults, backups and settings are never removed.
