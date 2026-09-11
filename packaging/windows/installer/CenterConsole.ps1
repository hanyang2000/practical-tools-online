param(
    [Parameter(Mandatory = $true)]
    [string]$AppRoot
)

# Keep this launcher ASCII-only for Windows PowerShell 5.1. Chinese labels
# are assembled from Unicode code points so legacy system code pages cannot
# corrupt the script before the window opens.
$ErrorActionPreference = 'Stop'
$script:root = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
$script:logRoot = Join-Path $script:root 'data\logs'
$script:centerProcess = $null
$script:tunnelProcess = $null
$script:centerCpuSample = $null
$script:isClosing = $false
$script:tunnelLog = Join-Path $script:logRoot 'cloudflare-tunnel.log'
$script:tunnelErrorLog = Join-Path $script:logRoot 'cloudflare-tunnel-error.log'

New-Item -ItemType Directory -Force -Path $script:logRoot | Out-Null
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

function U {
    param([int[]]$CodePoints)
    return (-join ($CodePoints | ForEach-Object { [char]$_ }))
}

trap {
    $detail = $_.Exception.Message
    try {
        $errorLog = Join-Path $script:logRoot 'center-console-error.log'
        Add-Content -LiteralPath $errorLog -Value ("[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $detail") -Encoding UTF8
    } catch { }
    try {
        $failureText = (U 0x4E2D 0x5FC3 0x63A7 0x5236 0x53F0 0x542F 0x52A8 0x5931 0x8D25 0xFF1A) + $detail + "`r`n`r`nDetails are in data\logs\center-console-error.log"
        [System.Windows.Forms.MessageBox]::Show($failureText, 'Center Console', 'OK', 'Error') | Out-Null
    } catch { }
    exit 1
}

