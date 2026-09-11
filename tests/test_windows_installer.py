from __future__ import annotations

import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "windows" / "installer"


def test_windows_installer_inputs_exist_and_target_current_version():
    required = [
        INSTALLER / "PracticalToolsOnline.iss",
        INSTALLER / "PracticalToolsOnline.nsi",
        INSTALLER / "PracticalToolsOnlineMigration.ps1",
        INSTALLER / "BackupCenter.ps1",
        INSTALLER / "MigrationAssistant.cmd",
        INSTALLER / "BackupCenter.cmd",
        INSTALLER / "StartCenter.cmd",
        INSTALLER / "verify_center_install.py",
        INSTALLER / "StopCenterProcesses.ps1",
        INSTALLER / "CenterConsole.ps1",
        INSTALLER / "CenterConsole.hta",
        INSTALLER / "CenterController.py",
        INSTALLER / "CenterConsole.ico",
        INSTALLER / "CenterConsoleIcon.png",
        INSTALLER / "CenterConsoleIconSource.png",
        ROOT / "packaging" / "build_installer.cmd",
        ROOT / "packaging" / "build_installer_nsis.sh",
        ROOT / "packaging" / "build_center_console_icon.py",
        ROOT / "packaging" / "center_console.py",
        ROOT / "packaging" / "center_console.spec",
        ROOT / "packaging" / "windows" / "version_info.txt",
    ]
    assert all(path.is_file() for path in required)
    iss = (INSTALLER / "PracticalToolsOnline.iss").read_text(encoding="utf-8")
    assert "PracticalToolsOnline-Setup-v" in iss
    assert "PrivilegesRequired=admin" in iss
    assert "Data is intentionally retained" in iss
    nsi = (INSTALLER / "PracticalToolsOnline.nsi").read_text(encoding="utf-8")
    assert "RequestExecutionLevel admin" in nsi
    assert "WriteUninstaller" in nsi
    assert "Deliberately keep $INSTDIR" in nsi
    assert 'postgresql\\data\\*' in nsi
    assert 'postgresql\\data\\*' in iss
    assert 'InstallDir "D:\\PracticalToolsOnline"' in nsi
    assert 'DefaultDirName=D:\\PracticalToolsOnline' in iss
    assert "PracticalToolsCenterConsole.exe" in nsi
    assert "PracticalToolsCenterConsole.exe" in iss
    assert "CenterConsole.ps1" in nsi
    assert "CenterConsole.ps1" in iss
    assert "CenterConsole.hta" in nsi
    assert "CenterController.py" in nsi
    assert "CenterConsole.ico" in nsi
    assert "CenterConsoleIcon.png" in nsi
    assert "CenterConsole.hta" in iss
    assert "CenterController.py" in iss
    assert "CenterConsole.ico" in iss
    assert "CenterConsoleIcon.png" in iss
    assert "IconFilename" in iss
    assert "mshta.exe" in nsi
    assert "mshta.exe" in iss
    assert "powershell.exe" in nsi
    assert "powershell.exe" in iss
    assert "StopExistingCenterProcesses" in nsi
    assert "StopCenterProcesses.ps1" in nsi
    assert "BeforeInstall: StopExistingCenterProcesses" in iss
    assert "StopCenterProcesses.ps1" in iss
    stopper = (INSTALLER / "StopCenterProcesses.ps1").read_text(encoding="utf-8")
    assert "Get-CimInstance Win32_Service" in stopper
    assert "sc.exe stop" in stopper
    assert "PracticalToolsCenterConsole.exe" in stopper
    assert "taskkill.exe /PID" in stopper
    assert "PracticalToolsOnline-install-stop.log" in stopper
    assert "-WindowStyle Hidden" in nsi
    assert "-WindowStyle Hidden" in iss
    assert "-LogPath" in nsi
    assert "-LogPath" in iss
    assert "PowerShell exit code" in nsi
    console = (INSTALLER / "CenterConsole.ps1").read_text(encoding="utf-8")
    assert "System.Windows.Forms" in console
    assert "Start-Tunnel" in console
    assert "Start-Center" in console
    assert "Stop-AllAndExit" in console
    assert "Stop-MatchingCenterProcesses" in console
    assert "Get-HostPerformance" in console
    assert "Get-ProcessCpuPercent" in console
    assert "TUNNEL_TOKEN" in console
    assert "tokenHint" in console
    assert "stopTunnel" in console
    assert "metricValues" in console
    assert "metricBars" in console
    assert "ProgressBar" in console
    assert "center-console-error.log" in console
    assert "[object]$Color" in console
    assert "[object]$Back" in console
    assert "failureText" in console
    hta = (INSTALLER / "CenterConsole.hta").read_text(encoding="utf-8")
    assert "<hta:application" in hta
    assert "TUNNEL_TOKEN" in hta
    assert "实时性能" in hta
    assert "window.onunload" in hta
    assert "CenterController.py" in hta
    assert "pythonw.exe" in hta
    assert "cscript.exe" not in hta.lower()
    assert "wscript.exe" not in hta.lower()
    assert "shell.Exec" not in hta
    assert "tasklist" not in hta.lower()
    assert "center-status-" in hta
    assert 'fso.BuildPath(logRoot, "center.pid")' in hta
    assert "stop-all-exit" in hta
    assert "CenterConsole.ico" in hta
    assert "CenterConsoleIcon.png" in hta
    assert "中心主机控制台" in hta
    assert "wmic" not in hta.lower()
    controller_path = INSTALLER / "CenterController.py"
    controller = controller_path.read_text(encoding="utf-8")
    compile(controller, str(controller_path), "exec")
    assert "CREATE_NO_WINDOW" in controller
    assert "GetSystemTimes" in controller
    assert "GlobalMemoryStatusEx" in controller
    assert "GetProcessMemoryInfo" in controller
    assert "launch_center" in controller
    assert "launch_tunnel" in controller
    assert "taskkill.exe" in controller
    assert "wmic.exe" not in controller.lower()
    assert "winmgmts" not in controller.lower()
    assert "powershell.exe" not in controller.lower()
    icon_build = (ROOT / "packaging" / "build_center_console_icon.py").read_text(encoding="utf-8")
    compile(icon_build, str(ROOT / "packaging" / "build_center_console_icon.py"), "exec")
    version_info = (ROOT / "packaging" / "windows" / "version_info.txt").read_text(encoding="utf-8")
    assert "filevers=(0, 6, 6, 0)" in version_info
    assert "ProductVersion', '0.6.10.0'" in version_info
    for launcher in (INSTALLER / "MigrationAssistant.cmd", INSTALLER / "BackupCenter.cmd"):
        assert '-AppRoot "%~dp0."' in launcher.read_text(encoding="utf-8")
    start = (INSTALLER / "StartCenter.cmd").read_text(encoding="utf-8")
    portable_start = (ROOT / "packaging" / "windows" / "portable" / "START.cmd").read_text(encoding="utf-8")
    assert 'portable\\START.cmd' in start
    assert "CENTER_ROOT=%~dp0.." in portable_start
    assert "runtime\\python.exe" in portable_start


