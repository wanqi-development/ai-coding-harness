[CmdletBinding()]
param(
    [string]$Distro = $env:WSL_DISTRO,
    [string]$HarnessUser = $(if ($env:WSL_USER) { $env:WSL_USER } else { "harness" }),
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$repoRootForWsl = $repoRoot.Replace("\", "/")

function Invoke-WslChecked {
    param(
        [string[]]$Arguments,
        [switch]$CaptureOutput,
        [switch]$AllowFailure
    )
    $stderrFile = [IO.Path]::GetTempFileName()
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        if ($CaptureOutput) {
            $output = @(& wsl.exe @Arguments 2> $stderrFile)
        }
        else {
            & wsl.exe @Arguments 2> $stderrFile | Out-Host
            $output = @()
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    $stderrText = if (Test-Path -LiteralPath $stderrFile) {
        Get-Content -LiteralPath $stderrFile -Raw
    }
    else {
        $null
    }
    $stderr = if ($null -eq $stderrText) { "" } else { $stderrText.Trim() }
    Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
    if ($exitCode -ne 0 -and $stderr) {
        Write-Host $stderr
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "wsl.exe failed with exit code $exitCode"
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
        Error = $stderr
    }
}

function Get-WslDistros {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "WSL is not installed."
    }
    $result = Invoke-WslChecked -Arguments @("-l", "-q") -CaptureOutput -AllowFailure
    $items = @(
        $result.Output |
            ForEach-Object { ($_ -replace "`0", "").Trim() } |
            Where-Object { $_ }
    )
    if ($result.ExitCode -ne 0) {
        throw "WSL is unavailable."
    }
    return $items
}

$distros = Get-WslDistros
if (-not $Distro) {
    $Distro = @("Ubuntu-24.04", "Ubuntu-22.04", "Ubuntu") |
        Where-Object { $distros -contains $_ } |
        Select-Object -First 1
    if (-not $Distro) {
        $Distro = $distros[0]
    }
}
if ($distros -notcontains $Distro) {
    throw "WSL distribution '$Distro' was not found."
}

$pathResult = Invoke-WslChecked -CaptureOutput -Arguments @(
    "-d", $Distro, "-u", "root", "--",
    "wslpath", "-a", "-u", $repoRootForWsl
)
$wslRepo = ($pathResult.Output -join "`n").Trim()
if (-not $wslRepo) {
    throw "Cannot resolve repository path inside WSL."
}

Write-Host "[1/2] Stop WSL backend on port $BackendPort ..."
Invoke-WslChecked -Arguments @(
    "-d", $Distro, "-u", $HarnessUser, "--",
    "env", "PORT=$BackendPort",
    "bash", "$wslRepo/scripts/wsl/stop-standalone.sh"
) | Out-Null

Write-Host "[2/2] Stop Windows frontend on port $FrontendPort ..."
$connections = @(
    Get-NetTCPConnection -State Listen -LocalPort $FrontendPort -ErrorAction SilentlyContinue
)
$frontendPids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
$stopped = 0
foreach ($pidValue in $frontendPids) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
    if (-not $process) {
        continue
    }
    $commandLine = [string]$process.CommandLine
    if ($commandLine -match "vite" -and $commandLine -match [regex]::Escape($repoRoot)) {
        Write-Host "Stopping standalone frontend: pid=$pidValue"
        & taskkill.exe /PID $pidValue /T /F 2>$null | Out-Null
        $stopped++
    }
    else {
        Write-Warning "Port $FrontendPort is not this repository's Vite process; skipped: pid=$pidValue"
    }
}
if ($stopped -eq 0) {
    Write-Host "No standalone frontend process was found."
}

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $backendOpen = Get-NetTCPConnection -State Listen -LocalPort $BackendPort -ErrorAction SilentlyContinue
    $frontendOpen = Get-NetTCPConnection -State Listen -LocalPort $FrontendPort -ErrorAction SilentlyContinue
    if (-not $backendOpen -and -not $frontendOpen) {
        Write-Host "Standalone services are fully stopped."
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

throw "Ports are still in use: backend=$([bool]$backendOpen) frontend=$([bool]$frontendOpen)"