$script:ui = @{
    appTitle = (U 0x5B9E 0x7528 0x5DE5 0x5177 0x5728 0x7EBF 0x7248)
    consoleTitle = (U 0x4E2D 0x5FC3 0x63A7 0x5236 0x53F0)
    center = (U 0x4E2D 0x5FC3 0x670D 0x52A1)
    tunnel = 'Cloudflare Tunnel'
    running = (U 0x8FD0 0x884C 0x4E2D)
    stopped = (U 0x5DF2 0x505C 0x6B62)
    startCenter = (U 0x542F 0x52A8 0x4E2D 0x5FC3)
    stopCenter = (U 0x505C 0x6B62 0x4E2D 0x5FC3)
    openWeb = (U 0x6253 0x5F00 0x7F51 0x9875)
    performance = (U 0x6027 0x80FD 0x5360 0x7528)
    centerCpu = (U 0x4E2D 0x5FC3 0x50A8 0x5668)
    centerMemory = (U 0x4E2D 0x5FC3 0x5185 0x5B58)
    hostCpu = (U 0x4E3B 0x673A 0x50A8 0x5668)
    hostMemory = (U 0x4E3B 0x673A 0x5185 0x5B58)
    diskFree = (U 0x7CFB 0x7EDF 0x76D8 0x5269 0x4F59)
    tunnelConfig = (U 0x969A 0x9053 0x914D 0x7F6E)
    mode = (U 0x8FD0 0x884C 0x6A21 0x5F0F)
    quickMode = (U 0x4E34 0x65F6 0x5FEB 0x901F 0x20 0x54 0x75 0x6E 0x6E 0x65 0x6C)
    namedMode = (U 0x547D 0x540D 0x20 0x54 0x75 0x6E 0x6E 0x65 0x6C)
    tokenMode = 'Token Tunnel'
    tunnelName = (U 0x54 0x75 0x6E 0x6E 0x65 0x6C 0x20 0x540D 0x79F0)
    token = 'Token'
    executable = (U 0x63 0x6C 0x6F 0x75 0x64 0x66 0x6C 0x61 0x72 0x65 0x64 0x20 0x7A0B 0x5E8F)
    browse = (U 0x6D4F 0x89C8)
    startTunnel = (U 0x542F 0x52A8 0x20 0x54 0x75 0x6E 0x6E 0x65 0x6C)
    stopTunnel = (U 0x505C 0x6B62 0x20 0x54 0x75 0x6E 0x6E 0x65 0x6C)
    startAll = (U 0x5168 0x90E8 0x542F 0x52A8)
    stopExit = (U 0x505C 0x6B62 0x5E76 0x9000 0x51FA)
    logs = (U 0x8FD0 0x884C 0x65E5 0x5FD7)
    quickHint = (U 0x5FEB 0x901F 0x6A21 0x5F0F 0x65E0 0x9700 0x20 0x54 0x6F 0x6B 0x65 0x6E 0xFF0C 0x5730 0x5740 0x4E3A 0x4E34 0x65F6 0x20 0x74 0x72 0x79 0x63 0x6C 0x6F 0x75 0x64 0x66 0x6C 0x61 0x72 0x65 0x2E 0x63 0x6F 0x6D 0x3002)
    namedHint = (U 0x547D 0x540D 0x6A21 0x5F0F 0x9700 0x8981 0x672C 0x673A 0x5DF2 0x5B8C 0x6210 0x20 0x43 0x6C 0x6F 0x75 0x64 0x66 0x6C 0x61 0x72 0x65 0x20 0x767B 0x5F55 0x3002)
    tokenHint = (U 0x54 0x6F 0x6B 0x65 0x6E 0x20 0x6A21 0x5F0F 0x53EA 0x9700 0x586B 0x5199 0x20 0x54 0x6F 0x6B 0x65 0x6E 0xFF0C 0x4E0D 0x9700 0x8981 0x20 0x54 0x75 0x6E 0x6E 0x65 0x6C 0x20 0x540D 0x79F0 0x3002)
    prompt = (U 0x63D0 0x793A)
    startFailed = (U 0x542F 0x52A8 0x5931 0x8D25)
    envMissing = (U 0x4E2D 0x5FC3 0x8FD0 0x884C 0x73AF 0x5883 0x672A 0x627E 0x5230 0x3002)
    tunnelMissing = (U 0x672A 0x627E 0x5230 0x20 0x63 0x6C 0x6F 0x75 0x64 0x66 0x6C 0x61 0x72 0x65 0x64 0xFF0C 0x8BF7 0x586B 0x5199 0x6B63 0x786E 0x7684 0x7A0B 0x5E8F 0x8DEF 0x5F84 0x3002)
    needName = (U 0x8BF7 0x8F93 0x5165 0x547D 0x540D 0x20 0x54 0x75 0x6E 0x6E 0x65 0x6C 0x7684 0x540D 0x79F0 0x3002)
    needToken = (U 0x8BF7 0x8F93 0x5165 0x20 0x54 0x6F 0x6B 0x65 0x6E 0x3002)
}

$script:colors = @{
    navy = [System.Drawing.Color]::FromArgb(15, 23, 42)
    blue = [System.Drawing.Color]::FromArgb(37, 99, 235)
    green = [System.Drawing.Color]::FromArgb(5, 150, 105)
    red = [System.Drawing.Color]::FromArgb(220, 38, 38)
    page = [System.Drawing.Color]::FromArgb(244, 247, 251)
    card = [System.Drawing.Color]::White
    border = [System.Drawing.Color]::FromArgb(226, 232, 240)
    text = [System.Drawing.Color]::FromArgb(30, 41, 59)
    muted = [System.Drawing.Color]::FromArgb(100, 116, 139)
    log = [System.Drawing.Color]::FromArgb(15, 23, 42)
    logText = [System.Drawing.Color]::FromArgb(226, 232, 240)
}