def test_migration_wizard_supports_dump_bundle_and_guided_online_modes():
    wizard = (INSTALLER / "PracticalToolsOnlineMigration.ps1").read_text(encoding="utf-8")
    assert ".dump" in wizard and "pg_restore.exe" in wizard
    assert ".ptcenter.zip" in wizard and "--inspect-only" in wizard
    assert "Cloudflare Tunnel" in wizard
    assert "在线复制 PostgreSQL" in wizard
    assert "practical_*" in wizard
    assert "PRACTICAL_COOKIE_SECURE" in wizard
    assert "Start-Process -FilePath \"cmd.exe\"" in wizard
    assert "DATA_ONLY" in wizard
    assert "Inspect-Bundle $bundle ($choice -eq \"3\")" in wizard


def test_data_only_import_is_available_and_drops_old_runtime_links():
    service = (ROOT / "app" / "services" / "center_migration.py").read_text(encoding="utf-8")
    importer = (ROOT / "scripts" / "import_center_migration.py").read_text(encoding="utf-8")
    wizard = (INSTALLER / "PracticalToolsOnlineMigration.ps1").read_text(encoding="utf-8")
    assert "DATA_ONLY_MODELS" in service
    assert '"agent_id", "job_id"' in service
    assert "--data-only" in importer
    assert "verify_program=not args.data_only" in importer
    assert "Initialize-BundledPostgres" in wizard
    assert "Ensure-TargetDatabase" in wizard
    portable = (ROOT / "packaging" / "windows" / "portable" / "portable_center.py").read_text(encoding="utf-8")
    assert "standalone_data" in portable and 'root / "postgresql" / "data"' in portable
    assert "timeout=30" in portable
    assert '"-o", f"-p {port}"' in portable
    assert "Start-BundledPostgres" in wizard
    assert "Do not redirect pg_ctl stdout/stderr" in wizard
    assert "New-Item -ItemType Directory -Force (Split-Path -Parent $pgLog)" in wizard
    assert 'PGDATABASE = "postgres"' in wizard
    env_example = (ROOT / "packaging" / "center_windows.env.example").read_text(encoding="utf-8")
    assert "C:\\ProgramData" not in env_example
    release_builder = (ROOT / "packaging" / "build_release.py").read_text(encoding="utf-8")
    assert 'relative.parts[:2] == ("postgresql", "data")' in release_builder


def test_backup_helper_surfaces_pg_dump_output_and_cleans_password():
    backup = (INSTALLER / "BackupCenter.ps1").read_text(encoding="utf-8")
    assert "pg_dump 原始输出" in backup
    assert "pg_isready.exe" in backup
    assert "Get-FileHash -Algorithm SHA256" in backup
    assert "PGPASSWORD" in backup
    assert "Remove-Item Env:PGPASSWORD" in backup


def test_installer_readme_puts_planned_cutover_before_online_replication():
    readme = (INSTALLER / "README.md").read_text(encoding="utf-8")
    assert "不要求新机事先安装 PostgreSQL" in readme
    assert "postgresql\\data" in readme
    assert "StartCenter.cmd" in readme
    assert "18100" in readme
    assert "恢复 PostgreSQL `.dump`（推荐）" in readme
    assert "不要启动新机的 Tunnel" in readme
    assert "高级在线复制 PostgreSQL" in readme


def test_install_verifier_compiles_and_covers_public_and_business_routes():
    verify = ROOT / "packaging" / "windows" / "installer" / "verify_center_install.py"
    source = verify.read_text(encoding="utf-8")
    compile(source, str(verify), "exec")
    for path in (
        "/api/health",
        "/api/screenshot/list",
        "/api/analysis/metrics",
        "/api/admin/status",
    ):
        assert path in source
