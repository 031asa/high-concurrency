[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Config,
    [switch]$Apply,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Output,
    [string]$NtpRoot = "",
    [ValidateRange(30, 600)][int]$StabilizationTimeoutSeconds = 150
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Invariant = [Globalization.CultureInfo]::InvariantCulture
$ManagedMarker = "# Managed by ydtrader-time-sync"
$RequiredInternalVersion = "4.2.8p18a-o"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Read-TimeAuthorityConfig([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Config not found: $Path" }
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines((Resolve-Path -LiteralPath $Path))) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) { continue }
        $parts = $trimmed.Split(@("="), 2, [StringSplitOptions]::None)
        if ($parts.Count -ne 2) { throw "Invalid config line: $line" }
        $values[$parts[0].Trim()] = $parts[1].Trim()
    }
    foreach ($key in @("authority_name", "authority_url", "ntp_servers", "environment", "max_offset_ms", "max_cross_difference_ms")) {
        if (-not $values.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($values[$key])) { throw "Missing config value: $key" }
    }
    if ($values.authority_url -notmatch '^https?://') { throw "authority_url must be HTTP(S)." }
    if ($values.environment -notin @("test", "production")) { throw "environment must be test or production." }
    if ($values.ntp_servers -match 'REPLACE_|PLACEHOLDER') { throw "Refusing placeholder NTP configuration." }
    $servers = @($values.ntp_servers -split '\s+' | Where-Object { $_ })
    foreach ($server in $servers) {
        if ($server -notmatch '^[A-Za-z0-9._:-]+$') { throw "Invalid NTP server: $server" }
    }
    $maxOffset = 0
    $maxCross = 0
    if (-not [int]::TryParse($values.max_offset_ms, [ref]$maxOffset) -or $maxOffset -le 0) { throw "Invalid max_offset_ms." }
    if (-not [int]::TryParse($values.max_cross_difference_ms, [ref]$maxCross) -or $maxCross -le 0) { throw "Invalid max_cross_difference_ms." }
    return [pscustomobject]@{
        Name = $values.authority_name; Url = $values.authority_url; Servers = $servers
        Environment = $values.environment; MaxOffsetMs = $maxOffset; MaxCrossDifferenceMs = $maxCross
    }
}

