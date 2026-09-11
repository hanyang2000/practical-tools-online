param(
    [Parameter(Mandatory = $false)]
    [string]$AppRoot = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$AppRoot = (Resolve-Path $AppRoot).Path
$PostgresBin = Join-Path $AppRoot "postgresql\bin"
$BackupRoot = Join-Path $AppRoot "data\backups"

function Find-PgTool([string]$Name) {
    $bundled = Join-Path $PostgresBin $Name
    if (Test-Path $bundled) { return $bundled }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Ask([string]$Prompt, [string]$Default = "") {
    $value = Read-Host $(if ($Default) { "$Prompt [$Default]" } else { $Prompt })
    if (-not $value -and $Default) { return $Default }
    return $value
}

Clear-Host
Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host "Practical Tools Online - PostgreSQL 最终备份助手" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host "请在旧中心已停止后运行，确保备份期间数据库不会继续写入。"
Write-Host "此助手会显示 pg_dump 的原始错误，不会把密码写入日志。"
Write-Host ""

$pgDump = Find-PgTool "pg_dump.exe"
if (-not $pgDump) {
    Write-Host "找不到 pg_dump.exe。请确认安装包的 postgresql\bin 存在，或把 PostgreSQL\bin 加入 PATH。" -ForegroundColor Red
    exit 2
}
Write-Host "使用 pg_dump：$pgDump" -ForegroundColor DarkGray

$hostName = Ask "数据库地址" "127.0.0.1"
$port = Ask "数据库端口" "5432"
$database = Ask "数据库名"
$user = Ask "数据库用户名"
if (-not $database -or -not $user) { throw "数据库名和数据库用户名不能为空" }
$password = Read-Secret "数据库密码"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force $BackupRoot | Out-Null
$output = Join-Path $BackupRoot "practical-center-$timestamp.dump"
$logPath = Join-Path $BackupRoot "practical-center-$timestamp-pg_dump.log"

$oldPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $password
    $arguments = @(
        "--format=custom",
        "--verbose",
        "--no-password",
        "--host=$hostName",
        "--port=$port",
        "--username=$user",
        "--file=$output",
        $database
    )
    Write-Host "正在测试数据库连接……"
    $ready = Find-PgTool "pg_isready.exe"
    if ($ready) {
        & $ready "--host=$hostName" "--port=$port" "--dbname=$database" "--username=$user" 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            throw "pg_isready 连接测试失败，退出码：$LASTEXITCODE"
        }
    }

    Write-Host "正在生成备份：$output"
    $rawOutput = @(& $pgDump @arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $rawOutput | Out-File -FilePath $logPath -Encoding utf8
    $rawOutput | ForEach-Object { Write-Host $_ }
    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "pg_dump 原始输出已保存：$logPath" -ForegroundColor Yellow
        throw "pg_dump 失败，退出码：$exitCode"
    }
}
finally {
    if ($null -eq $oldPassword) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    else { $env:PGPASSWORD = $oldPassword }
}

$sha256 = (Get-FileHash -Algorithm SHA256 -Path $output).Hash.ToLowerInvariant()
Write-Host ""
Write-Host "备份成功：$output" -ForegroundColor Green
Write-Host "文件大小：$((Get-Item $output).Length) 字节"
Write-Host "SHA-256：$sha256"
Write-Host "请把 .dump 文件和 SHA-256 一起复制到新 Windows 主机。"
