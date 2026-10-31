[CmdletBinding()]
param(
    [switch]$CheckEnv
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$commonBat = Join-Path $PSScriptRoot "_common.bat"
$backendBat = Join-Path $PSScriptRoot "start-backend.bat"
$frontendBat = Join-Path $PSScriptRoot "start-frontend.bat"
$cmdExe = Join-Path $env:SystemRoot "System32\cmd.exe"
$taskkillExe = Join-Path $env:SystemRoot "System32\taskkill.exe"
$logDir = Join-Path $repoRoot "logs"
$launcherLog = Join-Path $logDir "launcher.log"
$statePath = Join-Path $logDir "launcher-state.json"
$serviceLogDir = Join-Path $env:TEMP (
    "ai-coding-harness-" + [guid]::NewGuid().ToString("N")
)
$launcherEncoding = New-Object System.Text.UTF8Encoding($false)
$backend = $null
$frontend = $null

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path $serviceLogDir -Force | Out-Null
[IO.File]::WriteAllText($launcherLog, "", $launcherEncoding)

function Write-LauncherLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")][string]$Level = "INFO"
    )
    $line = "{0:yyyy-MM-dd HH:mm:ss.fff} [{1}] {2}" -f (Get-Date), $Level, $Message
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            [IO.File]::AppendAllText(
                $launcherLog,
                $line + [Environment]::NewLine,
                $launcherEncoding
            )
            break
        }
        catch {
            if ($attempt -eq 10) {
                Write-Host $line
                return
            }
            Start-Sleep -Milliseconds 50
        }
    }
    Write-Host $line
}

function Write-CapturedOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet("INFO", "WARN", "ERROR")][string]$DefaultLevel = "INFO"
    )
    Get-Content -LiteralPath $Path | ForEach-Object {
        $level = if ($_ -match "^\[ERROR\]") {
            "ERROR"
        }
        elseif ($_ -match "^\[WARN\]") {
            "WARN"
        }
        else {
            $DefaultLevel
        }
        Write-LauncherLog -Level $level -Message $_
    }
}

function Write-ConsoleLogTail {
    param([int]$Lines = 80)
    if (Test-Path -LiteralPath $launcherLog) {
        Write-Host ""
        Write-Host "----- launcher.log tail -----"
        Get-Content -LiteralPath $launcherLog -Tail $Lines | Write-Host
        Write-Host "----- end launcher.log -----"
    }
}

function Merge-ServiceLogTails {
    foreach ($entry in @(
        @{ label = "backend"; path = Join-Path $serviceLogDir "backend.log" },
        @{ label = "frontend"; path = Join-Path $serviceLogDir "frontend.log" }
    )) {
        if (-not (Test-Path -LiteralPath $entry.path)) {
            continue
        }
        Write-LauncherLog -Level WARN -Message "[$($entry.label)] recent output follows"
        Get-Content -LiteralPath $entry.path -Tail 80 | ForEach-Object {
            Write-LauncherLog -Level WARN -Message $_
        }
    }
}

function Invoke-CommonAction {
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $actionLog = Join-Path $serviceLogDir "preflight-$Action.log"
    $arguments = "/d /c `"call `"$commonBat`" $Action > `"$actionLog`" 2>&1`""
    $process = Start-Process -FilePath $cmdExe -ArgumentList $arguments `
        -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -Wait
    if (Test-Path -LiteralPath $actionLog) {
        Write-CapturedOutput -Path $actionLog
        Remove-Item -LiteralPath $actionLog -Force
    }
    if ($process.ExitCode -ne 0) {
        throw "$Name preflight failed with exit code $($process.ExitCode)"
    }
}

function Test-TcpPort {
    param(
        [int]$Port,
        [string]$HostName = "127.0.0.1"
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(250)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-HttpReady {
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][int]$Attempts,
        [Parameter(Mandatory = $true)][string]$Name
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        if ($Process.HasExited) {
            throw "$Name exited before becoming ready (exit code $($Process.ExitCode))"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-LauncherLog "$Name is ready at $Uri"
                return
            }
        }
        catch {
            # The service is still starting.
        }
        Start-Sleep -Seconds 1
    }
    throw "$Name did not become ready at $Uri after $Attempts attempts"
}

function Stop-ProcessTree {
    param($Process)
    if ($null -eq $Process -or $Process.HasExited) {
        return
    }
    & $taskkillExe /PID $Process.Id /T /F *> $null
}

try {
    Write-LauncherLog "Launcher started. log=$launcherLog"
    Invoke-CommonAction -Action "preflight-backend" -Name "backend-preflight"
    Invoke-CommonAction -Action "preflight-frontend" -Name "frontend-preflight"

    if ($CheckEnv) {
        Write-LauncherLog "Environment check passed."
        exit 0
    }

    $backendPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 }
    $frontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5173 }
    $backendAttempts = if ($env:BACKEND_TRIES) { [int]$env:BACKEND_TRIES } else { 30 }
    $frontendAttempts = if ($env:FRONTEND_TRIES) { [int]$env:FRONTEND_TRIES } else { 15 }
    $backendHost = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "127.0.0.1" }

    if (Test-TcpPort $backendPort) {
        throw "Backend port $backendPort is already in use."
    }
    if (Test-TcpPort -Port $frontendPort -HostName "localhost") {
        throw "Frontend port $frontendPort is already in use."
    }

    $env:HARNESS_SKIP_PORT_CHECK = "1"
    $env:HARNESS_SKIP_PREFLIGHT = "1"
    $backendServiceLog = Join-Path $serviceLogDir "backend.log"
    $frontendServiceLog = Join-Path $serviceLogDir "frontend.log"

    Write-LauncherLog "Starting backend on ${backendHost}:$backendPort"
    $backend = Start-Process -FilePath $cmdExe `
        -ArgumentList "/d /c `"call `"$backendBat`" >> `"$backendServiceLog`" 2>&1`"" `
        -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    Wait-HttpReady -Process $backend -Uri "http://${backendHost}:$backendPort/health" `
        -Attempts $backendAttempts -Name "Backend"

    Write-LauncherLog "Starting frontend on port $frontendPort"
    $frontend = Start-Process -FilePath $cmdExe `
        -ArgumentList "/d /c `"call `"$frontendBat`" >> `"$frontendServiceLog`" 2>&1`"" `
        -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    Wait-HttpReady -Process $frontend -Uri "http://localhost:$frontendPort/" `
        -Attempts $frontendAttempts -Name "Frontend"

    $state = [ordered]@{
        started_at = (Get-Date).ToString("o")
        backend_pid = $backend.Id
        backend_started_at = $backend.StartTime.ToUniversalTime().ToString("o")
        frontend_pid = $frontend.Id
        frontend_started_at = $frontend.StartTime.ToUniversalTime().ToString("o")
        backend_port = $backendPort
        frontend_port = $frontendPort
        launcher_log = $launcherLog
        service_log_dir = $serviceLogDir
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Write-LauncherLog "All services are ready. Run stop.bat to stop them."
    Start-Process "http://localhost:$frontendPort"
    exit 0
}
catch {
    Write-LauncherLog -Level ERROR -Message $_.Exception.Message
    Stop-ProcessTree $frontend
    Stop-ProcessTree $backend
    Merge-ServiceLogTails
    Write-ConsoleLogTail
    if ($null -ne $backend -or $null -ne $frontend) {
        Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $serviceLogDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
