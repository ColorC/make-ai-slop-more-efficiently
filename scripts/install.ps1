Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe = $null
$PythonPrefix = @()

function Test-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )

    try {
        & $Command @Arguments *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    if (Test-ExternalCommand -Command "python" -Arguments @("--version")) {
        $PythonExe = "python"
    }
}

if (-not $PythonExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
    if (Test-ExternalCommand -Command "py" -Arguments @("-3", "--version")) {
        $PythonExe = "py"
        $PythonPrefix = @("-3")
    }
}

if (-not $PythonExe) {
    Write-Error "Python 3.10+ was not found on PATH. Install Python first, then re-run: powershell -ExecutionPolicy Bypass -File scripts/install.ps1"
}

function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $PythonExe @PythonPrefix @Arguments
    return $LASTEXITCODE
}

Invoke-Python -Arguments @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)") | Out-Null
if ($LASTEXITCODE -ne 0) {
    $VersionOutput = & $PythonExe @PythonPrefix --version 2>&1
    Write-Error "$VersionOutput is older than Python 3.10. Install Python 3.10+ and retry."
}

Invoke-Python -Arguments @("-m", "pip", "--version") | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip is not available for $PythonExe. Install/enable pip, then re-run: powershell -ExecutionPolicy Bypass -File scripts/install.ps1"
}

Push-Location $RepoRoot
try {
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        Write-Host "Installing omnicompany with pipx..."
        & pipx install .
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Install complete. Try: omni --help"
            exit 0
        }

        Write-Warning "pipx install failed; falling back to pip install ."
    } else {
        Write-Warning "pipx was not found; falling back to pip install ."
        Write-Host "Tip: install pipx for isolated CLI installs: python -m pip install --user pipx"
    }

    Write-Host "Installing omnicompany with pip..."
    Invoke-Python -Arguments @("-m", "pip", "install", ".") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install . failed."
    }
    Write-Host "Install complete. Try: omni --help"
} finally {
    Pop-Location
}