function Write-ConsoleLog {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Add-Content -LiteralPath (Join-Path $script:logRoot 'center-console.log') -Value $line -Encoding UTF8
    if ($script:logBox) {
        $script:logBox.AppendText($line + [Environment]::NewLine)
        $script:logBox.SelectionStart = $script:logBox.TextLength
        $script:logBox.ScrollToCaret()
    }
}

function Stop-ChildProcess {
    param([object]$Process)
    if ($null -eq $Process) { return }
    try { if (-not $Process.HasExited) { & taskkill.exe /PID $Process.Id /T /F *> $null } } catch { }
}

function Start-Center {
    if ($script:centerProcess -and -not $script:centerProcess.HasExited) { return $true }
    $native = Join-Path $script:root 'PracticalToolsOnline.exe'
    $runtime = Join-Path $script:root 'runtime\python.exe'
    $portable = Join-Path $script:root 'portable\portable_center.py'
    if (Test-Path -LiteralPath $native) {
        $script:centerProcess = Start-Process -FilePath $native -WorkingDirectory $script:root -WindowStyle Hidden -PassThru
    } elseif ((Test-Path -LiteralPath $runtime) -and (Test-Path -LiteralPath $portable)) {
        $script:centerProcess = Start-Process -FilePath $runtime -ArgumentList @('"' + $portable + '"') -WorkingDirectory $script:root -WindowStyle Hidden -PassThru
    } else {
        [System.Windows.Forms.MessageBox]::Show($script:ui.envMissing, $script:ui.startFailed, 'OK', 'Error')
        return $false
    }
    Write-ConsoleLog 'Center start requested.'
    return $true
}

function Stop-Center {
    Stop-ChildProcess $script:centerProcess
    $script:centerProcess = $null
    $script:centerCpuSample = $null
    Write-ConsoleLog 'Center stop requested.'
}

