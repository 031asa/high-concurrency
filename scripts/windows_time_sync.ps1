[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Config,
    [switch]$Apply,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Output
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Invariant = [Globalization.CultureInfo]::InvariantCulture

if (-not ("YdTrader.PreciseClock" -as [type])) {
    Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
namespace YdTrader {
    public static class PreciseClock {
        [DllImport("kernel32.dll")]
        private static extern void GetSystemTimePreciseAsFileTime(out long fileTime);

        public static double UnixMilliseconds() {
            long fileTime;
            GetSystemTimePreciseAsFileTime(out fileTime);
            return (fileTime - 116444736000000000L) / 10000.0;
        }
    }
}
"@
}

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

function Set-NtpTimestamp([byte[]]$Packet, [int]$Offset, [double]$UnixMilliseconds) {
    $ntp = ($UnixMilliseconds / 1000.0) + 2208988800.0
    [uint32]$whole = [math]::Floor($ntp)
    [uint32]$fraction = [math]::Floor(($ntp - [math]::Floor($ntp)) * 4294967296.0)
    for ($index = 0; $index -lt 4; $index++) {
        $shift = 24 - (8 * $index)
        $Packet[$Offset + $index] = [byte](($whole -shr $shift) -band 0xff)
        $Packet[$Offset + 4 + $index] = [byte](($fraction -shr $shift) -band 0xff)
    }
}

function Get-NtpTimestamp([byte[]]$Packet, [int]$Offset) {
    $whole = ([double]$Packet[$Offset] * 16777216.0) +
        ([double]$Packet[$Offset + 1] * 65536.0) +
        ([double]$Packet[$Offset + 2] * 256.0) +
        [double]$Packet[$Offset + 3]
    $fraction = ([double]$Packet[$Offset + 4] * 16777216.0) +
        ([double]$Packet[$Offset + 5] * 65536.0) +
        ([double]$Packet[$Offset + 6] * 256.0) +
        [double]$Packet[$Offset + 7]
    return (($whole - 2208988800.0) + ($fraction / 4294967296.0)) * 1000.0
}

function Get-Median([double[]]$Values) {
    if ($Values.Count -eq 0) { return 0.0 }
    [double[]]$ordered = @($Values | Sort-Object)
    $middle = [int][math]::Floor($ordered.Count / 2)
    if (($ordered.Count % 2) -eq 1) { return [double]$ordered[$middle] }
    return ([double]$ordered[$middle - 1] + [double]$ordered[$middle]) / 2.0
}

function Get-NtpSample([string]$Server) {
    $packet = New-Object byte[] 48
    $packet[0] = 0x1b # Leap=0, NTPv3, client mode.
    $client = New-Object Net.Sockets.UdpClient
    try {
        $client.Client.ReceiveTimeout = 3000
        $client.Connect($Server, 123)
        $t1 = [YdTrader.PreciseClock]::UnixMilliseconds()
        Set-NtpTimestamp $packet 40 $t1
        [void]$client.Send($packet, $packet.Length)
        $remote = New-Object Net.IPEndPoint([Net.IPAddress]::Any, 0)
        [byte[]]$response = $client.Receive([ref]$remote)
        $t4 = [YdTrader.PreciseClock]::UnixMilliseconds()
    }
    finally { $client.Close() }
    if ($response.Length -lt 48) { throw "$Server returned a short NTP packet." }
    $leap = ($response[0] -shr 6) -band 0x3
    $mode = $response[0] -band 0x7
    $stratum = [int]$response[1]
    if ($leap -eq 3 -or $mode -notin @(4, 5) -or $stratum -lt 1 -or $stratum -gt 15) {
        throw "$Server returned an unsynchronized or invalid NTP response."
    }
    $echoedT1 = Get-NtpTimestamp $response 24
    if ([math]::Abs($echoedT1 - $t1) -gt 2.0) { throw "$Server returned an NTP response for a different request." }
    $t2 = Get-NtpTimestamp $response 32
    $t3 = Get-NtpTimestamp $response 40
    if ($t2 -eq 0 -or $t3 -eq 0) { throw "$Server returned an empty NTP timestamp." }
    return [pscustomobject]@{
        OffsetMs = (($t2 - $t1) + ($t3 - $t4)) / 2.0
        RoundTripMs = [math]::Max(0.0, ($t4 - $t1) - ($t3 - $t2))
    }
}

function Get-NtpSamples([string[]]$Servers, [int]$SamplesPerServer = 11) {
    $samples = New-Object System.Collections.Generic.List[object]
    $usedServers = New-Object System.Collections.Generic.List[string]
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($server in $Servers) {
        $before = $samples.Count
        for ($sampleIndex = 0; $sampleIndex -lt $SamplesPerServer; $sampleIndex++) {
            try {
                $sample = Get-NtpSample $server
                $samples.Add($sample)
            }
            catch { $errors.Add("$server sample failed: $($_.Exception.Message)") }
            if ($sampleIndex -lt ($SamplesPerServer - 1)) { Start-Sleep -Milliseconds 100 }
        }
        if ($samples.Count -gt $before) { $usedServers.Add($server) }
    }
    $median = 0.0
    $mean = 0.0
    $maximum = 0.0
    $uncertainty = 0.0
    $medianRoundTrip = 0.0
    $filtered = @()
    if ($samples.Count -gt 0) {
        # Large RTT samples are the ones most exposed to queueing and asymmetric VPN paths.
        # Keep the fastest 75%, then use the median offset instead of a fragile arithmetic mean.
        $keepCount = [math]::Max(1, [int][math]::Ceiling($samples.Count * 0.75))
        $filtered = @($samples | Sort-Object RoundTripMs | Select-Object -First $keepCount)
        [double[]]$offsets = @($filtered | ForEach-Object { [double]$_.OffsetMs })
        [double[]]$roundTrips = @($filtered | ForEach-Object { [double]$_.RoundTripMs })
        $median = Get-Median $offsets
        $mean = [double](($offsets | Measure-Object -Average).Average)
        $maximum = [double](($offsets | ForEach-Object { [math]::Abs($_) } | Measure-Object -Maximum).Maximum)
        $medianRoundTrip = Get-Median $roundTrips
        [double[]]$absoluteDeviations = @($offsets | ForEach-Object { [math]::Abs($_ - $median) })
        $uncertainty = ($medianRoundTrip / 2.0) + (Get-Median $absoluteDeviations)
    }
    return [pscustomobject]@{
        Values = $samples.ToArray(); FilteredValues = @($filtered); Average = $median; Median = $median; Mean = $mean
        Maximum = $maximum; Uncertainty = $uncertainty; MedianRoundTrip = $medianRoundTrip
        DiscardedCount = ($samples.Count - $filtered.Count); Servers = $usedServers.ToArray(); Errors = $errors.ToArray()
    }
}

function Invoke-TimeConfiguration($Settings, $InitialSamples) {
    if (-not (Test-IsAdministrator)) { throw "-Apply requires an Administrator PowerShell." }
    if ($InitialSamples.Values.Count -eq 0) { throw "No valid NTP samples; refusing to change Windows time configuration." }
    $peers = (($Settings.Servers | ForEach-Object { "$_,0x8" }) -join " ")
    Set-Service -Name W32Time -StartupType Automatic
    Start-Service -Name W32Time
    & w32tm.exe /config "/manualpeerlist:$peers" /syncfromflags:manual /reliable:no /update
    if ($LASTEXITCODE -ne 0) { throw "w32tm peer configuration failed with exit code $LASTEXITCODE." }
    $registry = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config"
    $ntpClientRegistry = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient"
    $originalStepLimit = [int](Get-ItemProperty -LiteralPath $registry).MaxAllowedPhaseOffset
    $needsStep = $InitialSamples.Maximum -gt $Settings.MaxOffsetMs
    # Microsoft high-accuracy profile: poll every 64 seconds and discipline the
    # clock more aggressively. Keep 0x8 client mode; Min/MaxPollInterval govern it.
    Set-ItemProperty -LiteralPath $registry -Name MinPollInterval -Value 6
    Set-ItemProperty -LiteralPath $registry -Name MaxPollInterval -Value 6
    Set-ItemProperty -LiteralPath $registry -Name UpdateInterval -Value 100
    Set-ItemProperty -LiteralPath $registry -Name FrequencyCorrectRate -Value 2
    Set-ItemProperty -LiteralPath $ntpClientRegistry -Name SpecialPollInterval -Value 64
    if ($needsStep) {
        Write-Output ("Offset exceeds {0} ms; requesting one immediate correction." -f $Settings.MaxOffsetMs)
        Set-ItemProperty -LiteralPath $registry -Name MaxAllowedPhaseOffset -Value 0
    }
    try {
        Restart-Service -Name W32Time
        $ok = $false
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            & w32tm.exe /resync /rediscover
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
            Start-Sleep -Seconds 3
        }
        if (-not $ok) { throw "w32tm resync failed after three attempts." }
        # One forced sample plus one normal 64-second poll gives W32Time time to
        # start estimating frequency before the post-apply report is generated.
        Write-Output "W32Time high-accuracy profile applied; waiting 70 seconds for stabilization..."
        Start-Sleep -Seconds 70
    }
    finally {
        if ($needsStep) {
            Set-ItemProperty -LiteralPath $registry -Name MaxAllowedPhaseOffset -Value $originalStepLimit
            & w32tm.exe /config /update | Out-Null
        }
    }
}

