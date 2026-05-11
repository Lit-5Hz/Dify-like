param(
    [int]$Port = 8000,
    [string]$CondaEnv = 'dify',
    [switch]$Visible,
    [int]$PortWaitSeconds = 30
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$backendDir = Join-Path $root 'backend'

function Get-ListeningProcessIds {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$TargetPid,

        [string]$Name = "PID $TargetPid"
    )

    $process = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host "$Name is not running."
        return
    }

    Write-Host "Stopping $Name..."
    & taskkill.exe /PID $TargetPid /T /F | Out-Null
}

function Wait-ForPortFree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,

        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        if (-not (Get-ListeningProcessIds -Port $Port)) {
            return
        }

        Start-Sleep -Seconds 1
    }

    throw "Timed out waiting for port $Port to be released."
}

function Start-BackendPowerShell {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    $windowStyle = if ($Visible) { 'Normal' } else { 'Hidden' }
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass'
    )

    if ($Visible) {
        $arguments += '-NoExit'
    }

    $arguments += @(
        '-Command',
        $Command
    )

    Start-Process -FilePath powershell.exe `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle $windowStyle `
        -PassThru `
        -ArgumentList $arguments
}

if (-not (Test-Path $backendDir)) {
    throw "Backend directory not found: $backendDir"
}

Write-Host "Restarting backend on port $Port..."

$processIds = @(Get-ListeningProcessIds -Port $Port)
if ($processIds.Count -eq 0) {
    Write-Host "No process is listening on port $Port."
}
else {
    foreach ($processId in $processIds) {
        if ($processId -and $processId -ne 0) {
            Stop-ProcessTree -TargetPid ([int]$processId) -Name "backend listener on port $Port (PID $processId)"
        }
    }

    Wait-ForPortFree -Port $Port -TimeoutSeconds $PortWaitSeconds
}

$backendCommand = "conda run --no-capture-output -n `"$CondaEnv`" uvicorn app.main:app --reload --port $Port"
$backendProc = Start-BackendPowerShell -WorkingDirectory $backendDir -Command $backendCommand

Write-Host "Backend PID: $($backendProc.Id)"
Write-Host "Backend restart requested."
