[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Config,
    [switch]$Apply,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Output
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Invariant = [Globalization.CultureInfo]::InvariantCulture

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

function Get-NtpSample([string]$Server) {
    $packet = New-Object byte[] 48
    $packet[0] = 0x1b # Leap=0, NTPv3, client mode.
    $client = New-Object Net.Sockets.UdpClient
    try {
        $client.Client.ReceiveTimeout = 3000
        $client.Connect($Server, 123)
        $t1 = [double][DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        Set-NtpTimestamp $packet 40 $t1
        [void]$client.Send($packet, $packet.Length)
        $remote = New-Object Net.IPEndPoint([Net.IPAddress]::Any, 0)
        [byte[]]$response = $client.Receive([ref]$remote)
        $t4 = [double][DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
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

function Get-NtpSamples([string[]]$Servers, [int]$SamplesPerServer = 3) {
    $samples = New-Object System.Collections.Generic.List[double]
    $delays = New-Object System.Collections.Generic.List[double]
    $usedServers = New-Object System.Collections.Generic.List[string]
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($server in $Servers) {
        $before = $samples.Count
        for ($sampleIndex = 0; $sampleIndex -lt $SamplesPerServer; $sampleIndex++) {
            try {
                $sample = Get-NtpSample $server
                $samples.Add([double]$sample.OffsetMs)
                $delays.Add([double]$sample.RoundTripMs)
            }
            catch { $errors.Add("$server sample failed: $($_.Exception.Message)") }
        }
        if ($samples.Count -gt $before) { $usedServers.Add($server) }
    }
    $average = 0.0
    $maximum = 0.0
    $uncertainty = 0.0
    if ($samples.Count -gt 0) {
        $average = [double](($samples | Measure-Object -Average).Average)
        $maximum = [double](($samples | ForEach-Object { [math]::Abs($_) } | Measure-Object -Maximum).Maximum)
        $uncertainty = [double](($delays | Measure-Object -Average).Average) / 2.0
    }
    return [pscustomobject]@{ Values = @($samples); Average = $average; Maximum = $maximum; Uncertainty = $uncertainty; Servers = @($usedServers); Errors = @($errors) }
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
    $originalStepLimit = [int](Get-ItemProperty -LiteralPath $registry).MaxAllowedPhaseOffset
    $needsStep = $InitialSamples.Maximum -gt $Settings.MaxOffsetMs
    Set-ItemProperty -LiteralPath $registry -Name UpdateInterval -Value 100
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
        Start-Sleep -Seconds 5
    }
    finally {
        if ($needsStep) {
            Set-ItemProperty -LiteralPath $registry -Name MaxAllowedPhaseOffset -Value $originalStepLimit
            & w32tm.exe /config /update | Out-Null
        }
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
    $sourceRaw = & w32tm.exe /query /source 2>&1
    $activeSource = if ($LASTEXITCODE -eq 0) { (($sourceRaw | Out-String).Trim()) } else { "UNAVAILABLE" }
    $resolved = New-Object System.Collections.Generic.List[string]
    foreach ($server in $settings.Servers) {
        try { [Net.Dns]::GetHostAddresses($server) | ForEach-Object { if (-not $resolved.Contains($_.IPAddressToString)) { $resolved.Add($_.IPAddressToString) } } } catch { }
    }
    $failures = New-Object System.Collections.Generic.List[string]
    if ($samples.Values.Count -eq 0) { $failures.Add("no valid NTP sample") }
    if ($samples.Maximum -gt $settings.MaxOffsetMs) { $failures.Add("Windows/authority offset exceeds $($settings.MaxOffsetMs) ms") }
    if ($activeSource -match 'Local CMOS Clock|本地\s*CMOS\s*时钟') { $failures.Add("W32Time is using the local CMOS clock") }
    $sourceName = $activeSource -replace ',0x[0-9A-Fa-f]+$', ''
    $activeAllowed = ($settings.Servers -contains $sourceName) -or ($resolved -contains $sourceName)
    if (-not $activeAllowed) { $failures.Add("W32Time active source is not the configured authority") }
    $report = [ordered]@{
        schema = 1; platform = "windows"; hostname = $env:COMPUTERNAME
        generated_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", $Invariant)
        authority = [ordered]@{ name = $settings.Name; url = $settings.Url; ntp_servers = @($settings.Servers); environment = $settings.Environment }
        selected_source = $activeSource; resolved_ips = @($resolved)
        authority_minus_local_ms = [math]::Round($samples.Average, 3); max_abs_sample_ms = [math]::Round($samples.Maximum, 3)
        uncertainty_ms = [math]::Round($samples.Uncertainty, 3); max_offset_ms = $settings.MaxOffsetMs; max_cross_difference_ms = $settings.MaxCrossDifferenceMs
        sample_count = $samples.Values.Count; pass = ($failures.Count -eq 0); failure = ($failures -join "; ")
    }
    Write-Utf8Json $report $Output
    Write-Output "=== Windows time authority report ==="
    Write-Output "Authority : $($settings.Name)"
    Write-Output "Website   : $($settings.Url)"
    Write-Output "NTP       : $($settings.Servers -join ', ')"
    Write-Output "Active    : $activeSource"
    Write-Output ("Offset    : {0:+0.000;-0.000;0.000} ms (authority minus Windows)" -f $samples.Average)
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