function Get-W32TimeProfile {
    $registry = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config"
    $ntpClientRegistry = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient"
    $config = Get-ItemProperty -LiteralPath $registry
    $ntpClient = Get-ItemProperty -LiteralPath $ntpClientRegistry
    $service = Get-Service -Name W32Time
    return [pscustomobject]@{
        MinPollInterval = [int]$config.MinPollInterval
        MaxPollInterval = [int]$config.MaxPollInterval
        UpdateInterval = [int]$config.UpdateInterval
        FrequencyCorrectRate = [int]$config.FrequencyCorrectRate
        SpecialPollInterval = [int]$ntpClient.SpecialPollInterval
        ServiceStartType = [string]$service.StartType
    }
}

function Write-Utf8Json($Value, [string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "Output directory does not exist: $parent" }
    if (Test-Path -LiteralPath $full -PathType Leaf) {
        if ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing symbolic-link output: $full" }
    }
    [IO.File]::WriteAllText($full, ($Value | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
}

try {
    $settings = Read-TimeAuthorityConfig $Config
    $initial = Get-NtpSamples $settings.Servers
    if ($Apply) { Invoke-TimeConfiguration $settings $initial }
    $samples = if ($Apply) { Get-NtpSamples $settings.Servers } else { $initial }
    $w32timeProfile = Get-W32TimeProfile
    $sourceRaw = & w32tm.exe /query /source 2>&1
    $activeSource = if ($LASTEXITCODE -eq 0) { (($sourceRaw | Out-String).Trim()) } else { "UNAVAILABLE" }
    $resolved = New-Object System.Collections.Generic.List[string]
    foreach ($server in $settings.Servers) {
        try { [Net.Dns]::GetHostAddresses($server) | ForEach-Object { if (-not $resolved.Contains($_.IPAddressToString)) { $resolved.Add($_.IPAddressToString) } } } catch { }
    }
    $failures = New-Object System.Collections.Generic.List[string]
    if ($samples.Values.Count -lt 7) { $failures.Add("fewer than 7 valid NTP samples") }
    if ($samples.Maximum -gt $settings.MaxOffsetMs) { $failures.Add("Windows/authority offset exceeds $($settings.MaxOffsetMs) ms") }
    if ($activeSource -match 'Local CMOS Clock|本地\s*CMOS\s*时钟') { $failures.Add("W32Time is using the local CMOS clock") }
    $sourceName = $activeSource -replace ',0x[0-9A-Fa-f]+$', ''
    $activeAllowed = ($settings.Servers -contains $sourceName) -or ($resolved -contains $sourceName)
    if (-not $activeAllowed) { $failures.Add("W32Time active source is not the configured authority") }
    $profileMatches = $w32timeProfile.MinPollInterval -eq 6 -and
        $w32timeProfile.MaxPollInterval -eq 6 -and
        $w32timeProfile.UpdateInterval -eq 100 -and
        $w32timeProfile.FrequencyCorrectRate -eq 2 -and
        $w32timeProfile.SpecialPollInterval -eq 64 -and
        $w32timeProfile.ServiceStartType -eq "Automatic"
    if (-not $profileMatches) { $failures.Add("W32Time high-accuracy profile is not applied; rerun with -Apply") }
    $report = [ordered]@{
        schema = 1; platform = "windows"; hostname = $env:COMPUTERNAME
        generated_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", $Invariant)
        authority = [ordered]@{ name = $settings.Name; url = $settings.Url; ntp_servers = @($settings.Servers); environment = $settings.Environment }
        selected_source = $activeSource; resolved_ips = @($resolved)
        authority_minus_local_ms = [math]::Round($samples.Median, 3); mean_offset_ms = [math]::Round($samples.Mean, 3)
        max_abs_sample_ms = [math]::Round($samples.Maximum, 3); uncertainty_ms = [math]::Round($samples.Uncertainty, 3)
        median_round_trip_ms = [math]::Round($samples.MedianRoundTrip, 3)
        max_offset_ms = $settings.MaxOffsetMs; max_cross_difference_ms = $settings.MaxCrossDifferenceMs
        sample_count = $samples.Values.Count; filtered_sample_count = $samples.FilteredValues.Count
        discarded_high_rtt_samples = $samples.DiscardedCount; estimator = "median_of_fastest_75_percent"
        sample_errors = @($samples.Errors)
        w32time_profile = [ordered]@{
            min_poll_interval = $w32timeProfile.MinPollInterval; max_poll_interval = $w32timeProfile.MaxPollInterval
            update_interval = $w32timeProfile.UpdateInterval; frequency_correct_rate = $w32timeProfile.FrequencyCorrectRate
            special_poll_interval = $w32timeProfile.SpecialPollInterval; service_start_type = $w32timeProfile.ServiceStartType
        }
        pass = ($failures.Count -eq 0); failure = ($failures -join "; ")
    }
    Write-Utf8Json $report $Output
    Write-Output "=== Windows time authority report ==="
    Write-Output "Authority : $($settings.Name)"
    Write-Output "Website   : $($settings.Url)"
    Write-Output "NTP       : $($settings.Servers -join ', ')"
    Write-Output "Active    : $activeSource"
    Write-Output ("Median    : {0:+0.000;-0.000;0.000} ms (authority minus Windows)" -f $samples.Median)
    Write-Output ("Mean      : {0:+0.000;-0.000;0.000} ms (filtered samples)" -f $samples.Mean)
    Write-Output ("RTT       : {0:0.000} ms median; kept {1}/{2} samples" -f $samples.MedianRoundTrip, $samples.FilteredValues.Count, $samples.Values.Count)
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
