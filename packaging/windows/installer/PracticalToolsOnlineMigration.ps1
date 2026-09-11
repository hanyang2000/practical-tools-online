param(
    [Parameter(Mandatory = $false)]
    [string]$AppRoot = (Split-Path -Parent $PSScriptRoot)
)

# Windows-native migration assistant. It deliberately uses a guided,
# recoverable workflow. Passwords stay in memory and are not logged.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$AppRoot = (Resolve-Path $AppRoot).Path
$RuntimePython = Join-Path $AppRoot "runtime\python.exe"
$PostgresBin = Join-Path $AppRoot "postgresql\bin"
$DataRootDefault = Join-Path $AppRoot "data"

function Find-Tool([string]$Name) {
    $bundled = Join-Path $PostgresBin $Name
    if (Test-Path $bundled) { return $bundled }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Quote-Argument([string]$Value) {
    if ($null -eq $Value) { return '""' }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @(),
        [Parameter(Mandatory = $false)][string]$Password = $null,
        [Parameter(Mandatory = $false)][int]$TimeoutSeconds = 300
    )
    if (-not (Test-Path $FilePath) -and -not (Get-Command $FilePath -ErrorAction SilentlyContinue)) {
        throw "找不到程序：$FilePath"
    }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = (($Arguments | ForEach-Object { Quote-Argument ([string]$_) }) -join " ")
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $oldPassword = $env:PGPASSWORD
    try {
        if ($null -ne $Password) { $env:PGPASSWORD = $Password }
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $info
        [void]$process.Start()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            throw "程序执行超时：$FilePath"
        }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        return [pscustomobject]@{ ExitCode = $process.ExitCode; StdOut = $stdout; StdErr = $stderr }
    }
    finally {
        if ($null -eq $oldPassword) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
        else { $env:PGPASSWORD = $oldPassword }
    }
}

function Invoke-Psql {
    param(
        [string]$HostName, [string]$Port, [string]$Database,
        [string]$UserName, [string]$Password, [string]$Sql
    )
    $psql = Find-Tool "psql.exe"
    if (-not $psql) { throw "找不到 psql.exe，请检查 PostgreSQL 是否安装" }
    $result = Invoke-External $psql @("-h", $HostName, "-p", $Port, "-U", $UserName, "-d", $Database, "-v", "ON_ERROR_STOP=1", "-tAc", $Sql) $Password 120
    if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
    return $result.StdOut.Trim()
}

function Test-DbConnection([hashtable]$Db) {
    $ready = Find-Tool "pg_isready.exe"
    if ($ready) {
        $result = Invoke-External $ready @("-h", $Db.Host, "-p", $Db.Port, "-d", $Db.Name, "-U", $Db.User) $Db.Password 30
        if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
    }
    [void](Invoke-Psql $Db.Host $Db.Port $Db.Name $Db.User $Db.Password "SELECT 1")
}

function Ensure-TargetDatabase([hashtable]$Db) {
    if ($Db.Name -eq "postgres") { return }
    $createdb = Find-Tool "createdb.exe"
    if (-not $createdb) { throw "找不到 createdb.exe，无法创建目标数据库" }
    $oldDatabase = $env:PGDATABASE
    try {
        $env:PGDATABASE = "postgres"
        $result = Invoke-External $createdb @("-h", $Db.Host, "-p", $Db.Port, "-U", $Db.User, $Db.Name) $Db.Password 120
    }
    finally {
        if ($null -eq $oldDatabase) { Remove-Item Env:PGDATABASE -ErrorAction SilentlyContinue }
        else { $env:PGDATABASE = $oldDatabase }
    }
    if ($result.ExitCode -ne 0 -and (($result.StdErr + $result.StdOut) -notmatch "(?i)already exists|已存在")) {
        throw (($result.StdErr + $result.StdOut).Trim())
    }
}

function Test-TcpPort([string]$HostName, [int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(1200)) { return $false }
        $client.EndConnect($async)
        return $true
    }
    catch { return $false }
    finally { $client.Close() }
}

function Start-BundledPostgres([string]$PgCtl, [string]$PgData, [string]$PgLog, [string]$Port) {
    # Do not redirect pg_ctl stdout/stderr. The postgres child inherits those
    # handles and a redirected PowerShell pipe can remain open after pg_ctl
    # exits, making ReadToEnd wait forever. PostgreSQL's own -l log is the
    # reliable diagnostic channel for this launch.
    $logParent = Split-Path -Parent $PgLog
    New-Item -ItemType Directory -Force $logParent | Out-Null
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $PgCtl
    $arguments = @("start", "-D", $PgData, "-l", $PgLog, "-o", "-p $Port") | ForEach-Object { Quote-Argument ([string]$_) }
    $info.Arguments = ($arguments -join " ")
    $info.WorkingDirectory = $PostgresBin
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    if (-not $process.WaitForExit(30000)) {
        try { $process.Kill() } catch { }
        throw "PostgreSQL 启动命令超时；日志：$PgLog"
    }
    return $process.ExitCode
}

