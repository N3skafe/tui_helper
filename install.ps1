# Register the tui command for the current user.
# cmd and PowerShell both find tui.cmd on PATH. A new terminal is required.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Create the virtual environment first. See the README Setup section.'
    exit 1
}

$dest = Join-Path $env:LOCALAPPDATA 'tui_helper'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$launcher = Join-Path $root 'tui.cmd'
$shim = Join-Path $dest 'tui.cmd'
$content = "@echo off`r`ncall `"$launcher`" %*`r`n"
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($shim, $content, $utf8)
$account = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
icacls $shim /inheritance:r /grant:r "${account}:(F)" "SYSTEM:(F)" | Out-Null
icacls $dest /inheritance:r /grant:r "${account}:(OI)(CI)(F)" "SYSTEM:(OI)(CI)(F)" | Out-Null

$user = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $user) { $user = '' }
$target = $dest.TrimEnd('\')
$found = $false
foreach ($item in ($user -split ';')) {
    if ($item.Trim().TrimEnd('\') -ieq $target) { $found = $true }
}
if (-not $found) {
    if ($user.Trim() -eq '') {
        $new = $dest
    } else {
        $new = $user.TrimEnd(';') + ';' + $dest
    }
    [Environment]::SetEnvironmentVariable('Path', $new, 'User')
    Write-Host "Added to the user PATH: $dest"
} else {
    Write-Host "The user PATH already contains $dest"
}

Write-Host "Installed: $shim"
Write-Host 'Close this window and open a new cmd or PowerShell window.'
Write-Host 'Then run: tui'