function Resolve-TunnelExecutable {
    $value = $script:tunnelPath.Text.Trim()
    if ($value -and (Test-Path -LiteralPath $value)) { return (Resolve-Path -LiteralPath $value).Path }
    $command = Get-Command $value -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Get-TunnelMode {
    switch ($script:tunnelMode.SelectedIndex) {
        1 { return 'named' }
        2 { return 'token' }
        default { return 'quick' }
    }
}

function Start-Tunnel {
    if ($script:tunnelProcess -and -not $script:tunnelProcess.HasExited) { return }
    $executable = Resolve-TunnelExecutable
    if (-not $executable) {
        [System.Windows.Forms.MessageBox]::Show($script:ui.tunnelMissing, $script:ui.startFailed, 'OK', 'Error')
        return
    }
    $mode = Get-TunnelMode
    $arguments = @('tunnel', '--no-autoupdate')
    $token = $null
    if ($mode -eq 'named') {
        $name = $script:tunnelName.Text.Trim()
        if (-not $name) {
            [System.Windows.Forms.MessageBox]::Show($script:ui.needName, $script:ui.startFailed, 'OK', 'Error')
            return
        }
        $arguments += @('run', $name)
    } elseif ($mode -eq 'token') {
        $token = $script:tunnelToken.Text.Trim()
        if (-not $token) {
            [System.Windows.Forms.MessageBox]::Show($script:ui.needToken, $script:ui.startFailed, 'OK', 'Error')
            return
        }
        # Cloudflare documents TUNNEL_TOKEN for remotely-managed tunnels.
        # It avoids placing the secret in the command line or our own log.
        $arguments += @('run')
    } else {
        $arguments += @('--url', 'http://127.0.0.1:18180')
    }
    New-Item -ItemType File -Force -Path $script:tunnelLog, $script:tunnelErrorLog | Out-Null
    $previousToken = $env:TUNNEL_TOKEN
    try {
        if ($mode -eq 'token') { $env:TUNNEL_TOKEN = $token }
        $script:tunnelProcess = Start-Process -FilePath $executable -ArgumentList $arguments -WorkingDirectory $script:root -WindowStyle Hidden -RedirectStandardOutput $script:tunnelLog -RedirectStandardError $script:tunnelErrorLog -PassThru
    } finally {
        if ($mode -eq 'token') {
            if ($null -eq $previousToken) { Remove-Item Env:TUNNEL_TOKEN -ErrorAction SilentlyContinue }
            else { $env:TUNNEL_TOKEN = $previousToken }
        }
    }
    Write-ConsoleLog 'Cloudflare Tunnel start requested.'
}

function Stop-Tunnel {
    Stop-ChildProcess $script:tunnelProcess
    $script:tunnelProcess = $null
    Write-ConsoleLog 'Cloudflare Tunnel stop requested.'
}

function Open-CenterWeb { Start-Process 'http://127.0.0.1:18180' }

function Stop-MatchingCenterProcesses {
    try {
        $names = @('PracticalToolsOnline.exe', 'PracticalToolsCenterConsole.exe', 'python.exe', 'pythonw.exe', 'postgres.exe', 'pg_ctl.exe', 'cmd.exe')
        $items = Get-CimInstance Win32_Process
        foreach ($item in @($items | Where-Object {
            if ($names -notcontains $_.Name) { return $false }
            $text = "$($_.ExecutablePath) $($_.CommandLine)"
            if ($_.Name -eq 'cmd.exe' -and $text -notmatch '(?i)(StartCenter|MigrationAssistant|BackupCenter)\.cmd') { return $false }
            $text.IndexOf($script:root, [StringComparison]::OrdinalIgnoreCase) -ge 0
        })) { & taskkill.exe /PID $item.ProcessId /T /F *> $null }
    } catch { }
}

function Get-ProcessCpuPercent {
    param([object]$Process)
    if ($null -eq $Process) { return $null }
    try {
        $Process.Refresh()
        if ($Process.HasExited) { $script:centerCpuSample = $null; return $null }
        $now = Get-Date
        $cpu = $Process.TotalProcessorTime
        if ($script:centerCpuSample) {
            $elapsed = ($now - $script:centerCpuSample.Time).TotalSeconds
            if ($elapsed -gt 0) {
                $delta = ($cpu - $script:centerCpuSample.Cpu).TotalSeconds
                $script:centerCpuSample = @{ Time = $now; Cpu = $cpu }
                return [Math]::Max(0, [Math]::Min(100, ($delta / $elapsed / [Environment]::ProcessorCount) * 100))
            }
        }
        $script:centerCpuSample = @{ Time = $now; Cpu = $cpu }
    } catch { $script:centerCpuSample = $null }
    return $null
}

function Get-HostPerformance {
    $result = @{ Cpu = $null; Memory = $null; MemoryTotal = $null; Disk = $null }
    try {
        $processor = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop
        $result.Cpu = [double]$processor.PercentProcessorTime
    } catch { }
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        if ([double]$os.TotalVisibleMemorySize -gt 0) {
            $used = [double]$os.TotalVisibleMemorySize - [double]$os.FreePhysicalMemory
            $result.Memory = ($used / [double]$os.TotalVisibleMemorySize) * 100
            $result.MemoryTotal = [double]$os.TotalVisibleMemorySize * 1KB
        }
    } catch { }
    try {
        $driveName = [IO.Path]::GetPathRoot($script:root).TrimEnd('\').TrimEnd(':')
        $drive = Get-PSDrive -Name $driveName -ErrorAction Stop
        if (($drive.Used + $drive.Free) -gt 0) { $result.Disk = ([double]$drive.Free / ([double]$drive.Used + [double]$drive.Free)) * 100 }
    } catch { }
    return $result
}

function Format-Percent { param([object]$Value) if ($null -eq $Value) { return '--' } return ('{0:0.0}%' -f [double]$Value) }
function Format-Memory { param([object]$Process) if ($null -eq $Process) { return '--' } try { $Process.Refresh(); return ('{0:0} MB' -f ($Process.WorkingSet64 / 1MB)) } catch { return '--' } }
function Get-ProcessMemoryPercent {
    param([object]$Process, [object]$TotalBytes)
    if (($null -eq $Process) -or ($null -eq $TotalBytes) -or ([double]$TotalBytes -le 0)) { return $null }
    try { $Process.Refresh(); return ($Process.WorkingSet64 / [double]$TotalBytes) * 100 } catch { return $null }
}
function Set-ProgressValue {
    param([System.Windows.Forms.ProgressBar]$Bar, [object]$Value)
    if ($null -eq $Value) { $Bar.Value = 0; return }
    $number = [int][Math]::Round([double]$Value)
    $Bar.Value = [Math]::Max($Bar.Minimum, [Math]::Min($Bar.Maximum, $number))
}

function Set-StatusVisual {
    param([System.Windows.Forms.Label]$StatusLabel, [System.Windows.Forms.Label]$Dot, [bool]$Running)
    if ($Running) {
        $StatusLabel.Text = $script:ui.running
        $StatusLabel.ForeColor = $script:colors.green
        $Dot.ForeColor = $script:colors.green
    } else {
        $StatusLabel.Text = $script:ui.stopped
        $StatusLabel.ForeColor = $script:colors.muted
        $Dot.ForeColor = $script:colors.border
    }
}

function New-UiLabel {
    param([string]$Text, [int]$X, [int]$Y, [int]$Width = 0, [int]$Height = 24, [int]$Size = 9, [System.Drawing.FontStyle]$Style = 'Regular', [object]$Color = $null)
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    if ($Width -gt 0) { $label.Size = New-Object System.Drawing.Size($Width, $Height) } else { $label.AutoSize = $true }
    $label.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', $Size, $Style)
    $label.ForeColor = if ($null -eq $Color) { $script:colors.text } else { [System.Drawing.Color]$Color }
    return $label
}

function New-UiButton {
    param([string]$Text, [int]$X, [int]$Y, [int]$Width = 112, [int]$Height = 34, [object]$Back = $null)
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.Location = New-Object System.Drawing.Point($X, $Y)
    $button.Size = New-Object System.Drawing.Size($Width, $Height)
    $button.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
    $button.FlatStyle = 'Flat'
    $button.FlatAppearance.BorderSize = 0
    $button.BackColor = if ($null -eq $Back) { $script:colors.card } else { [System.Drawing.Color]$Back }
    $button.ForeColor = if ($null -eq $Back) { $script:colors.text } else { [System.Drawing.Color]::White }
    $button.Cursor = [System.Windows.Forms.Cursors]::Hand
    return $button
}

function Update-TunnelModeUI {
    $mode = Get-TunnelMode
    $isNamed = $mode -eq 'named'
    $isToken = $mode -eq 'token'
    $script:tunnelName.Visible = $isNamed
    $script:tunnelNameLabel.Visible = $isNamed
    $script:tunnelToken.Visible = $isToken
    $script:tunnelTokenLabel.Visible = $isToken
    if ($isToken) { $script:tunnelHint.Text = $script:ui.tokenHint }
    elseif ($isNamed) { $script:tunnelHint.Text = $script:ui.namedHint }
    else { $script:tunnelHint.Text = $script:ui.quickHint }
}

function Stop-AllAndExit {
    if ($script:isClosing) { return }
    $script:isClosing = $true
    Stop-Tunnel
    Stop-Center
    Stop-MatchingCenterProcesses
    Write-ConsoleLog 'All center and Tunnel processes were stopped.'
    $form.Close()
}

$form = New-Object System.Windows.Forms.Form
$form.Text = ($script:ui.appTitle + ' - ' + $script:ui.consoleTitle)
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(980, 760)
$form.MinimumSize = New-Object System.Drawing.Size(900, 680)
$form.BackColor = $script:colors.page
$form.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)

