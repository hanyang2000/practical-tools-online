; Standard Windows installer for the current complete center package.
; Build after refreshing dist\PracticalToolsOnlinePortable from the current
; source. Secrets and live database files are deliberately not included.

#ifndef AppVersion
  #define AppVersion "0.6.10"
#endif

#define AppName "Practical Tools Online"
#define AppPublisher "Practical Tools Online"
#define StageDir "..\..\..\dist\PracticalToolsOnlinePortable"
#define InstallerOut "..\..\..\dist\installer"

[Setup]
AppId={{A7B0F2C4-7F7D-4D41-9C65-6D7B0A7D9B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName=D:\PracticalToolsOnline
DefaultGroupName={#AppName}
OutputDir={#InstallerOut}
OutputBaseFilename=PracticalToolsOnline-Setup-v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
Uninstallable=yes
UninstallDisplayName={#AppName}
; Never remove the data directory during uninstall.
UninstallFilesDir={app}\uninstall

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".env;data\*;postgresql\data\*;capture-agent\data\*;capture-agent\agent.json"; BeforeInstall: StopExistingCenterProcesses
Source: "StopCenterProcesses.ps1"; DestDir: "{tmp}"; Flags: dontcopy
Source: "CenterConsole.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "CenterConsole.hta"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "CenterController.py"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "CenterConsole.ico"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "CenterConsoleIcon.png"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "PracticalToolsOnlineMigration.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "BackupCenter.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "verify_center_install.py"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "MigrationAssistant.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "StartCenter.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "BackupCenter.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\data"
Name: "{app}\postgresql"

[Code]
function UsePowerShellConsoleFallback(): Boolean;
begin
  Result := (not FileExists(ExpandConstant('{app}\installer\CenterConsole.hta'))) and
    (not FileExists(ExpandConstant('{app}\PracticalToolsCenterConsole.exe'))) and
    FileExists(ExpandConstant('{app}\installer\CenterConsole.ps1'));
end;

function UseHtaConsoleFallback(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\installer\CenterConsole.hta'));
end;

function UseNativeConsoleFallback(): Boolean;
begin
  Result := (not FileExists(ExpandConstant('{app}\installer\CenterConsole.hta'))) and
    FileExists(ExpandConstant('{app}\PracticalToolsCenterConsole.exe'));
end;

function StopExistingCenterProcesses(): Boolean;
var
  ResultCode: Integer;
  Params: string;
begin
  ExtractTemporaryFile('StopCenterProcesses.ps1');
  ForceDirectories(ExpandConstant('{app}\installer'));
  SaveStringToFile(ExpandConstant('{app}\installer\PracticalToolsOnline-install-stop.log'), 'Inno Setup pre-install checker started.' + #13#10, False);
  Params := '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\StopCenterProcesses.ps1') + '" -AppRoot "' + ExpandConstant('{app}') + '" -LogPath "' + ExpandConstant('{app}\installer\PracticalToolsOnline-install-stop.log') + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('无法启动安装前进程检查，安装已停止。', mbError, MB_OK);
    Result := False;
    exit;
  end;
  if ResultCode <> 0 then
  begin
    MsgBox('安装前检查未通过，安装已停止。详细原因已记录到：' + ExpandConstant('{app}\installer\PracticalToolsOnline-install-stop.log'), mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;

[Icons]
Name: "{group}\Practical Tools Online - 迁移助手"; Filename: "{app}\MigrationAssistant.cmd"; WorkingDir: "{app}"
Name: "{group}\Practical Tools Online - 旧中心数据库备份"; Filename: "{app}\BackupCenter.cmd"; WorkingDir: "{app}"
Name: "{group}\Practical Tools Online - 启动中心"; Filename: "{app}\StartCenter.cmd"; WorkingDir: "{app}"
Name: "{group}\Practical Tools Online - 中心控制台"; Filename: "{sys}\mshta.exe"; Parameters: "\"{app}\installer\CenterConsole.hta\""; WorkingDir: "{app}"; IconFilename: "{app}\installer\CenterConsole.ico"; Check: UseHtaConsoleFallback
Name: "{group}\Practical Tools Online - 中心控制台"; Filename: "{app}\PracticalToolsCenterConsole.exe"; WorkingDir: "{app}"; IconFilename: "{app}\installer\CenterConsole.ico"; Check: UseNativeConsoleFallback
Name: "{group}\Practical Tools Online - 中心控制台"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{app}\installer\CenterConsole.ps1\" -AppRoot \"{app}\""; WorkingDir: "{app}"; IconFilename: "{app}\installer\CenterConsole.ico"; Check: UsePowerShellConsoleFallback
Name: "{autodesktop}\Practical Tools Online - 迁移助手"; Filename: "{app}\MigrationAssistant.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面迁移助手快捷方式"; GroupDescription: "快捷方式："

[Run]
Filename: "{app}\MigrationAssistant.cmd"; Description: "打开中心安装与迁移向导"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Only remove installer-owned program files. Data is intentionally retained.
Type: filesandordirs; Name: "{app}\installer"
