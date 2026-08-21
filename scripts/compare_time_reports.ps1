[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WindowsReport,
    [Parameter(Mandatory = $true)][string]$LinuxReport,
    [ValidateRange(1, 3600)][int]$MaxSampleSeparationSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Invariant = [Globalization.CultureInfo]::InvariantCulture

function Read-Report([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Report not found: $Path" }
    return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}
function Canonical-List($Items) { return (@($Items | ForEach-Object { [string]$_ } | Sort-Object) -join "`n") }

try {
    $windows = Read-Report $WindowsReport
    $linux = Read-Report $LinuxReport
    $failures = New-Object System.Collections.Generic.List[string]
    if ($windows.schema -ne 1 -or $linux.schema -ne 1) { $failures.Add("unsupported report schema") }
    if ($windows.platform -ne "windows" -or $linux.platform -ne "linux") { $failures.Add("wrong report platform") }
    if ($windows.authority.name -cne $linux.authority.name) { $failures.Add("authority name mismatch") }
    if ($windows.authority.url -cne $linux.authority.url) { $failures.Add("authority website mismatch") }
    if ((Canonical-List $windows.authority.ntp_servers) -cne (Canonical-List $linux.authority.ntp_servers)) { $failures.Add("NTP server list mismatch") }
    if ($windows.authority.environment -cne $linux.authority.environment) { $failures.Add("environment mismatch") }
    if (-not [bool]$windows.pass) { $failures.Add("Windows report failed: $($windows.failure)") }
    if (-not [bool]$linux.pass) { $failures.Add("Linux report failed: $($linux.failure)") }
    $windowsTime = [DateTimeOffset]::Parse([string]$windows.generated_at_utc, $Invariant, [Globalization.DateTimeStyles]::AssumeUniversal)
    $linuxTime = [DateTimeOffset]::Parse([string]$linux.generated_at_utc, $Invariant, [Globalization.DateTimeStyles]::AssumeUniversal)
    $separation = [math]::Abs(($windowsTime - $linuxTime).TotalSeconds)
    if ($separation -gt $MaxSampleSeparationSeconds) { $failures.Add("report sample times differ by more than $MaxSampleSeparationSeconds seconds") }
    if ([int]$windows.max_cross_difference_ms -ne [int]$linux.max_cross_difference_ms) { $failures.Add("cross-difference threshold mismatch") }
    $limit = [math]::Min([double]$windows.max_cross_difference_ms, [double]$linux.max_cross_difference_ms)
    $difference = [math]::Abs([double]$windows.authority_minus_local_ms - [double]$linux.authority_minus_local_ms)
    if ($difference -gt $limit) { $failures.Add("Windows/Linux difference exceeds $limit ms") }
    Write-Output "=== Windows / Linux time report comparison ==="
    Write-Output "Authority       : $($windows.authority.name)"
    Write-Output "Website         : $($windows.authority.url)"
    Write-Output "NTP servers     : $($windows.authority.ntp_servers -join ', ')"
    Write-Output ("Windows offset  : {0:+0.000;-0.000;0.000} ms (authority minus Windows)" -f [double]$windows.authority_minus_local_ms)
    Write-Output ("Linux offset    : {0:+0.000;-0.000;0.000} ms (authority minus Linux)" -f [double]$linux.authority_minus_local_ms)
    Write-Output ("Final difference: {0:0.000} ms; limit {1:0.000} ms" -f $difference, $limit)
    Write-Output ("Sample gap      : {0:0.000} seconds" -f $separation)
    if ($failures.Count -gt 0) {
        Write-Output "RESULT: FAIL"
        $failures | ForEach-Object { Write-Output "- $_" }
        exit 2
    }
    Write-Output "RESULT: PASS"
    exit 0
}
catch { Write-Error $_; exit 1 }
