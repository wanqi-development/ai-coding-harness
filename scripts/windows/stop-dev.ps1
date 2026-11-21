[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$statePath = Join-Path $repoRoot "logs\launcher-state.json"
$taskkillExe = Join-Path $env:SystemRoot "System32\taskkill.exe"
$tempRoot = (Resolve-Path -LiteralPath $env:TEMP).Path.TrimEnd("\")

if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Host "No running launcher state was found."
    exit 0
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$failed = $false
foreach ($entry in @(
    @{
        name = "frontend"
        pid = $state.frontend_pid
        started_at = $state.frontend_started_at
    },
    @{
        name = "backend"
        pid = $state.backend_pid
        started_at = $state.backend_started_at
    }
)) {
    $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        continue
    }
    if (-not $entry.started_at) {
        Write-Host "Skipping $($entry.name) PID $($entry.pid): state has no start time."
        $failed = $true
        continue
    }
    $expectedStart = [datetime]::Parse($entry.started_at).ToUniversalTime()
    $actualStart = $process.StartTime.ToUniversalTime()
    if ([math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 2) {
        Write-Host "Skipping $($entry.name) PID $($entry.pid): PID was reused."
        continue
    }
    $graceful = $false
    if ($entry.name -eq "backend" -and $state.shutdown_file) {
        try {
            [IO.File]::WriteAllText(
                [string]$state.shutdown_file,
                "stop",
                (New-Object System.Text.UTF8Encoding($false))
            )
            Write-Host "Waiting for $($entry.name) to save active work ..."
            for ($attempt = 0; $attempt -lt 150; $attempt++) {
                $process.Refresh()
                if ($process.HasExited) {
                    $graceful = $true
                    break
                }
                Start-Sleep -Milliseconds 200
            }
        }
        catch {
            Write-Host "Graceful shutdown request failed: $($_.Exception.Message)"
        }
    }
    if (-not $graceful) {
        Write-Host "Stopping $($entry.name) process tree (PID $($entry.pid)) ..."
        & $taskkillExe /PID $entry.pid /T /F *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Failed to stop $($entry.name) PID $($entry.pid)."
            $failed = $true
        }
    }
    else {
        Write-Host "$($entry.name) stopped gracefully."
    }
}

if (-not $failed) {
    if ($state.service_log_dir) {
        $serviceLogDir = [string]$state.service_log_dir
        if (Test-Path -LiteralPath $serviceLogDir) {
            $resolvedServiceLogDir = (Resolve-Path -LiteralPath $serviceLogDir).Path
            $stableServiceLogDir = Join-Path $repoRoot "logs\services"
            $isStableHarnessLogDir = [string]::Equals(
                $resolvedServiceLogDir,
                $stableServiceLogDir,
                [StringComparison]::OrdinalIgnoreCase
            )
            $isHarnessTempDir = (
                $resolvedServiceLogDir.StartsWith(
                    $tempRoot + [IO.Path]::DirectorySeparatorChar,
                    [StringComparison]::OrdinalIgnoreCase
                ) -and
                (Split-Path -Leaf $resolvedServiceLogDir).StartsWith(
                    "ai-coding-harness-",
                    [StringComparison]::Ordinal
                )
            )
            if ($isStableHarnessLogDir) {
                Write-Host "Preserving service logs: $resolvedServiceLogDir"
            }
            elseif ($isHarnessTempDir) {
                Remove-Item -LiteralPath $resolvedServiceLogDir -Recurse -Force
            }
            else {
                Write-Host "Skipping unsafe service log path: $resolvedServiceLogDir"
                $failed = $true
            }
        }
    }
    if (-not $failed) {
        Remove-Item -LiteralPath $statePath -Force
        Write-Host "Launcher services stopped."
        exit 0
    }
}
exit 1
