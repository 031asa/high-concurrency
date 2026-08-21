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

function Get-NtpSamples([string[]]$Servers, [int]$SamplesPerServer = 3) {
    $samples = New-Object System.Collections.Generic.List[double]
    $usedServers = New-Object System.Collections.Generic.List[string]
    $errors = New-Object System.Collections.Generic.List[string]
    foreach ($server in $Servers) {
        $raw = & w32tm.exe /stripchart "/computer:$server" /dataonly "/samples:$SamplesPerServer" 2>&1
        if ($LASTEXITCODE -ne 0) { $errors.Add("$server returned no usable NTP data"); continue }
        $before = $samples.Count
        foreach ($line in $raw) {
            if ($line -match '(?<sign>[+-])(?<seconds>\d+(?:[.,]\d+)?)s\s*$') {
                $milliseconds = [double]::Parse($Matches.seconds.Replace(",", "."), $Invariant) * 1000.0
                if ($Matches.sign -eq "-") { $milliseconds = -$milliseconds }
                # W32Time phase/NTP offset is the correction from the local
                # clock toward the target: authority-minus-local.
                $samples.Add($milliseconds)
            }
        }
        if ($samples.Count -gt $before) { $usedServers.Add($server) } else { $errors.Add("$server offsets could not be parsed") }
    }
    $average = 0.0
    $maximum = 0.0
    if ($samples.Count -gt 0) {
        $average = [double](($samples | Measure-Object -Average).Average)
        $maximum = [double](($samples | ForEach-Object { [math]::Abs($_) } | Measure-Object -Maximum).Maximum)
    }
    return [pscustomobject]@{ Values = @($samples); Average = $average; Maximum = $maximum; Servers = @($usedServers); Errors = @($errors) }
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
        uncertainty_ms = 0.0; max_offset_ms = $settings.MaxOffsetMs; max_cross_difference_ms = $settings.MaxCrossDifferenceMs
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