function Initialize-BundledPostgres([hashtable]$Db) {
    if ($Db.Host -notin @("127.0.0.1", "localhost", "::1")) {
        throw "内置 PostgreSQL 只能初始化本机地址，远程数据库请先自行安装并创建数据库"
    }
    $initdb = Find-Tool "initdb.exe"
    $pgctl = Find-Tool "pg_ctl.exe"
    $createdb = Find-Tool "createdb.exe"
    if (-not $initdb -or -not $pgctl -or -not $createdb) {
        throw "安装包缺少 initdb.exe、pg_ctl.exe 或 createdb.exe，无法初始化内置 PostgreSQL"
    }
    $pgRoot = Join-Path $AppRoot "postgresql"
    $pgData = Join-Path $pgRoot "data"
    $pgLog = Join-Path $AppRoot "data\logs\postgresql.log"
    New-Item -ItemType Directory -Force $pgRoot | Out-Null
    New-Item -ItemType Directory -Force (Split-Path -Parent $pgLog) | Out-Null
    if (-not (Test-Path (Join-Path $pgData "PG_VERSION"))) {
        $existing = Get-ChildItem -LiteralPath $pgData -Force -ErrorAction SilentlyContinue
        if ($existing) { throw "内置 PostgreSQL 数据目录已存在但未初始化：$pgData；为避免覆盖，已停止" }
        New-Item -ItemType Directory -Force $pgData | Out-Null
        $passwordFile = Join-Path $env:TEMP ("practical-pg-password-" + [Guid]::NewGuid().ToString("N") + ".txt")
        try {
            [IO.File]::WriteAllText($passwordFile, $Db.Password, [Text.UTF8Encoding]::new($false))
            Write-Host "正在初始化安装包内置 PostgreSQL……"
            $result = Invoke-External $initdb @("-D", $pgData, "-U", $Db.User, "--pwfile=$passwordFile", "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C") $null 900
            if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
        }
        finally { Remove-Item $passwordFile -Force -ErrorAction SilentlyContinue }
    }
    Write-Host "正在启动安装包内置 PostgreSQL（端口 $($Db.Port)）……"
    # Do not use pg_ctl's synchronous -w here. Start it without redirected
    # output and use our own bounded readiness loop below instead.
    $startExit = Start-BundledPostgres $pgctl $pgData $pgLog $Db.Port
    if ($startExit -ne 0 -and -not (Test-TcpPort "127.0.0.1" ([int]$Db.Port))) {
        $detail = "pg_ctl 返回码：$startExit"
        if (Test-Path $pgLog) { $detail = $detail + "；日志：" + ((Get-Content -LiteralPath $pgLog -Tail 40 -ErrorAction SilentlyContinue) -join " ") }
        throw ($detail + "；PostgreSQL 日志：" + $pgLog)
    }
    $ready = Find-Tool "pg_isready.exe"
    if ($ready) {
        $deadline = (Get-Date).AddSeconds(60)
        do {
            $check = Invoke-External $ready @("-h", $Db.Host, "-p", $Db.Port, "-d", "postgres", "-U", $Db.User) $Db.Password 15
            if ($check.ExitCode -eq 0) { break }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $deadline)
        if ($check.ExitCode -ne 0) { throw "内置 PostgreSQL 启动后未就绪；日志：$pgLog" }
    }
    Write-Host "正在创建目标数据库 $($Db.Name)……"
    if ($Db.Name -ne "postgres") {
        $oldDatabase = $env:PGDATABASE
        try {
            $env:PGDATABASE = "postgres"
            $result = Invoke-External $createdb @("-h", $Db.Host, "-p", $Db.Port, "-U", $Db.User, $Db.Name) $Db.Password 120
        }
        finally {
            if ($null -eq $oldDatabase) { Remove-Item Env:PGDATABASE -ErrorAction SilentlyContinue }
            else { $env:PGDATABASE = $oldDatabase }
        }
        if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
    }
}

