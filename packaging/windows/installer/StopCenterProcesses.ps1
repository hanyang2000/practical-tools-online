param(
    [Parameter(Mandatory = $true)]
    [string]$AppRoot,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $AppRoot 'installer\PracticalToolsOnline-install-stop.log'
}
$logPath = [IO.Path]::GetFullPath($LogPath)
try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
    Set-Content -LiteralPath $logPath -Value "Pre-install process check started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding UTF8
} catch { }
function Write-CheckLog {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    Write-Output $line
}
trap {
    $detail = ($_ | Out-String).Trim()
    try { Write-CheckLog ("Checker exception: " + $detail) } catch { }
    exit 3
}

$root = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$names = @('PracticalToolsOnline.exe', 'PracticalToolsCenterConsole.exe', 'python.exe', 'pythonw.exe', 'powershell.exe', 'postgres.exe', 'pg_ctl.exe', 'cmd.exe')

function Get-TargetProcesses {
    $items = Get-CimInstance Win32_Process
    return @($items | Where-Object {
        if ($names -notcontains $_.Name) { return $false }
        $text = "$($_.ExecutablePath) $($_.CommandLine)"
        if ($_.Name -eq 'cmd.exe' -and $text -notmatch '(?i)(StartCenter|MigrationAssistant|BackupCenter)\.cmd') { return $false }
        if ($_.Name -eq 'powershell.exe' -and $text -notmatch '(?i)CenterConsole\.ps1') { return $false }
        return $text.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Get-TargetServices {
    try {
        $items = Get-CimInstance Win32_Service -ErrorAction Stop
    } catch {
        Write-CheckLog ("Cannot read Windows services; continuing with processes: " + $_.Exception.Message)
        return @()
    }
    return @($items | Where-Object {
        if ($_.State -eq 'Stopped') { return $false }
        $text = "$($_.PathName) $($_.DisplayName)"
        return $text.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

$deadline = (Get-Date).AddSeconds(30)
do {
    $services = Get-TargetServices
    foreach ($service in $services) {
        Write-CheckLog ("Stopping matching Windows service: $($service.Name)")
        & sc.exe stop $service.Name *> $null
        try { Stop-Service -Name $service.Name -Force -ErrorAction SilentlyContinue } catch { }
    }

    $targets = Get-TargetProcesses
    foreach ($item in $targets) {
        Write-CheckLog ("Force stopping matching process: $($item.Name)[$($item.ProcessId)]")
        & taskkill.exe /PID $item.ProcessId /T /F *> $null
    }

    Start-Sleep -Milliseconds 500
    $remaining = Get-TargetProcesses
    $remainingServices = Get-TargetServices
    if ($remaining.Count -eq 0 -and $remainingServices.Count -eq 0) {
        exit 0
    }
} while ((Get-Date) -lt $deadline)

if ($remaining.Count -gt 0 -or $remainingServices.Count -gt 0) {
    $processText = (($remaining | ForEach-Object { "$($_.Name)[$($_.ProcessId)]" }) -join ', ')
    $serviceText = (($remainingServices | ForEach-Object { "$($_.Name)" }) -join ', ')
    Write-CheckLog ("Matching processes or services are still running. Processes: $processText; Services: $serviceText")
    exit 2
}
Write-CheckLog 'All matching processes and services have stopped.'
exit 0
