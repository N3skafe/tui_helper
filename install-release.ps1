# Install the latest GitHub release tui.exe for the current user.
# End users: run install-release.cmd or this script. No Python or venv required.
# Developers who want the source shim should run install.ps1 instead.

$ErrorActionPreference = 'Stop'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$repo = 'N3skafe/tui_helper'
$dest = Join-Path $env:LOCALAPPDATA 'tui_helper'
$destExe = Join-Path $dest 'tui.exe'
$destCmd = Join-Path $dest 'tui.cmd'

function Add-UserPathEntry {
    param([string]$Entry)
    $target = $Entry.TrimEnd('\')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($null -eq $user) { $user = '' }
    $kept = New-Object System.Collections.Generic.List[string]
    $found = $false
    foreach ($item in ($user -split ';')) {
        $trim = $item.Trim()
        if ($trim -eq '') { continue }
        if ($trim.TrimEnd('\') -ieq $target) {
            if (-not $found) { $kept.Add($trim) }
            $found = $true
        } else {
            $kept.Add($trim)
        }
    }
    if (-not $found) { $kept.Add($Entry) }
    $new = ($kept -join ';')
    $old = (($user -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }) -join ';')
    if ($new -ne $old) {
        [Environment]::SetEnvironmentVariable('Path', $new, 'User')
        if ($found) {
            Write-Host "Removed duplicate user PATH entries for $Entry"
        } else {
            Write-Host "Added to the user PATH: $Entry"
        }
    } else {
        Write-Host "The user PATH already contains $Entry"
    }

    $session = New-Object System.Collections.Generic.List[string]
    $seen = $false
    foreach ($item in ($env:Path -split ';')) {
        $trim = $item.Trim()
        if ($trim -eq '') { continue }
        if ($trim.TrimEnd('\') -ieq $target) {
            if (-not $seen) { $session.Add($trim) }
            $seen = $true
        } else {
            $session.Add($trim)
        }
    }
    if (-not $seen) { $session.Add($Entry) }
    $env:Path = ($session -join ';')
}

function Save-WebFile {
    param([string]$Url, [string]$OutFile)
    if ($PSVersionTable.PSVersion.Major -lt 6) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    }
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
}

function Test-GhAuthenticated {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { return $false }
    & gh auth status 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-ReleaseFiles {
    param([string]$Directory)
    $exe = Join-Path $Directory 'tui.exe'
    $sha = Join-Path $Directory 'tui.exe.sha256'
    $exeUrl = if ($env:TUI_RELEASE_EXE_URL) { $env:TUI_RELEASE_EXE_URL } else { "https://github.com/$repo/releases/latest/download/tui.exe" }
    $shaUrl = if ($env:TUI_RELEASE_SHA_URL) { $env:TUI_RELEASE_SHA_URL } else { "https://github.com/$repo/releases/latest/download/tui.exe.sha256" }

    if ((Test-GhAuthenticated) -and -not $env:TUI_RELEASE_EXE_URL) {
        Write-Host 'Downloading the latest release with gh.'
        & gh release download --repo $repo --pattern tui.exe --dir $Directory --clobber
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $exe)) {
            & gh release download --repo $repo --pattern tui.exe.sha256 --dir $Directory --clobber
            if ($LASTEXITCODE -ne 0) {
                Write-Host 'tui.exe.sha256 is not on this release. Continuing without a hash check.'
                if (Test-Path -LiteralPath $sha) { Remove-Item -LiteralPath $sha -Force }
            }
            return
        }
        Write-Host 'gh download failed. Trying HTTPS.'
        if (Test-Path -LiteralPath $exe) { Remove-Item -LiteralPath $exe -Force }
    }

    Write-Host 'Downloading the latest release over HTTPS.'
    try {
        Save-WebFile -Url $exeUrl -OutFile $exe
    } catch {
        throw 'Could not download tui.exe from the latest GitHub release. Check the network and https://github.com/N3skafe/tui_helper/releases'
    }
    try {
        Save-WebFile -Url $shaUrl -OutFile $sha
    } catch {
        Write-Host 'tui.exe.sha256 is not on this release. Continuing without a hash check.'
        if (Test-Path -LiteralPath $sha) { Remove-Item -LiteralPath $sha -Force }
    }
}

function Assert-PeFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw 'Download did not produce tui.exe.'
    }
    $length = (Get-Item -LiteralPath $Path).Length
    if ($length -lt 1000000) {
        throw "Downloaded tui.exe is too small ($length bytes). The release asset may be missing."
    }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $bytes = New-Object byte[] 2
        [void]$stream.Read($bytes, 0, 2)
    } finally {
        $stream.Close()
    }
    if ($bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {
        throw 'Downloaded file is not a Windows executable.'
    }
}

function Assert-Sha256 {
    param([string]$ExePath, [string]$ShaPath)
    if (-not (Test-Path -LiteralPath $ShaPath)) { return }
    $token = ((Get-Content -LiteralPath $ShaPath -Raw) -split '\s+')[0]
    if ($null -eq $token) { $token = '' }
    $token = $token.Trim().ToLowerInvariant()
    if ($token -notmatch '^[0-9a-f]{64}$') {
        throw 'tui.exe.sha256 does not contain a 64-digit SHA-256 hash.'
    }
    $actual = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $token) {
        throw "SHA-256 mismatch. Expected $token, got $actual."
    }
    Write-Host "SHA-256 verified: $actual"
}

try {
    if (-not $env:LOCALAPPDATA) {
        throw 'LOCALAPPDATA is not set.'
    }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    $temp = Join-Path ([System.IO.Path]::GetTempPath()) ('tui-release-' + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        Get-ReleaseFiles -Directory $temp
        $tempExe = Join-Path $temp 'tui.exe'
        $tempSha = Join-Path $temp 'tui.exe.sha256'
        Assert-PeFile -Path $tempExe
        Assert-Sha256 -ExePath $tempExe -ShaPath $tempSha
        [System.IO.File]::Copy($tempExe, $destExe, $true)
    } finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (Test-Path -LiteralPath $destCmd) {
        Remove-Item -LiteralPath $destCmd -Force
        Write-Host "Removed old source shim: $destCmd"
    }

    $account = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    if ($account -and (Test-Path -LiteralPath $icacls)) {
        & $icacls $destExe /inheritance:r /grant:r "${account}:(F)" 'SYSTEM:(F)' 1>$null 2>$null
    }

    Add-UserPathEntry -Entry $dest

    Write-Host "Installed: $destExe"
    $resolved = Get-Command tui -ErrorAction SilentlyContinue
    if ($null -eq $resolved) {
        throw 'tui.exe is installed, but this process still cannot resolve tui. Open a new terminal and run: tui'
    }
    Write-Host "Resolved: $($resolved.Source)"
    Write-Host 'This window can run: tui'
    Write-Host 'Close and reopen any other terminals so they pick up the user PATH.'
} catch {
    Write-Host "Install failed: $($_.Exception.Message)"
    exit 1
}