$header = New-Object System.Windows.Forms.Panel
$header.Dock = 'Top'
$header.Height = 86
$header.BackColor = $script:colors.navy
$form.Controls.Add($header)
$header.Controls.Add((New-UiLabel ($script:ui.appTitle + '  /  ' + $script:ui.consoleTitle) 26 14 0 32 20 'Bold' ([System.Drawing.Color]::White)))
$header.Controls.Add((New-UiLabel 'CENTER HOST CONTROL DESK' 28 51 0 20 8 'Regular' ([System.Drawing.Color]::FromArgb(148, 163, 184))))

$centerCard = New-Object System.Windows.Forms.Panel
$centerCard.Location = New-Object System.Drawing.Point(24, 106)
$centerCard.Size = New-Object System.Drawing.Size(452, 112)
$centerCard.BackColor = $script:colors.card
$form.Controls.Add($centerCard)
$centerCard.Controls.Add((New-UiLabel $script:ui.center 18 14 0 24 11 'Bold'))
$centerDot = New-UiLabel ([string][char]0x25CF) 18 45 24 24 13 'Bold' $script:colors.border
$centerCard.Controls.Add($centerDot)
$centerStatus = New-UiLabel $script:ui.stopped 44 45 0 24 11 'Bold' $script:colors.muted
$centerCard.Controls.Add($centerStatus)
$startCenter = New-UiButton $script:ui.startCenter 188 39 104 34 $script:colors.blue
$startCenter.Add_Click({ Start-Center })
$centerCard.Controls.Add($startCenter)
$stopCenter = New-UiButton $script:ui.stopCenter 300 39 104 34
$stopCenter.Add_Click({ Stop-Center })
$centerCard.Controls.Add($stopCenter)

