[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$Distro = "Ubuntu-24.04",
    [ValidateRange(1, 10000)]
    [int]$MaxCrossClockOffsetMs = 50,
    [ValidateRange(1, 10000)]
    [int]$MaxChronyOffsetMs = 20,
    [ValidateRange(3, 31)]
    [int]$Samples = 7,
    [switch]$ResyncWindows,
    [string]$WindowsPeers = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$InvariantCulture = [System.Globalization.CultureInfo]::InvariantCulture

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-W32TimeResync {
    param([string]$Peers)

    if (-not (Test-IsAdministrator)) {
        throw "Windows time resync/configuration requires an Administrator PowerShell."
    }
    Set-Service -Name W32Time -StartupType Automatic
    Start-Service -Name W32Time
    if (-not [string]::IsNullOrWhiteSpace($Peers)) {
        & w32tm.exe /config "/manualpeerlist:$Peers" /syncfromflags:manual /reliable:no /update
        if ($LASTEXITCODE -ne 0) {
            throw "w32tm peer configuration failed with exit code $LASTEXITCODE."
        }
        Restart-Service -Name W32Time
    }
    & w32tm.exe /resync /force
    if ($LASTEXITCODE -ne 0) {
        throw "w32tm resync failed with exit code $LASTEXITCODE."
    }
}

function Invoke-WslText {
    param([string[]]$LinuxArguments)

    $output = & wsl.exe -d $Distro --exec @LinuxArguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed in distro '$Distro': $($LinuxArguments -join ' ')"
    }
    return (($output | Out-String).Trim())
}

function Get-ChronyStatus {
    $chronydArguments = Invoke-WslText -LinuxArguments @("ps", "-C", "chronyd", "-o", "args=")
    $tracking = Invoke-WslText -LinuxArguments @("chronyc", "-c", "tracking")
    $fields = $tracking.Split(",")
    if ($fields.Count -lt 14) {
        throw "Unexpected chronyc tracking CSV output: $tracking"
    }
    $sources = Invoke-WslText -LinuxArguments @("chronyc", "-c", "sources", "-n")
    $selectedSource = "NONE"
    $selectedMode = "NONE"
    foreach ($line in ($sources -split "`r?`n")) {
        $sourceFields = $line.Split(",")
        if ($sourceFields.Count -ge 3 -and $sourceFields[1] -eq "*") {
            $selectedSource = $sourceFields[2]
            $selectedMode = $sourceFields[0]
            break
        }
    }
    return [pscustomobject]@{
        ReferenceId = $fields[1]
        Stratum = [int]$fields[2]
        SystemTimeMs = [double]::Parse($fields[4], $InvariantCulture) * 1000.0
        LastOffsetMs = [double]::Parse($fields[5], $InvariantCulture) * 1000.0
        RootDispersionMs = [double]::Parse($fields[11], $InvariantCulture) * 1000.0
        LeapStatus = $fields[13].Trim()
        SelectedSource = $selectedSource
        SelectedMode = $selectedMode
        ControlsSystemClock = -not ($chronydArguments -match "(^|\s)-[A-Za-z0-9]*x(\s|$)")
        DaemonArguments = $chronydArguments
    }
}

function Get-BestCrossClockSample {
    $results = New-Object System.Collections.Generic.List[object]
    for ($index = 0; $index -lt $Samples; $index++) {
        $windowsBeforeMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        $rawLinuxTime = Invoke-WslText -LinuxArguments @("date", "+%s%3N")
        $windowsAfterMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        if ($rawLinuxTime -notmatch "^\d{13}$") {
            throw "Unexpected Linux epoch milliseconds: $rawLinuxTime"
        }
        $linuxMs = [long]$rawLinuxTime
        $roundTripMs = $windowsAfterMs - $windowsBeforeMs
        $results.Add([pscustomobject]@{
            EstimatedOffsetMs = $linuxMs - (($windowsBeforeMs + $windowsAfterMs) / 2.0)
            UncertaintyMs = $roundTripMs / 2.0
            MinimumPossibleOffsetMs = $linuxMs - $windowsAfterMs
            MaximumPossibleOffsetMs = $linuxMs - $windowsBeforeMs
            RoundTripMs = $roundTripMs
        })
    }
    return $results | Sort-Object RoundTripMs | Select-Object -First 1
}

