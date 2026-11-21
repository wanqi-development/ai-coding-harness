function Resolve-AbsoluteHostPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "$Name must be an absolute Windows path: $Path"
    }
    return [IO.Path]::GetFullPath($Path)
}

function Get-HarnessHostRuntime {
    $root = if ($env:HARNESS_HOST_HOME) {
        $env:HARNESS_HOST_HOME
    }
    elseif ($env:LOCALAPPDATA) {
        Join-Path $env:LOCALAPPDATA "ai-coding-harness"
    }
    else {
        Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "ai-coding-harness"
    }
    $root = Resolve-AbsoluteHostPath -Path $root -Name "HARNESS_HOST_HOME"

    $data = if ($env:HARNESS_HOST_DATA_DIR) {
        $env:HARNESS_HOST_DATA_DIR
    }
    else {
        Join-Path $root "data"
    }
    $logs = if ($env:HARNESS_HOST_LOG_DIR) {
        $env:HARNESS_HOST_LOG_DIR
    }
    else {
        Join-Path $root "logs"
    }
    $credentials = if ($env:HARNESS_HOST_CREDENTIALS_DIR) {
        $env:HARNESS_HOST_CREDENTIALS_DIR
    }
    else {
        Join-Path $root "credentials"
    }
    $backups = if ($env:HARNESS_HOST_BACKUP_DIR) {
        $env:HARNESS_HOST_BACKUP_DIR
    }
    else {
        Join-Path $root "backups"
    }

    $result = [pscustomobject]@{
        Root = $root
        Data = Resolve-AbsoluteHostPath -Path $data -Name "HARNESS_HOST_DATA_DIR"
        Logs = Resolve-AbsoluteHostPath -Path $logs -Name "HARNESS_HOST_LOG_DIR"
        Credentials = Resolve-AbsoluteHostPath -Path $credentials -Name "HARNESS_HOST_CREDENTIALS_DIR"
        Backups = Resolve-AbsoluteHostPath -Path $backups -Name "HARNESS_HOST_BACKUP_DIR"
    }
    foreach ($path in @(
        $result.Data,
        $result.Logs,
        $result.Credentials,
        $result.Backups
    )) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    return $result
}

function Reset-HarnessLogFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, "", [Text.UTF8Encoding]::new($false))
}
