# Build dist\tui.exe. Python and the virtual environment are only needed here.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "py"
    $prefix = @("-3")
} else {
    $prefix = @()
}
& $python @prefix -m pip install -r (Join-Path $root "requirements.txt") pyinstaller
& $python @prefix -m PyInstaller --noconfirm --clean (Join-Path $root "tui.spec")
Write-Host "Built $(Join-Path $root 'dist\tui.exe')"