$tunnelCard = New-Object System.Windows.Forms.Panel
$tunnelCard.Location = New-Object System.Drawing.Point(496, 106)
$tunnelCard.Size = New-Object System.Drawing.Size(444, 112)
$tunnelCard.BackColor = $script:colors.card
$form.Controls.Add($tunnelCard)
$tunnelCard.Controls.Add((New-UiLabel $script:ui.tunnel 18 14 0 24 11 'Bold'))
$tunnelDot = New-UiLabel ([string][char]0x25CF) 18 45 24 24 13 'Bold' $script:colors.border
$tunnelCard.Controls.Add($tunnelDot)
$tunnelStatus = New-UiLabel $script:ui.stopped 44 45 0 24 11 'Bold' $script:colors.muted
$tunnelCard.Controls.Add($tunnelStatus)
$openWeb = New-UiButton $script:ui.openWeb 156 39 92 34
$openWeb.Add_Click({ Open-CenterWeb })
$tunnelCard.Controls.Add($openWeb)
$startTunnel = New-UiButton $script:ui.startTunnel 254 39 92 34 $script:colors.green
$startTunnel.Add_Click({ Start-Tunnel })
$tunnelCard.Controls.Add($startTunnel)
$stopTunnel = New-UiButton $script:ui.stopTunnel 352 39 92 34
$stopTunnel.Add_Click({ Stop-Tunnel })
$tunnelCard.Controls.Add($stopTunnel)