function Ensure-StandaloneSharedAuthTables([hashtable]$Db) {
    $usersTable = Invoke-Psql $Db.Host $Db.Port $Db.Name $Db.User $Db.Password "SELECT to_regclass('public.users')"
    if (-not $usersTable) {
        Write-Host "未发现 users 表，正在为独立中心准备本地认证基础表……"
        $sql = @"
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    display_name VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'planner',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    avatar_storage_key VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_username ON users (username);
CREATE TABLE IF NOT EXISTS user_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_user_sessions_token_hash ON user_sessions (token_hash);
CREATE INDEX IF NOT EXISTS ix_user_sessions_expires_at ON user_sessions (expires_at);
"@
        [void](Invoke-Psql $Db.Host $Db.Port $Db.Name $Db.User $Db.Password $sql)
        Write-Host "[完成] 已准备本地认证基础表；首次打开中心时可创建新的管理员账号。" -ForegroundColor Green
    }
    else {
        $sessionsTable = Invoke-Psql $Db.Host $Db.Port $Db.Name $Db.User $Db.Password "SELECT to_regclass('public.user_sessions')"
        if (-not $sessionsTable) {
            $sql = @"
CREATE TABLE IF NOT EXISTS user_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_user_sessions_token_hash ON user_sessions (token_hash);
CREATE INDEX IF NOT EXISTS ix_user_sessions_expires_at ON user_sessions (expires_at);
"@
            [void](Invoke-Psql $Db.Host $Db.Port $Db.Name $Db.User $Db.Password $sql)
        }
    }
}

function Write-CenterEnv([hashtable]$Db, [string]$DataRoot, [bool]$CookieSecure) {
    $encodedUser = [Uri]::EscapeDataString($Db.User)
    $encodedPassword = [Uri]::EscapeDataString($Db.Password)
    $dbUrl = "postgresql+psycopg://{0}:{1}@{2}:{3}/{4}" -f $encodedUser, $encodedPassword, $Db.Host, $Db.Port, $Db.Name
    New-Item -ItemType Directory -Force $DataRoot | Out-Null
    $envPath = Join-Path $AppRoot ".env"
    $lines = @(
        "PRACTICAL_APP_ENV=production",
        "PRACTICAL_HOST=0.0.0.0",
        "PRACTICAL_PORT=18180",
        "PRACTICAL_DATABASE_URL=$dbUrl",
        "PRACTICAL_DATA_ROOT=$DataRoot",
        "PRACTICAL_COOKIE_SECURE=$($CookieSecure.ToString().ToLowerInvariant())",
        "PRACTICAL_SESSION_DAYS=7",
        "PRACTICAL_AUTO_CREATE_TABLES=false",
        "PRACTICAL_MAX_UPLOAD_BYTES=1073741824"
    )
    [IO.File]::WriteAllLines($envPath, $lines, [Text.UTF8Encoding]::new($false))
}

function Run-Alembic {
    $env:PRACTICAL_ENV_FILE = Join-Path $AppRoot ".env"
    $result = Invoke-External $RuntimePython @("-m", "alembic", "-c", (Join-Path $AppRoot "alembic.ini"), "upgrade", "head") $null 300
    if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
}

function Inspect-Bundle([string]$Bundle, [bool]$DataOnly = $false) {
    $scriptPath = Join-Path $AppRoot "scripts\import_center_migration.py"
    $arguments = @($scriptPath, $Bundle, "--inspect-only")
    if ($DataOnly) { $arguments += "--data-only" }
    $result = Invoke-External $RuntimePython $arguments $null 900
    if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
    return $result.StdOut
}

function Import-Bundle([string]$Bundle, [bool]$DataOnly = $false) {
    $scriptPath = Join-Path $AppRoot "scripts\import_center_migration.py"
    $arguments = @($scriptPath, $Bundle)
    if ($DataOnly) { $arguments += "--data-only" }
    $result = Invoke-External $RuntimePython $arguments $null 3600
    if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
    return $result.StdOut
}

function Restore-Dump([string]$Dump, [hashtable]$Db) {
    $restore = Find-Tool "pg_restore.exe"
    if (-not $restore) { throw "找不到 pg_restore.exe" }
    $result = Invoke-External $restore @("--clean", "--if-exists", "--no-owner", "--exit-on-error", "-h", $Db.Host, "-p", $Db.Port, "-U", $Db.User, "-d", $Db.Name, $Dump) $Db.Password 7200
    if ($result.ExitCode -ne 0) { throw (($result.StdErr + $result.StdOut).Trim()) }
}

function Copy-LocalData {
    param([string]$SourceRoot, [string]$TargetRoot)
    foreach ($name in @("storage", "analysis")) {
        $source = Join-Path $SourceRoot $name
        $target = Join-Path $TargetRoot $name
        if (-not (Test-Path $source)) {
            Write-Host "[提示] 未找到 $source，跳过。" -ForegroundColor Yellow
            continue
        }
        New-Item -ItemType Directory -Force $target | Out-Null
        & robocopy.exe $source $target /E /COPY:DAT /R:2 /W:2 /NFL /NDL /NP | Out-Host
        if ($LASTEXITCODE -gt 7) { throw "复制 $name 失败，robocopy 返回 $LASTEXITCODE" }
    }
}

