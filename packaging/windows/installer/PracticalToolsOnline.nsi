Unicode True
!include "LogicLib.nsh"
!include "MUI2.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.6.10"
!endif

!define APP_NAME "Practical Tools Online"
!define APP_PUBLISHER "Practical Tools Online"
!define SOURCE_DIR "..\..\..\dist\PracticalToolsOnlinePortable"
!define OUTPUT_DIR "..\..\..\dist\installer"

Name "${APP_NAME}"
OutFile "${OUTPUT_DIR}\PracticalToolsOnline-Setup-v${APP_VERSION}.exe"
InstallDir "D:\PracticalToolsOnline"
InstallDirRegKey HKLM "Software\PracticalToolsOnline" "InstallDir"
RequestExecutionLevel admin
Unicode True
SetCompressor /SOLID lzma
SetDatablockOptimize on
XPStyle on
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=2052 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME} Windows installer"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "${APP_PUBLISHER}"

!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetRegView 64
FunctionEnd

Section "Practical Tools Online center" SEC_CENTER
  SectionIn RO
  Call StopExistingCenterProcesses
  SetOutPath "$INSTDIR"
  ; Never package a live PostgreSQL cluster. Runtime data belongs under
  ; Beside the install directory and must survive upgrades/reinstalls untouched.
  File /r /x ".env" /x "agent.json" /x "state.json" /x "browser_state" /x "data" /x "postgresql\data\*" /x "__pycache__" "${SOURCE_DIR}\*.*"

  SetOutPath "$INSTDIR\installer"
  File "CenterConsole.hta"
  File "CenterController.py"
  File "CenterConsole.ico"
  File "CenterConsoleIcon.png"
  File "CenterConsole.ps1"
  File "PracticalToolsOnlineMigration.ps1"
  File "BackupCenter.ps1"
  File "verify_center_install.py"

  SetOutPath "$INSTDIR"
  File "MigrationAssistant.cmd"
  File "StartCenter.cmd"
  File "BackupCenter.cmd"

  CreateDirectory "$INSTDIR\data"
  CreateDirectory "$INSTDIR\postgresql"
  WriteRegStr HKLM "Software\PracticalToolsOnline" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\Practical Tools Online"
  CreateShortCut "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 迁移助手.lnk" "$INSTDIR\MigrationAssistant.cmd" "" "$INSTDIR\runtime\python.exe"
  CreateShortCut "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 旧中心数据库备份.lnk" "$INSTDIR\BackupCenter.cmd" "" "$INSTDIR\runtime\python.exe"
  CreateShortCut "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 启动中心.lnk" "$INSTDIR\StartCenter.cmd" "" "$INSTDIR\runtime\python.exe"
  IfFileExists "$INSTDIR\installer\CenterConsole.hta" 0 console_native
  CreateShortCut "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 中心控制台.lnk" "$SYSDIR\mshta.exe" '"$INSTDIR\installer\CenterConsole.hta"' "$INSTDIR\installer\CenterConsole.ico" 0
  Goto console_done
console_native:
  IfFileExists "$INSTDIR\PracticalToolsCenterConsole.exe" 0 console_powershell
  CreateShortCut "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 中心控制台.lnk" "$INSTDIR\PracticalToolsCenterConsole.exe" "" "$INSTDIR\installer\CenterConsole.ico" 0
  Goto console_done
console_powershell:
  CreateShortCut "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 中心控制台.lnk" "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\installer\CenterConsole.ps1" -AppRoot "$INSTDIR"' "$INSTDIR\installer\CenterConsole.ico" 0
console_done:
SectionEnd

Function StopExistingCenterProcesses
  InitPluginsDir
  CreateDirectory "$INSTDIR\installer"
  FileOpen $1 "$INSTDIR\installer\PracticalToolsOnline-install-stop.log" w
  FileWrite $1 "NSIS pre-install checker started.$\r$\n"
  FileClose $1
  SetOutPath "$PLUGINSDIR"
  File /oname=StopCenterProcesses.ps1 "StopCenterProcesses.ps1"
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "$PLUGINSDIR\StopCenterProcesses.ps1" -AppRoot "$INSTDIR" -LogPath "$INSTDIR\installer\PracticalToolsOnline-install-stop.log"' $0
  FileOpen $1 "$INSTDIR\installer\PracticalToolsOnline-install-stop.log" a
  FileWrite $1 "PowerShell exit code: $0$\r$\n"
  FileClose $1
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "安装前检查未通过，安装已停止。详细原因已记录到：$INSTDIR\installer\PracticalToolsOnline-install-stop.log"
    Abort
  ${EndIf}
FunctionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 迁移助手.lnk"
  Delete "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 旧中心数据库备份.lnk"
  Delete "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 启动中心.lnk"
  Delete "$SMPROGRAMS\Practical Tools Online\Practical Tools Online - 中心控制台.lnk"
  RMDir "$SMPROGRAMS\Practical Tools Online"
  DeleteRegKey HKLM "Software\PracticalToolsOnline"
  ; Deliberately keep $INSTDIR data, .env and PostgreSQL data for recoverability.
  ; The operator can remove the old install directory after the new center is
  ; accepted and backups are confirmed.
SectionEnd