$performanceCard = New-Object System.Windows.Forms.Panel
$performanceCard.Location = New-Object System.Drawing.Point(24, 234)
$performanceCard.Size = New-Object System.Drawing.Size(916, 110)
$performanceCard.BackColor = $script:colors.navy
$form.Controls.Add($performanceCard)
$performanceCard.Controls.Add((New-UiLabel $script:ui.performance 18 12 0 22 11 'Bold' ([System.Drawing.Color]::White)))
$metricX = @(18, 200, 382, 564, 746)
$metricTitles = @($script:ui.centerCpu, $script:ui.centerMemory, $script:ui.hostCpu, $script:ui.hostMemory, $script:ui.diskFree)
$script:metricValues = @()
$script:metricBars = @()
for ($i = 0; $i -lt $metricX.Count; $i++) {
    $performanceCard.Controls.Add((New-UiLabel $metricTitles[$i] $metricX[$i] 43 150 18 8 'Regular' ([System.Drawing.Color]::FromArgb(148, 163, 184))))
    $value = New-UiLabel '--' $metricX[$i] 62 150 30 10 'Bold' ([System.Drawing.Color]::White)
    $performanceCard.Controls.Add($value)
    $script:metricValues += $value
    $bar = New-Object System.Windows.Forms.ProgressBar
    $bar.Location = New-Object System.Drawing.Point($metricX[$i], 94)
    $bar.Size = New-Object System.Drawing.Size(150, 8)
    $bar.Minimum = 0
    $bar.Maximum = 100
    $bar.Style = 'Continuous'
    $performanceCard.Controls.Add($bar)
    $script:metricBars += $bar
}

$configCard = New-Object System.Windows.Forms.Panel
$configCard.Location = New-Object System.Drawing.Point(24, 360)
$configCard.Size = New-Object System.Drawing.Size(916, 170)
$configCard.BackColor = $script:colors.card
$form.Controls.Add($configCard)
$configCard.Controls.Add((New-UiLabel $script:ui.tunnelConfig 18 12 0 24 11 'Bold'))
$configCard.Controls.Add((New-UiLabel $script:ui.mode 18 49 80 24 9 'Regular' $script:colors.muted))
$script:tunnelMode = New-Object System.Windows.Forms.ComboBox
$script:tunnelMode.DropDownStyle = 'DropDownList'
$script:tunnelMode.Items.AddRange(@($script:ui.quickMode, $script:ui.namedMode, $script:ui.tokenMode))
$script:tunnelMode.SelectedIndex = 0
$script:tunnelMode.Location = New-Object System.Drawing.Point(94, 46)
$script:tunnelMode.Size = New-Object System.Drawing.Size(190, 28)
$script:tunnelMode.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
$script:tunnelMode.Add_SelectedIndexChanged({ Update-TunnelModeUI })
$configCard.Controls.Add($script:tunnelMode)
$script:tunnelNameLabel = New-UiLabel $script:ui.tunnelName 318 49 105 24 9 'Regular' $script:colors.muted
$configCard.Controls.Add($script:tunnelNameLabel)
$script:tunnelName = New-Object System.Windows.Forms.TextBox
$script:tunnelName.Location = New-Object System.Drawing.Point(424, 46)
$script:tunnelName.Size = New-Object System.Drawing.Size(210, 28)
$script:tunnelName.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$configCard.Controls.Add($script:tunnelName)
$script:tunnelTokenLabel = New-UiLabel $script:ui.token 318 49 105 24 9 'Regular' $script:colors.muted
$script:tunnelTokenLabel.Visible = $false
$configCard.Controls.Add($script:tunnelTokenLabel)
$script:tunnelToken = New-Object System.Windows.Forms.TextBox
$script:tunnelToken.Location = New-Object System.Drawing.Point(424, 46)
$script:tunnelToken.Size = New-Object System.Drawing.Size(450, 28)
$script:tunnelToken.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$script:tunnelToken.PasswordChar = [char]0x25CF
$script:tunnelToken.Visible = $false
$configCard.Controls.Add($script:tunnelToken)
$configCard.Controls.Add((New-UiLabel $script:ui.executable 18 91 100 24 9 'Regular' $script:colors.muted))
$script:tunnelPath = New-Object System.Windows.Forms.TextBox
$script:tunnelPath.Text = 'cloudflared'
$script:tunnelPath.Location = New-Object System.Drawing.Point(118, 88)
$script:tunnelPath.Size = New-Object System.Drawing.Size(684, 28)
$script:tunnelPath.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$configCard.Controls.Add($script:tunnelPath)
$browse = New-UiButton $script:ui.browse 814 86 76 30
$browse.Add_Click({
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Filter = 'cloudflared.exe|cloudflared.exe|Executable files|*.exe|All files|*.*'
    if ($dialog.ShowDialog() -eq 'OK') { $script:tunnelPath.Text = $dialog.FileName }
})
$configCard.Controls.Add($browse)
$script:tunnelHint = New-UiLabel $script:ui.quickHint 18 130 870 24 9 'Regular' $script:colors.muted
$configCard.Controls.Add($script:tunnelHint)

