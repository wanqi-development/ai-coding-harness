[CmdletBinding()]
param(
    [string]$Distro = $(if ($env:WSL_DISTRO) { $env:WSL_DISTRO } else { "ai-coding-harness" }),
    [string]$HarnessUser = $(if ($env:WSL_USER) { $env:WSL_USER } else { "harness" }),
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$Bootstrap,
    [switch]$SkipVerify,
    [switch]$NoFrontend
)

$ErrorActionPreference = "Stop"

function Invoke-WslChecked {
    param(
        [string[]]$Arguments,
        [switch]$CaptureOutput,
        [switch]$AllowFailure,
        [switch]$Quiet
    )
    $stderrFile = [IO.Path]::GetTempFileName()
    $previousErrorAction = $ErrorActionPreference
    try {
        # WSL writes non-fatal proxy warnings to stderr. Keep native stderr out of
        # PowerShell's ErrorRecord pipeline and decide success by the exit code.
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

    if ($exitCode -ne 0 -and -not $Quiet -and $stderr) {
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

function Invoke-Wsl {
    param([string[]]$Arguments)
    Invoke-WslChecked -Arguments $Arguments | Out-Null
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL is not installed. Run: wsl --install"
}

$distroResult = Invoke-WslChecked -Arguments @("-l", "-q") `
    -CaptureOutput -AllowFailure
if ($distroResult.ExitCode -ne 0) {
    throw "WSL is unavailable. Install WSL2 and one Linux distribution first."
}
$distros = @(
    $distroResult.Output |
        ForEach-Object { ($_ -replace "`0", "").Trim() } |
        Where-Object { $_ }
)
if ($distros -notcontains $Distro) {
    throw "WSL distribution '$Distro' was not found. Available: $($distros -join ', ')"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$repoRootForWsl = $repoRoot.Replace("\", "/")
# Resolve as root so the first run works before the harness user exists.
$pathResult = Invoke-WslChecked -CaptureOutput -Arguments @(
    "-d", $Distro, "-u", "root", "--",
    "wslpath", "-a", "-u", $repoRootForWsl
)
$wslRepo = ($pathResult.Output -join "`n").Trim()
if (-not $wslRepo) {
    throw "Cannot resolve repository path inside WSL."
}

if ($Bootstrap) {
    Write-Host "[1/4] Bootstrap WSL toolchain ..."
    Invoke-Wsl -Arguments @(
        "-d", $Distro, "-u", "root", "--",
        "env", "HARNESS_USER=$HarnessUser", "WSL_DISTRO=$Distro",
        "bash", "$wslRepo/scripts/wsl/bootstrap.sh"
    )
}

if (-not $SkipVerify) {
    Write-Host "[2/4] Verify WSL toolchain ..."
    Invoke-Wsl -Arguments @(
        "-d", $Distro, "-u", $HarnessUser, "--",
        "bash", "$wslRepo/scripts/wsl/verify-toolchain.sh"
    )
}

$backendOut = Join-Path $env:TEMP "harness-wsl-backend.out.log"
$backendErr = Join-Path $env:TEMP "harness-wsl-backend.err.log"
$frontendOut = Join-Path $env:TEMP "harness-wsl-frontend.out.log"
$frontendErr = Join-Path $env:TEMP "harness-wsl-frontend.err.log"

$backendArgs = @(
    "-d", $Distro, "-u", $HarnessUser, "--",
    "env", "PORT=$BackendPort",
    "bash", "$wslRepo/scripts/wsl/start-standalone.sh"
)
$backend = Start-Process -FilePath "wsl.exe" -ArgumentList $backendArgs `
    -WindowStyle Hidden -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr -PassThru
$frontend = $null
$previousFrontendPort = $env:FRONTEND_PORT
$previousHarnessFrontendPort = $env:HARNESS_FRONTEND_PORT
$previousHarnessBackendUrl = $env:HARNESS_BACKEND_URL

try {
    Write-Host "[3/4] Wait for backend http://127.0.0.1:$BackendPort ..."
    $backendReady = $false
    for ($i = 0; $i -lt 90; $i++) {
        if ($backend.HasExited) {
            break
        }
        try {
            $response = Invoke-WebRequest `
                -UseBasicParsing `
                -Uri "http://127.0.0.1:$BackendPort/health" `
                -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $backendReady = $true
                break
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $backendReady) {
        throw "Backend did not become ready. See $backendOut and $backendErr"
    }

    if (-not $NoFrontend) {
        Write-Host "[4/4] Start frontend http://localhost:$FrontendPort ..."
        $frontendBat = Join-Path $PSScriptRoot "start-frontend.bat"
        $env:FRONTEND_PORT = "$FrontendPort"
        $env:HARNESS_FRONTEND_PORT = "$FrontendPort"
        $env:HARNESS_BACKEND_URL = "http://127.0.0.1:$BackendPort"
        $frontend = Start-Process -FilePath "cmd.exe" `
            -ArgumentList @("/c", "`"$frontendBat`"") `
            -WindowStyle Hidden -RedirectStandardOutput $frontendOut `
            -RedirectStandardError $frontendErr -PassThru

        $frontendReady = $false
        for ($i = 0; $i -lt 60; $i++) {
            if ($frontend.HasExited) {
                break
            }
            try {
                $response = Invoke-WebRequest `
                    -UseBasicParsing `
                    -Uri "http://localhost:$FrontendPort/" `
                    -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                    $frontendReady = $true
                    break
                }
            }
            catch {
                Start-Sleep -Seconds 1
            }
        }
        if (-not $frontendReady) {
            throw "Frontend did not become ready. See $frontendOut and $frontendErr"
        }
        Start-Process "http://localhost:$FrontendPort"
    }

    Write-Host ""
    Write-Host "Standalone harness is running."
    Write-Host "Backend log: $backendOut"
    if ($frontend) {
        Write-Host "Frontend log: $frontendOut"
    }
    Write-Host "Press Ctrl+C to stop."
    Wait-Process -Id $backend.Id
}
finally {
    $env:FRONTEND_PORT = $previousFrontendPort
    $env:HARNESS_FRONTEND_PORT = $previousHarnessFrontendPort
    $env:HARNESS_BACKEND_URL = $previousHarnessBackendUrl
    $processes = @($backend)
    if ($frontend) {
        $processes += $frontend
    }
    foreach ($process in $processes) {
        if ($process -and -not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
}
