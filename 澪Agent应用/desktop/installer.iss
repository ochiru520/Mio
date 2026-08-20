#define MyAppName "Mio"
#define MyAppVersion "0.7.0"
#define MyAppPublisher "Mio Project"
#define MyAppExeName "Mio.exe"

[Setup]
AppId={{6D807D33-DAA4-4A61-A79F-37A78E32C029}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Mio
UsePreviousAppDir=no
DisableDirPage=no
DefaultGroupName={#MyAppName}
OutputDir=..\release
OutputBaseFilename=Mio-0.7.0-Windows-x64-Setup
SetupIconFile=mio.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl, languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked
Name: "startup"; Description: "开机后自动启动 Mio"; GroupDescription: "自动启动："; Flags: unchecked

[Files]
Source: "..\release\Mio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal\agent_frontend\assets"
Type: filesandordirs; Name: "{app}\_internal\agent_frontend\live2d-pet\models\fili"
Type: files; Name: "{app}\_internal\agent_frontend\live2d-pet\licenses\FILI-CC0.txt"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MioAgent"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Code]
var
  DataModePage: TInputOptionWizardPage;
  DataDirPage: TInputDirWizardPage;

function InitialDataDir: String;
var
  AppDir: String;
  RequestedDir: String;
begin
  RequestedDir := Trim(ExpandConstant('{param:DataDir|}'));
  if RequestedDir <> '' then
  begin
    Result := RequestedDir;
    Exit;
  end;

  AppDir := RemoveBackslashUnlessRoot(WizardDirValue);
  if AppDir = '' then
    AppDir := RemoveBackslashUnlessRoot(ExpandConstant('{localappdata}\Mio'));
  Result := GetPreviousData('DataDir', AppDir + '\Data');
end;

procedure InitializeWizard;
begin
  DataModePage := CreateInputOptionPage(
    wpSelectDir,
    '选择数据使用方式',
    '这次安装要使用哪一份 Mio 数据？',
    '默认将数据放在 Mio 程序目录内的 Data 文件夹。选择全新数据后，原来的聊天、日记、头像和设置不会出现在这个安装实例中。',
    True,
    False
  );
  DataModePage.Add('创建全新独立数据（进入首次启动流程）');
  DataModePage.Add('沿用原有数据（保留当前聊天、日记和设置）');
  DataModePage.SelectedValueIndex := 0;

  DataDirPage := CreateInputDirPage(
    DataModePage.ID,
    '选择数据保存位置',
    'Mio 的数据保存在哪里？',
    '您可以自由选择数据目录。请勿选择程序目录本身；使用全新空目录可体验首次启动流程。',
    False,
    SetupMessage(msgNewFolderName)
  );
  DataDirPage.Add('');
  DataDirPage.Values[0] := InitialDataDir;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  SelectedDir: String;
begin
  Result := True;
  if CurPageID = DataModePage.ID then
  begin
    if DataModePage.SelectedValueIndex = 0 then
      SelectedDir := RemoveBackslashUnlessRoot(WizardDirValue) + '\Data'
    else
      SelectedDir := InitialDataDir;
    DataDirPage.Values[0] := SelectedDir;
  end
  else if CurPageID = DataDirPage.ID then
  begin
    SelectedDir := Trim(DataDirPage.Values[0]);
    if SelectedDir = '' then
    begin
      MsgBox('请选择数据保存位置。', mbError, MB_OK);
      Result := False;
    end
    else if CompareText(RemoveBackslashUnlessRoot(SelectedDir), RemoveBackslashUnlessRoot(WizardDirValue)) = 0 then
    begin
      MsgBox('数据目录不能与程序安装目录完全相同。请在安装目录中建立一个数据子目录，或选择其他位置。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataDir', DataDirPage.Values[0]);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not SaveStringToFile(
      ExpandConstant('{app}\数据目录.txt'),
      DataDirPage.Values[0],
      False
    ) then
      RaiseException('无法保存数据目录配置。');
    ForceDirectories(DataDirPage.Values[0]);
    if not SaveStringToFile(
      AddBackslash(DataDirPage.Values[0]) + '安装来源目录.txt',
      ExpandConstant('{src}'),
      False
    ) then
      RaiseException('无法保存安装来源目录。');
  end;
end;