function Read-DatabaseValues {
    $db = @{
        Host = (Read-Host "新数据库地址 [127.0.0.1]")
        Port = (Read-Host "新数据库端口 [5432]")
        Name = (Read-Host "新数据库名")
        User = (Read-Host "新数据库用户名")
        Password = Read-Secret "新数据库密码"
    }
    if (-not $db.Host) { $db.Host = "127.0.0.1" }
    if (-not $db.Port) { $db.Port = "5432" }
    if (-not $db.Name -or -not $db.User) { throw "数据库名和数据库用户名不能为空" }
    return $db
}

function Show-Header([string]$Text) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor DarkCyan
}

function Run-Verify {
    $verifyPath = Join-Path $AppRoot "installer\verify_center_install.py"
    $centerUrl = Read-Host "中心验收地址 [http://127.0.0.1:18180]"
    if (-not $centerUrl) { $centerUrl = "http://127.0.0.1:18180" }
    $username = Read-Host "验收账号（留空则只检查公开页面）"
    $args = @($verifyPath, "--base-url", $centerUrl)
    if ($username) { $args += @("--username", $username, "--password-stdin") }
    $password = if ($username) { Read-Secret "验收账号密码" } else { $null }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $RuntimePython
    $info.Arguments = (($args | ForEach-Object { Quote-Argument ([string]$_) }) -join " ")
    $info.WorkingDirectory = $AppRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    if ($username) { $process.StandardInput.WriteLine($password) }
    $process.StandardInput.Close()
    $output = $process.StandardOutput.ReadToEnd()
    $error = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    Write-Host $output
    if ($error) { Write-Host $error -ForegroundColor Yellow }
    if ($process.ExitCode -ne 0) { throw "功能验收存在失败项目" }
}

Clear-Host
Show-Header "Practical Tools Online - 中心安装与迁移助手"
Write-Host "默认路径：旧中心先停机生成最终备份，新机恢复后再切换 Cloudflare Tunnel。"
Write-Host "请不要在最终切换前启动新机的 Cloudflare Tunnel。"
Write-Host ""
Write-Host "请选择迁移方式："
Write-Host "  1. 恢复 PostgreSQL .dump（推荐）"
Write-Host "  2. 导入旧中心 .ptcenter.zip（目标 practical_* 表必须为空）"
Write-Host "  3. 仅导入旧主机程序数据（新账号、Agent、调度和任务）"
Write-Host "  4. 高级：在线复制 PostgreSQL（需要旧库与新机私网直连）"
$choice = Read-Host "请输入 1、2、3 或 4"
if ($choice -notin @("1", "2", "3", "4")) { throw "无效选择" }