function Get-NtpInstallation([string]$RequestedRoot) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) { $candidates.Add($RequestedRoot) }
    try {
        $imagePath = [string](Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\NTP" -Name ImagePath).ImagePath
        $match = [regex]::Match($imagePath, '(?i)(?:"(?<exe>[^"]*\\ntpd\.exe)"|(?<exe>[^\s]*\\ntpd\.exe))')
        if ($match.Success) { $candidates.Add((Split-Path -Parent (Split-Path -Parent $match.Groups['exe'].Value))) }
    }
    catch { }
    foreach ($candidate in @("D:\NTP", "${env:ProgramFiles(x86)}\NTP", "$env:ProgramFiles\NTP")) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) { $candidates.Add($candidate) }
    }
    foreach ($candidate in $candidates) {
        try { $root = [IO.Path]::GetFullPath($candidate).TrimEnd('\') } catch { continue }
        $ntpd = Join-Path $root "bin\ntpd.exe"
        $ntpq = Join-Path $root "bin\ntpq.exe"
        if ((Test-Path -LiteralPath $ntpd -PathType Leaf) -and (Test-Path -LiteralPath $ntpq -PathType Leaf)) {
            return [pscustomobject]@{ Root = $root; Ntpd = $ntpd; Ntpq = $ntpq; Config = (Join-Path $root "etc\ntp.conf") }
        }
    }
    throw "Meinberg NTP was not found. Install MeinbergGlobal.NTP 4.2.8p18a2 (recommended root D:\NTP), or pass -NtpRoot."
}

function Get-NtpdVersion($Installation) {
    $raw = (& $Installation.Ntpd --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) { throw "Unable to read ntpd version from $($Installation.Ntpd)." }
    if ($raw -notmatch [regex]::Escape($RequiredInternalVersion)) {
        throw "Unsupported ntpd build. Required internal version $RequiredInternalVersion; got: $raw"
    }
    return $raw
}

function Resolve-AuthorityAddresses([string[]]$Servers) {
    $resolved = New-Object System.Collections.Generic.List[string]
    foreach ($server in $Servers) {
        if (-not $resolved.Contains($server)) { $resolved.Add($server) }
        try {
            [Net.Dns]::GetHostAddresses($server) | ForEach-Object {
                if (-not $resolved.Contains($_.IPAddressToString)) { $resolved.Add($_.IPAddressToString) }
            }
        }
        catch { }
    }
    return $resolved.ToArray()
}

function Get-ReachSampleCount([string]$Reach) {
    try { $value = [Convert]::ToInt32($Reach, 8) } catch { return 0 }
    $count = 0
    while ($value -gt 0) { $count += ($value -band 1); $value = $value -shr 1 }
    return $count
}

function Get-RvValue([string]$Text, [string]$Name) {
    $escaped = [regex]::Escape($Name)
    $pattern = '(?ms)(?:^|,\s*){0}=("[^"]*"|[^,\r\n]+)' -f $escaped
    $match = [regex]::Match($Text, $pattern)
    if (-not $match.Success) { return $null }
    return $match.Groups[1].Value.Trim().Trim('"').Trim('[', ']')
}

function Convert-InvariantDouble([string]$Value, [string]$Label) {
    $number = 0.0
    if ([string]::IsNullOrWhiteSpace($Value) -or -not [double]::TryParse($Value, [Globalization.NumberStyles]::Float, $Invariant, [ref]$number)) {
        throw "ntpq did not return a valid $Label."
    }
    return $number
}

function Get-NtpStatus($Installation, [string[]]$AllowedSources) {
    $peersText = (& $Installation.Ntpq -pn 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "ntpq -pn failed: $($peersText.Trim())" }
    $rvText = (& $Installation.Ntpq -c "rv 0 version,processor,system,offset,sys_jitter,frequency,rootdisp,refid,stratum,leap" 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "ntpq system query failed: $($rvText.Trim())" }
    $selected = $null
    foreach ($line in ($peersText -split "`r?`n")) {
        if ($line -match '^\*(?<remote>\S+)\s+(?<refid>\S+)\s+(?<stratum>\d+)\s+(?<mode>\S+)\s+(?<when>\S+)\s+(?<poll>\d+)\s+(?<reach>[0-7]+)\s+(?<delay>[+-]?\d+(?:\.\d+)?)\s+(?<offset>[+-]?\d+(?:\.\d+)?)\s+(?<jitter>[+-]?\d+(?:\.\d+)?)') {
            $selected = [pscustomobject]@{
                Remote = $Matches.remote.Trim('[', ']'); RefId = $Matches.refid.Trim('[', ']')
                Stratum = [int]$Matches.stratum; Mode = $Matches.mode; When = $Matches.when
                Poll = [int]$Matches.poll; Reach = $Matches.reach; SampleCount = (Get-ReachSampleCount $Matches.reach)
                DelayMs = (Convert-InvariantDouble $Matches.delay "peer delay")
                OffsetMs = (Convert-InvariantDouble $Matches.offset "peer offset")
                JitterMs = (Convert-InvariantDouble $Matches.jitter "peer jitter")
            }
            break
        }
    }
    $service = Get-Service -Name NTP -ErrorAction Stop
    $w32time = Get-Service -Name W32Time -ErrorAction Stop
    $systemOffset = Convert-InvariantDouble (Get-RvValue $rvText "offset") "system offset"
    $systemJitter = Convert-InvariantDouble (Get-RvValue $rvText "sys_jitter") "system jitter"
    $rootDispersion = Convert-InvariantDouble (Get-RvValue $rvText "rootdisp") "root dispersion"
    $stratumRaw = Get-RvValue $rvText "stratum"
    $stratum = 0
    if (-not [int]::TryParse($stratumRaw, [ref]$stratum)) { throw "ntpq did not return a valid stratum." }
    $allowed = $false
    if ($null -ne $selected) { $allowed = $AllowedSources -contains $selected.Remote }
    return [pscustomobject]@{
        PeersText = $peersText.Trim(); RvText = $rvText.Trim(); Selected = $selected
        SystemOffsetMs = $systemOffset; SystemJitterMs = $systemJitter
        FrequencyPpm = (Convert-InvariantDouble (Get-RvValue $rvText "frequency") "frequency")
        RootDispersionMs = $rootDispersion; RefId = (Get-RvValue $rvText "refid"); Stratum = $stratum
        Leap = (Get-RvValue $rvText "leap"); Version = (Get-RvValue $rvText "version")
        ServiceStatus = [string]$service.Status; ServiceStartType = [string]$service.StartType
        W32TimeStatus = [string]$w32time.Status; W32TimeStartType = [string]$w32time.StartType
        SelectedIsAllowed = $allowed
    }
}

function New-ManagedNtpConfig($Settings, $Installation) {
    $drift = (Join-Path $Installation.Root "etc\ntp.drift").Replace('\', '/')
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($ManagedMarker)
    $lines.Add("# Authority: $($Settings.Name)")
    $lines.Add("# Website: $($Settings.Url)")
    $lines.Add("driftfile `"$drift`"")
    $lines.Add("restrict default kod nomodify notrap nopeer noquery limited")
    $lines.Add("restrict -6 default kod nomodify notrap nopeer noquery limited")
    $lines.Add("restrict 127.0.0.1")
    $lines.Add("restrict ::1")
    foreach ($server in $Settings.Servers) { $lines.Add("server $server iburst minpoll 6 maxpoll 6") }
    return ($lines -join "`r`n") + "`r`n"
}

function Restore-ServiceStartType([string]$Name, [string]$StartType) {
    if ($StartType -eq "Automatic") { Set-Service -Name $Name -StartupType Automatic }
    elseif ($StartType -eq "Manual") { Set-Service -Name $Name -StartupType Manual }
    elseif ($StartType -eq "Disabled") { Set-Service -Name $Name -StartupType Disabled }
}

function Invoke-TimeConfiguration($Settings, $Installation, [string[]]$AllowedSources) {
    if (-not (Test-IsAdministrator)) { throw "-Apply requires an Administrator PowerShell." }
    $computer = Get-CimInstance Win32_ComputerSystem
    if ([bool]$computer.PartOfDomain) {
        throw "This computer is domain-joined. Refusing to disable W32Time because Active Directory may depend on it. Ask the administrator for a domain time design."
    }
    $configDirectory = Split-Path -Parent $Installation.Config
    if (-not (Test-Path -LiteralPath $configDirectory -PathType Container)) { New-Item -ItemType Directory -Path $configDirectory | Out-Null }
    $hadConfig = Test-Path -LiteralPath $Installation.Config -PathType Leaf
    $originalConfig = if ($hadConfig) { [IO.File]::ReadAllText($Installation.Config) } else { $null }
    $backup = "$($Installation.Config).ydtrader-original"
    if ($hadConfig -and -not $originalConfig.StartsWith($ManagedMarker) -and -not (Test-Path -LiteralPath $backup)) {
        [IO.File]::WriteAllText($backup, $originalConfig, (New-Object Text.UTF8Encoding($false)))
    }
    $w32timeBefore = Get-Service -Name W32Time
    $ntpBefore = Get-Service -Name NTP -ErrorAction Stop
    try {
        if ($ntpBefore.Status -ne "Stopped") { Stop-Service -Name NTP -Force }
        [IO.File]::WriteAllText($Installation.Config, (New-ManagedNtpConfig $Settings $Installation), (New-Object Text.UTF8Encoding($false)))
        if ($w32timeBefore.Status -ne "Stopped") { Stop-Service -Name W32Time -Force }
        Set-Service -Name W32Time -StartupType Disabled
        Set-Service -Name NTP -StartupType Automatic
        Start-Service -Name NTP
        $deadline = [DateTime]::UtcNow.AddSeconds($StabilizationTimeoutSeconds)
        $ready = $null
        do {
            Start-Sleep -Seconds 5
            try {
                $candidate = Get-NtpStatus $Installation $AllowedSources
                if ($null -ne $candidate.Selected -and $candidate.SelectedIsAllowed -and $candidate.Selected.SampleCount -ge 3 -and $candidate.Stratum -ge 1 -and $candidate.Stratum -le 15) {
                    $ready = $candidate
                    break
                }
            }
            catch { }
            Write-Output "Waiting for Meinberg ntpd to select the configured authority..."
        } while ([DateTime]::UtcNow -lt $deadline)
        if ($null -eq $ready) { throw "Meinberg ntpd did not reach a stable configured peer within $StabilizationTimeoutSeconds seconds." }
    }
    catch {
        Stop-Service -Name NTP -Force -ErrorAction SilentlyContinue
        if ($hadConfig) { [IO.File]::WriteAllText($Installation.Config, $originalConfig, (New-Object Text.UTF8Encoding($false))) }
        elseif (Test-Path -LiteralPath $Installation.Config) { Remove-Item -LiteralPath $Installation.Config -Force }
        Restore-ServiceStartType "W32Time" ([string]$w32timeBefore.StartType)
        if ($w32timeBefore.Status -eq "Running") { Start-Service -Name W32Time }
        throw
    }
}

function Write-Utf8Json($Value, [string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "Output directory does not exist: $parent" }
    if (Test-Path -LiteralPath $full -PathType Leaf) {
        if ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing symbolic-link output: $full" }
    }
    [IO.File]::WriteAllText($full, ($Value | ConvertTo-Json -Depth 7), (New-Object Text.UTF8Encoding($false)))
}

try {
    $settings = Read-TimeAuthorityConfig $Config
    $installation = Get-NtpInstallation $NtpRoot
    $versionText = Get-NtpdVersion $installation
    $allowedSources = @(Resolve-AuthorityAddresses $settings.Servers)
    if ($Apply) { Invoke-TimeConfiguration $settings $installation $allowedSources }
    $status = Get-NtpStatus $installation $allowedSources
    $failures = New-Object System.Collections.Generic.List[string]
    if ($status.ServiceStatus -ne "Running" -or $status.ServiceStartType -ne "Automatic") { $failures.Add("Meinberg NTP service is not running with Automatic startup") }
    if ($status.W32TimeStatus -ne "Stopped" -or $status.W32TimeStartType -ne "Disabled") { $failures.Add("W32Time must be stopped and disabled while Meinberg ntpd is active; rerun with -Apply") }
    if ($null -eq $status.Selected) { $failures.Add("ntpd has no selected peer (*)") }
    elseif (-not $status.SelectedIsAllowed) { $failures.Add("ntpd selected peer is not the configured authority") }
    elseif ($status.Selected.SampleCount -lt 3) { $failures.Add("ntpd selected peer has fewer than 3 successful reach samples") }
    if ($status.Stratum -lt 1 -or $status.Stratum -gt 15) { $failures.Add("ntpd is not synchronized") }
    if ([math]::Abs($status.SystemOffsetMs) -gt $settings.MaxOffsetMs) { $failures.Add("Windows/authority offset exceeds $($settings.MaxOffsetMs) ms") }
    $selectedName = if ($null -eq $status.Selected) { "UNAVAILABLE" } else { $status.Selected.Remote }
    $sampleCount = if ($null -eq $status.Selected) { 0 } else { $status.Selected.SampleCount }
    $report = [ordered]@{
        schema = 1; platform = "windows"; hostname = $env:COMPUTERNAME
        generated_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", $Invariant)
        authority = [ordered]@{ name = $settings.Name; url = $settings.Url; ntp_servers = @($settings.Servers); environment = $settings.Environment }
        selected_source = $selectedName; resolved_ips = @($allowedSources)
        authority_minus_local_ms = [math]::Round($status.SystemOffsetMs, 3)
        max_abs_sample_ms = [math]::Round([math]::Abs($status.SystemOffsetMs), 3)
        uncertainty_ms = [math]::Round($status.RootDispersionMs, 3)
        max_offset_ms = $settings.MaxOffsetMs; max_cross_difference_ms = $settings.MaxCrossDifferenceMs
        sample_count = $sampleCount; estimator = "meinberg_ntpd_system_peer"
        ntpd = [ordered]@{
            package_version = "4.2.8p18a2"; internal_version = $versionText; root = $installation.Root
            service_status = $status.ServiceStatus; service_start_type = $status.ServiceStartType
            w32time_status = $status.W32TimeStatus; w32time_start_type = $status.W32TimeStartType
            stratum = $status.Stratum; refid = $status.RefId
            system_offset_ms = [math]::Round($status.SystemOffsetMs, 6)
            system_jitter_ms = [math]::Round($status.SystemJitterMs, 6)
            frequency_ppm = [math]::Round($status.FrequencyPpm, 6)
            root_dispersion_ms = [math]::Round($status.RootDispersionMs, 6)
            selected_peer = if ($null -eq $status.Selected) { $null } else { [ordered]@{
                remote = $status.Selected.Remote; refid = $status.Selected.RefId; poll_seconds = $status.Selected.Poll
                reach = $status.Selected.Reach; delay_ms = $status.Selected.DelayMs
                offset_ms = $status.Selected.OffsetMs; jitter_ms = $status.Selected.JitterMs
            } }
        }
        pass = ($failures.Count -eq 0); failure = ($failures -join "; ")
    }
    Write-Utf8Json $report $Output
    Write-Output "=== Windows Meinberg ntpd report ==="
    Write-Output "Authority : $($settings.Name)"
    Write-Output "Website   : $($settings.Url)"
    Write-Output "NTP       : $($settings.Servers -join ', ')"
    Write-Output "Selected  : $selectedName"
    Write-Output ("Offset    : {0:+0.000;-0.000;0.000} ms (authority minus Windows)" -f $status.SystemOffsetMs)
    Write-Output ("Jitter    : {0:0.000} ms; root dispersion {1:0.000} ms" -f $status.SystemJitterMs, $status.RootDispersionMs)
    Write-Output "Report    : $Output"
    if ($failures.Count -gt 0) {
        Write-Output "RESULT: FAIL"
        $failures | ForEach-Object { Write-Output "- $_" }
        exit 2
    }
    Write-Output "RESULT: PASS"
    exit 0
}
catch { Write-Error $_; exit 1 }