$startAll = New-UiButton $script:ui.startAll 24 548 148 38 $script:colors.blue
$startAll.Add_Click({ Start-Center; $form.BeginInvoke([Action]{ Start-Tunnel }) | Out-Null })
$form.Controls.Add($startAll)
$stopAll = New-UiButton $script:ui.stopExit 184 548 180 38 $script:colors.red
$stopAll.Add_Click({ Stop-AllAndExit })
$form.Controls.Add($stopAll)
$form.Controls.Add((New-UiLabel $script:ui.logs 24 600 0 22 11 'Bold'))
$script:logBox = New-Object System.Windows.Forms.TextBox
$script:logBox.Multiline = $true
$script:logBox.ReadOnly = $true
$script:logBox.ScrollBars = 'Vertical'
$script:logBox.BackColor = $script:colors.log
$script:logBox.ForeColor = $script:colors.logText
$script:logBox.Font = New-Object System.Drawing.Font('Consolas', 9)
$script:logBox.Location = New-Object System.Drawing.Point(24, 626)
$script:logBox.Size = New-Object System.Drawing.Size(916, 84)
$script:logBox.Anchor = 'Top,Bottom,Left,Right'
$form.Controls.Add($script:logBox)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1500
$timer.Add_Tick({
    $centerRunning = $script:centerProcess -and -not $script:centerProcess.HasExited
    $tunnelRunning = $script:tunnelProcess -and -not $script:tunnelProcess.HasExited
    Set-StatusVisual $centerStatus $centerDot $centerRunning
    Set-StatusVisual $tunnelStatus $tunnelDot $tunnelRunning
    $startCenter.Enabled = -not $centerRunning
    $stopCenter.Enabled = $centerRunning
    $startTunnel.Enabled = -not $tunnelRunning
    $stopTunnel.Enabled = $tunnelRunning
    $stopAll.Enabled = $centerRunning -or $tunnelRunning
    $centerCpu = Get-ProcessCpuPercent $script:centerProcess
    $hostPerf = Get-HostPerformance
    $centerMemoryPct = Get-ProcessMemoryPercent $script:centerProcess $hostPerf.MemoryTotal
    $script:metricValues[0].Text = Format-Percent $centerCpu
    $script:metricValues[1].Text = Format-Memory $script:centerProcess
    $script:metricValues[2].Text = Format-Percent $hostPerf.Cpu
    $script:metricValues[3].Text = Format-Percent $hostPerf.Memory
    $script:metricValues[4].Text = if ($null -eq $hostPerf.Disk) { '--' } else { ('{0:0.0}%' -f [double]$hostPerf.Disk) }
    Set-ProgressValue $script:metricBars[0] $centerCpu
    Set-ProgressValue $script:metricBars[1] $centerMemoryPct
    Set-ProgressValue $script:metricBars[2] $hostPerf.Cpu
    Set-ProgressValue $script:metricBars[3] $hostPerf.Memory
    Set-ProgressValue $script:metricBars[4] $hostPerf.Disk
})
$timer.Start()

$form.Add_FormClosed({
    if (-not $script:isClosing) {
        $script:isClosing = $true
        Stop-Tunnel
        Stop-Center
        Stop-MatchingCenterProcesses
    }
    $timer.Stop()
})

Write-ConsoleLog 'Center console started.'
[System.Windows.Forms.Application]::Run($form)
