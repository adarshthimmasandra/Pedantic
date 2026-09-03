<#
.SYNOPSIS
    Installs Pedantic for the current user. No administrator rights needed.

.DESCRIPTION
    Copies Pedantic.exe to %LOCALAPPDATA%\Programs\Pedantic and optionally
    creates Start Menu, desktop, and startup shortcuts.

    The install is per-user on purpose. Pedantic injects keystrokes into the
    application you are typing in, and Windows blocks that across integrity
    levels, so it must run as the normal user rather than elevated.

.PARAMETER DesktopShortcut
    Also create a desktop shortcut.

.PARAMETER StartWithWindows
    Also start Pedantic when the current user signs in.

.PARAMETER Uninstall
    Remove the installed copy and its shortcuts. User data is kept.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File Install-Pedantic.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File Install-Pedantic.ps1 -DesktopShortcut -StartWithWindows
#>

[CmdletBinding()]
param(
    [switch]$DesktopShortcut,
    [switch]$StartWithWindows,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$AppName      = "Pedantic"
$ExeName      = "Pedantic.exe"
$InstallDir   = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$InstalledExe = Join-Path $InstallDir $ExeName
$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$StartupDir   = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$DesktopDir   = [Environment]::GetFolderPath("Desktop")

$Shortcuts = @(
    (Join-Path $StartMenuDir "$AppName.lnk"),
    (Join-Path $StartupDir   "$AppName.lnk"),
    (Join-Path $DesktopDir   "$AppName.lnk")
)

function Write-Step {
    param([string]$Message)
    Write-Host "  $Message"
}

function Stop-RunningPedantic {
    $running = Get-Process -Name ($ExeName -replace '\.exe$', '') -ErrorAction SilentlyContinue
    if (-not $running) {
        return
    }
    Write-Step "Stopping the running $AppName instance"
    $running | Stop-Process -Force
    # The file stays locked briefly after the process object disappears.
    Start-Sleep -Milliseconds 800
}

function New-Shortcut {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Target
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)
        $shortcut.TargetPath       = $Target
        $shortcut.WorkingDirectory = Split-Path -Parent $Target
        $shortcut.Description      = "$AppName - transform selected text with a hotkey"
        $shortcut.Save()
    }
    finally {
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
    }
    Write-Step "Created $Path"
}

function Invoke-Uninstall {
    Write-Host "Removing $AppName for the current user..." -ForegroundColor Cyan
    Stop-RunningPedantic

    foreach ($shortcut in $Shortcuts) {
        if (Test-Path $shortcut) {
            Remove-Item $shortcut -Force
            Write-Step "Removed $shortcut"
        }
    }

    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
        Write-Step "Removed $InstallDir"
    }

    Write-Host ""
    Write-Host "$AppName was removed." -ForegroundColor Green
    Write-Host "Your settings and history are still in $env:APPDATA\clipai."
    Write-Host "Delete that folder if you want them gone too."
}

function Invoke-Install {
    $source = Join-Path $PSScriptRoot $ExeName
    if (-not (Test-Path $source)) {
        throw "$ExeName was not found next to this script ($PSScriptRoot)."
    }

    Write-Host "Installing $AppName for the current user..." -ForegroundColor Cyan
    Stop-RunningPedantic

    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }
    Copy-Item $source $InstalledExe -Force
    Write-Step "Installed $InstalledExe"

    $instructions = Join-Path $PSScriptRoot "INSTALL.txt"
    if (Test-Path $instructions) {
        Copy-Item $instructions (Join-Path $InstallDir "INSTALL.txt") -Force
    }

    New-Shortcut -Path (Join-Path $StartMenuDir "$AppName.lnk") -Target $InstalledExe
    if ($DesktopShortcut) {
        New-Shortcut -Path (Join-Path $DesktopDir "$AppName.lnk") -Target $InstalledExe
    }
    if ($StartWithWindows) {
        New-Shortcut -Path (Join-Path $StartupDir "$AppName.lnk") -Target $InstalledExe
    }

    Write-Host ""
    Write-Host "$AppName is installed." -ForegroundColor Green
    Write-Host "Starting it now. Look for the green P icon in the system tray."
    Write-Host "On first launch it will ask for your Anthropic API key."
    Start-Process -FilePath $InstalledExe
}

if ($Uninstall) {
    Invoke-Uninstall
}
else {
    Invoke-Install
}
