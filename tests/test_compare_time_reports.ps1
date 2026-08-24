$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$comparator = Join-Path $root "scripts\compare_time_reports.ps1"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("ydtrader-time-report-tests-" + [guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($testRoot) | Out-Null

function New-Report([string]$Platform, [double]$Offset) {
    return [ordered]@{
        schema = 1; platform = $Platform; hostname = $Platform
        generated_at_utc = "2026-08-21T06:00:00.000Z"
        authority = [ordered]@{
            name = "Cloudflare Time Services"; url = "https://www.cloudflare.com/time/"
            ntp_servers = @("time.cloudflare.com"); environment = "test"
        }
        authority_minus_local_ms = $Offset; max_abs_sample_ms = [math]::Abs($Offset)
        max_offset_ms = 50; max_cross_difference_ms = 50; pass = $true; failure = ""
        sample_errors = @("中文网络超时样本")
    }
}

function Invoke-Case([string]$Name, [scriptblock]$Mutation, [int]$Expected) {
    $windows = New-Report "windows" 5
    $linux = New-Report "linux" 7
    & $Mutation $windows $linux
    $windowsPath = Join-Path $testRoot "$Name-windows.json"
    $linuxPath = Join-Path $testRoot "$Name-linux.json"
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($windowsPath, ($windows | ConvertTo-Json -Depth 5), $utf8)
    [IO.File]::WriteAllText($linuxPath, ($linux | ConvertTo-Json -Depth 5), $utf8)
    $result = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $comparator -WindowsReport $windowsPath -LinuxReport $linuxPath 2>&1
    if ($LASTEXITCODE -ne $Expected) { throw "$Name expected $Expected, got $LASTEXITCODE`n$($result -join "`n")" }
    Write-Output "PASS: $Name"
}

try {
    Invoke-Case "valid" {} 0
    Invoke-Case "authority-mismatch" { param($w, $l) $l.authority.name = "different" } 2
    Invoke-Case "website-mismatch" { param($w, $l) $l.authority.url = "https://example.invalid/" } 2
    Invoke-Case "stale" { param($w, $l) $l.generated_at_utc = "2026-08-21T06:02:00.000Z" } 2
    Invoke-Case "missing-sample" { param($w, $l) $l.pass = $false; $l.failure = "no valid NTP sample" } 2
    Invoke-Case "cross-offset" { param($w, $l) $l.authority_minus_local_ms = 100 } 2
    Invoke-Case "ntp-mismatch" { param($w, $l) $l.authority.ntp_servers = @("wrong.example") } 2
}
finally {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolved.StartsWith($tempRoot) -and (Split-Path -Leaf $resolved).StartsWith("ydtrader-time-report-tests-")) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
