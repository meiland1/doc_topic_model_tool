; Inno Setup script — compile with:  iscc /DAppVersion=0.2.0 packaging\windows\installer.iss
; Input: dist\Topic Model Tool\ from PyInstaller. Output: dist\TopicModelTool-Setup-<version>.exe
;
; Per-user install: no Administrator rights needed. Works fully offline.
; If a theme-naming model pack sits in the same folder as this installer, it is
; installed too, so labs can copy the whole kit onto a USB drive. Packs may be a
; single *.tmtpack or split parts *.tmtpack.001, .002 … (llama3.1:8b is split
; to stay under GitHub's 2 GiB per-file limit). Keep every part together.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#define AppName "Topic Model Tool"
#define AppExe "Topic Model Tool.exe"

[Setup]
AppId={{6E4C2A7B-9F1D-4C63-9B8E-3D2F6A1C7E55}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Topic Model Tool contributors (open source)
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=TopicModelTool-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Room for the app, engine and the default llama3.1:8b naming model (~4.9 GB).
ExtraDiskSpaceRequired=8000000000
UninstallDisplayName={#AppName}

[Messages]
WelcomeLabel2=This installs [name/ver] on your computer.%n%nEverything it needs is included. After installing, it works without an internet connection, and your documents never leave this computer.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--self-test"; StatusMsg: "Checking the installation…"; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the app's shared models; never touch research project folders.
Type: filesandordirs; Name: "{localappdata}\DocTopicModelTool\engine-models"
Type: filesandordirs; Name: "{localappdata}\DocTopicModelTool\logs"

[Code]
type
  TMemoryStatusEx = record
    dwLength: Cardinal;
    dwMemoryLoad: Cardinal;
    ullTotalPhys: Int64;
    ullAvailPhys: Int64;
    ullTotalPageFile: Int64;
    ullAvailPageFile: Int64;
    ullTotalVirtual: Int64;
    ullAvailVirtual: Int64;
    ullAvailExtendedVirtual: Int64;
  end;

function GlobalMemoryStatusEx(var lpBuffer: TMemoryStatusEx): Boolean;
  external 'GlobalMemoryStatusEx@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
var
  Status: TMemoryStatusEx;
  GiB: Int64;
begin
  Result := True;
  Status.dwLength := SizeOf(Status);
  if GlobalMemoryStatusEx(Status) then
  begin
    GiB := Status.ullTotalPhys div (1024 * 1024 * 1024);
    // llama3.1:8b needs roughly 6-8 GB of free memory while naming themes.
    if GiB < 12 then
      Result := MsgBox('This computer has about ' + IntToStr(GiB) + ' GB of memory. ' +
        'Theme finding will work, but automatic theme naming with the default 8B model ' +
        'may be slow or unavailable. Continue installing?' + #13#10 + #13#10 +
        'Tip: close other programs while naming themes. ' +
        'Continue installing?', mbConfirmation, MB_YESNO) = IDYES;
  end;
end;

procedure InstallPacksMatching(Pattern: String);
var
  FindRec: TFindRec;
  Folder, Pack: String;
  Code: Integer;
begin
  Folder := ExpandConstant('{src}');
  if FindFirst(Folder + '\' + Pattern, FindRec) then
  begin
    try
      repeat
        Pack := Folder + '\' + FindRec.Name;
        WizardForm.StatusLabel.Caption := 'Installing the theme-naming model ' + FindRec.Name +
          '… this can take several minutes for large models.';
        Exec(ExpandConstant('{app}\{#AppExe}'), '--install-model-pack "' + Pack + '"', '',
             SW_HIDE, ewWaitUntilTerminated, Code);
        if Code <> 0 then
          MsgBox('The theme-naming model ' + FindRec.Name + ' could not be installed. ' +
                 'Check that every part is in the same folder and there is enough disk space. ' +
                 'You can add it later from the start screen.', mbInformation, MB_OK);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    InstallPacksMatching('*.tmtpack');
    // Split packs: pass only the first part; the app reads the rest in order.
    InstallPacksMatching('*.tmtpack.001');
  end;
end;
