[CmdletBinding()]
param(
    [string]$Distro = $env:WSL_DISTRO,
    [string]$HarnessUser = $(if ($env:WSL_USER) { $env:WSL_USER } else { "harness" }),
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$NoFrontend
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$repoRootForWsl = $repoRoot.Replace("\", "/")
$launcher = Join-Path $PSScriptRoot "start-wsl-standalone.ps1"

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
        # Windows PowerShell turns native stderr into an ErrorRecord. WSL emits a harmless
        # localhost-proxy warning there, so isolate stderr and judge success by exit code.
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

function Get-WslDistros {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "WSL is not installed. Run: wsl --install"
    }
    $result = Invoke-WslChecked -Arguments @("-l", "-q") -CaptureOutput -AllowFailure
    if ($result.ExitCode -ne 0) {
        throw "WSL is unavailable. Install WSL2 and one Linux distribution first."
    }
    $items = @(
        $result.Output |
            ForEach-Object { ($_ -replace "`0", "").Trim() } |
            Where-Object { $_ }
    )
    if (-not $items) {
        throw "No WSL distribution is installed. Run: wsl --install -d Ubuntu-24.04"
    }
    return $items
}

$distros = Get-WslDistros
if (-not $Distro) {
    $preferred = @("Ubuntu-24.04", "Ubuntu-22.04", "Ubuntu")
    $Distro = $preferred | Where-Object { $distros -contains $_ } | Select-Object -First 1
    if (-not $Distro) {
        $Distro = $distros[0]
    }
}
if ($distros -notcontains $Distro) {
    throw "WSL distribution '$Distro' was not found. Available: $($distros -join ', ')"
}

Write-Host "Using WSL distribution: $Distro"
Write-Host "Harness user: $HarnessUser"

$needsBootstrap = $false
$userCheck = Invoke-WslChecked -CaptureOutput -AllowFailure -Quiet -Arguments @(
    "-d", $Distro, "-u", "root", "--",
    "getent", "passwd", $HarnessUser
)
$venvCheck = Invoke-WslChecked -AllowFailure -Quiet -Arguments @(
    "-d", $Distro, "-u", "root", "--",
    "test", "-x", "/home/$HarnessUser/.ai-coding-harness/venv/bin/python"
)
if ($userCheck.ExitCode -ne 0 -or -not ($userCheck.Output -join "").Trim()) {
    $needsBootstrap = $true
}
elseif ($venvCheck.ExitCode -ne 0) {
    $needsBootstrap = $true
}

# Resolve the repository from inside WSL only after the user is available.
$wslRepo = $null
if (-not $needsBootstrap) {
    $pathResult = Invoke-WslChecked -CaptureOutput -Arguments @(
        "-d", $Distro, "-u", $HarnessUser, "--",
        "wslpath", "-a", "-u", $repoRootForWsl
    )
    $wslRepo = ($pathResult.Output -join "`n").Trim()
    if (-not $wslRepo) {
        throw "Cannot resolve the repository path inside WSL."
    }

    $verifyResult = Invoke-WslChecked -CaptureOutput -AllowFailure -Arguments @(
        "-d", $Distro, "-u", $HarnessUser, "--",
        "bash", "$wslRepo/scripts/wsl/verify-toolchain.sh"
    )
    $verifyResult.Output | Write-Host
    if ($verifyResult.ExitCode -ne 0) {
        Write-Host "WSL toolchain check failed; running bootstrap to repair it."
        $needsBootstrap = $true
    }
}

if (-not $needsBootstrap) {
    $dbPath = "/home/$HarnessUser/.ai-coding-harness/harness.sqlite3"
    $dbResult = Invoke-WslChecked -CaptureOutput -AllowFailure -Quiet -Arguments @(
        "-d", $Distro, "-u", $HarnessUser, "--",
        "sqlite3", $dbPath, "SELECT COUNT(*) FROM users;"
    )
    $userCount = ($dbResult.Output -join "`n").Trim()
    if ($dbResult.ExitCode -ne 0 -or -not $userCount -or [int]$userCount -lt 1) {
        Write-Host "No administrator exists yet; running interactive bootstrap."
        $needsBootstrap = $true
    }
}

if (-not $NoFrontend) {
    if (-not $env:NODE_HOME) {
        throw "NODE_HOME is not set. Set it to the Node.js installation directory."
    }
    $npm = Join-Path $env:NODE_HOME "npm.cmd"
    if (-not (Test-Path -LiteralPath $npm)) {
        throw "npm.cmd was not found under NODE_HOME: $npm"
    }
    $viteBin = Join-Path $repoRoot "frontend\node_modules\.bin\vite.cmd"
    if (-not (Test-Path -LiteralPath $viteBin)) {
        Write-Host "Installing frontend dependencies ..."
        & $npm --prefix (Join-Path $repoRoot "frontend") install
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend dependency installation failed."
        }
    }
}

$arguments = @{
    Distro = $Distro
    HarnessUser = $HarnessUser
    BackendPort = $BackendPort
    FrontendPort = $FrontendPort
}
if ($needsBootstrap) {
    $arguments.Bootstrap = $true
}
else {
    $arguments.SkipVerify = $true
}
if ($NoFrontend) {
    $arguments.NoFrontend = $true
}

& $launcher @arguments