try {
    if ($ResyncWindows -or -not [string]::IsNullOrWhiteSpace($WindowsPeers)) {
        Invoke-W32TimeResync -Peers $WindowsPeers
    }
    $w32Parameters = Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters"
    $windowsSourceOutput = & w32tm.exe /query /source 2>&1
    if ($LASTEXITCODE -eq 0) {
        $windowsSource = (($windowsSourceOutput | Out-String).Trim())
    }
    else {
        $windowsSource = "UNAVAILABLE (run as Administrator to query the active source)"
    }
    $chrony = Get-ChronyStatus
    $crossClock = Get-BestCrossClockSample

    Write-Output "=== Windows time ==="
    Write-Output "Service          : W32Time"
    Write-Output "Mode             : $($w32Parameters.Type)"
    Write-Output "Configured peers : $($w32Parameters.NtpServer)"
    Write-Output "Active source    : $windowsSource"
    Write-Output ""
    Write-Output "=== Linux chrony ==="
    Write-Output "Reference        : $($chrony.ReferenceId)"
    Write-Output "Selected source  : $($chrony.SelectedSource)"
    Write-Output "Source mode      : $($chrony.SelectedMode) (# means local refclock)"
    Write-Output "Controls clock   : $($chrony.ControlsSystemClock)"
    Write-Output "Daemon arguments : $($chrony.DaemonArguments)"
    Write-Output ("System time      : {0:+0.000;-0.000;0.000} ms" -f $chrony.SystemTimeMs)
    Write-Output ("Last offset      : {0:+0.000;-0.000;0.000} ms" -f $chrony.LastOffsetMs)
    Write-Output ("Root dispersion  : {0:0.000} ms" -f $chrony.RootDispersionMs)
    Write-Output "Leap status      : $($chrony.LeapStatus)"
    Write-Output ""
    Write-Output "=== Windows <-> WSL ==="
    Write-Output ("Estimated offset : {0:+0.000;-0.000;0.000} ms (Linux minus Windows)" -f $crossClock.EstimatedOffsetMs)
    Write-Output ("Measurement +/-  : {0:0.000} ms" -f $crossClock.UncertaintyMs)
    Write-Output ("Best round trip  : {0} ms from {1} samples" -f $crossClock.RoundTripMs, $Samples)

    $failures = New-Object System.Collections.Generic.List[string]
    if ($chrony.LeapStatus -ne "Normal") {
        $failures.Add("chrony leap status is '$($chrony.LeapStatus)', not Normal")
    }
    if ([math]::Abs($chrony.SystemTimeMs) -gt $MaxChronyOffsetMs) {
        $failures.Add("chrony system-time offset exceeds $MaxChronyOffsetMs ms")
    }
    if ([math]::Abs($chrony.LastOffsetMs) -gt $MaxChronyOffsetMs) {
        $failures.Add("chrony last offset exceeds $MaxChronyOffsetMs ms")
    }
    if ($chrony.SelectedSource -eq "NONE") {
        $failures.Add("chrony has no selected source")
    }
    if (-not $chrony.ControlsSystemClock) {
        $failures.Add("chronyd is running with -x and cannot adjust the Linux clock")
    }
    if ($chrony.SelectedMode -ne "#" -or $chrony.SelectedSource -ne "PHC0") {
        $failures.Add("chrony is not following the Windows Hyper-V PHC0 host clock")
    }
    if ($crossClock.MinimumPossibleOffsetMs -gt $MaxCrossClockOffsetMs -or
        $crossClock.MaximumPossibleOffsetMs -lt -$MaxCrossClockOffsetMs) {
        $failures.Add("Windows/WSL offset exceeds $MaxCrossClockOffsetMs ms after measurement uncertainty")
    }

    if ($failures.Count -gt 0) {
        Write-Output ""
        Write-Output "RESULT: FAIL"
        foreach ($failure in $failures) {
            Write-Output "- $failure"
        }
        exit 2
    }
    Write-Output ""
    Write-Output "RESULT: PASS"
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