try {
    Show-Header "步骤 1/6：检查新机工具和数据库"
    if (-not (Test-Path $RuntimePython)) { throw "缺少内置 Python runtime" }
    if (-not (Test-Path (Join-Path $AppRoot "scripts\import_center_migration.py"))) { throw "缺少中心迁移脚本" }
    $db = Read-DatabaseValues
    Write-Host "正在测试数据库连接……"
    try {
        Test-DbConnection $db
    }
    catch {
        if ($db.Host -in @("127.0.0.1", "localhost", "::1") -and -not (Test-TcpPort $db.Host ([int]$db.Port))) {
            Write-Host "未发现本机 PostgreSQL 正在监听 $($db.Host):$($db.Port)。"
            $initialize = Read-Host "是否使用安装包内置 PostgreSQL 初始化并创建数据库？[Y/N]"
            if ($initialize -match "^(?i:y|yes)$") {
                Initialize-BundledPostgres $db
                Test-DbConnection $db
            }
            else { throw }
        }
        elseif ($db.Host -in @("127.0.0.1", "localhost", "::1")) {
            Write-Host "发现本机 PostgreSQL 已在监听，但目标数据库可能尚未创建；正在尝试创建 $($db.Name)……"
            Ensure-TargetDatabase $db
            Test-DbConnection $db
        }
        else { throw }
    }
    Write-Host "[通过] 数据库连接正常。" -ForegroundColor Green

    if ($choice -eq "1") {
        Show-Header "步骤 2/6：恢复 PostgreSQL .dump"
        $dump = Read-Host "请输入 .dump/.backup 文件完整路径"
        if (-not (Test-Path $dump)) { throw "找不到备份文件：$dump" }
        Write-Host "pg_restore 会执行 --clean。目标数据库必须是新建空库。"
        $confirm = Read-Host "确认目标库可以被恢复覆盖？请输入 RESTORE"
        if ($confirm -ne "RESTORE") { throw "未确认恢复操作，已停止" }
        Restore-Dump $dump $db
        Write-Host "[完成] PostgreSQL 数据库恢复完成。" -ForegroundColor Green
    }
    elseif ($choice -eq "2" -or $choice -eq "3") {
        Show-Header "步骤 2/6：检查旧中心迁移包"
        $bundle = Read-Host "请输入 .ptcenter.zip 文件完整路径"
        if (-not (Test-Path $bundle)) { throw "找不到中心迁移包：$bundle" }
        $report = Inspect-Bundle $bundle ($choice -eq "3")
        Write-Host "[通过] 迁移包校验完成：" -ForegroundColor Green
        Write-Host $report
        if ($choice -eq "3") {
            Write-Host "数据优先模式不会导入旧账号、Agent、调度、任务和会话；截图旧 owner/Agent/job 关联会清空。"
            $confirm = Read-Host "请确认目标 practical_* 表为空；向导会准备本地 users 表，输入 DATA_ONLY 开始导入"
            if ($confirm -ne "DATA_ONLY") { throw "未确认数据优先导入操作，已停止" }
        }
        else {
            $confirm = Read-Host "请确认目标 practical_* 表为空，输入 IMPORT 开始导入"
            if ($confirm -ne "IMPORT") { throw "未确认导入操作，已停止" }
        }
    }
    else {
        Show-Header "步骤 2/6：在线复制前置条件"
        Write-Host "在线复制仅适合旧库与新机有私网直连的情况。不要通过 Cloudflare Tunnel 暴露 5432。"
        Write-Host "请先在旧库完成 wal_level=replica、复制账号和 pg_hba.conf，再回到此向导。"
        Write-Host "当前向导不会擅自修改旧主机配置；如果网络不满足条件，请返回选择 1。"
        exit 0
    }

    Show-Header "步骤 3/6：写入中心配置并执行 Alembic"
    $dataRoot = Read-Host "业务数据目录 [$DataRootDefault]"
    if (-not $dataRoot) { $dataRoot = $DataRootDefault }
    Write-Host "本地验收通常使用 HTTP；如果现在就要通过 HTTPS 的 Cloudflare 域名登录，请输入 Y。"
    $secureAnswer = Read-Host "HTTPS Cookie 安全模式 [N]"
    $cookieSecure = $secureAnswer -match "^(?i:y|yes)$"
    Write-CenterEnv $db $dataRoot $cookieSecure
    if ($choice -eq "3") { Ensure-StandaloneSharedAuthTables $db }
    [void](Run-Alembic)
    Write-Host "[完成] .env 已写入，数据库迁移已完成。" -ForegroundColor Green

    if ($choice -eq "2" -or $choice -eq "3") {
        Show-Header "步骤 4/6：导入 .ptcenter.zip 数据"
        $report = Import-Bundle $bundle ($choice -eq "3")
        Write-Host $report
        if ($choice -eq "3") {
            Write-Host "[完成] 旧程序数据和本地文件导入完成；账号、Agent、调度和任务需要在新机重新创建。" -ForegroundColor Green
        }
        else {
            Write-Host "[完成] 业务数据、分析文件和本地截图导入完成。" -ForegroundColor Green
        }
    }
    else {
        Show-Header "步骤 4/6：复制本地文件"
        $source = Read-Host "旧数据备份目录（包含 storage、analysis；使用 COS 可留空）"
        if ($source) { Copy-LocalData $source $dataRoot }
        else { Write-Host "[提示] 已跳过本地文件复制；local 存储必须稍后补齐 storage 和 analysis。" -ForegroundColor Yellow }
    }

    Show-Header "步骤 5/6：启动中心"
    $launch = Join-Path $AppRoot "portable\START.cmd"
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $launch) -WorkingDirectory $AppRoot | Out-Null
    Start-Sleep -Seconds 5
    Write-Host "中心已启动。日志：$AppRoot\data\logs\portable-center.log" -ForegroundColor Green

    Show-Header "步骤 6/6：验收已有功能"
    Run-Verify
    Write-Host ""
    Write-Host "基础功能验收完成。" -ForegroundColor Green
    Write-Host "请在确认页面收集、真实 Agent、真实淘宝登录和 COS 后，再切换 Cloudflare Tunnel。"
}
catch {
    Write-Host ""
    Write-Host "迁移没有完成：" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
