<#
.SYNOPSIS
    Removes ASH Captions for the current Windows user.

.DESCRIPTION
    The reverse of install.ps1 -- the script an editor runs by double-clicking
    Uninstall-AshCaptions.bat. No admin rights needed. It:

      1. Quits the running ASH Captions (only a copy started from the install
         folder -- never some other AshCaptions.exe on the machine).
      2. Removes the "start at logon" Task Scheduler entry, or the
         Startup-folder shortcut the installer fell back to.
      3. Deletes the Desktop and Start Menu shortcuts.
      4. Deletes the install folder (%LOCALAPPDATA%\AshCaptions).

    Your captions, drop folder, glossaries, settings.json and the log in
    C:\AshCaptions are KEPT unless -RemoveData is passed, and the script says
    so at the end. Safe to run more than once: anything already gone is
    reported, not treated as an error.

.PARAMETER RemoveData
    Also delete C:\AshCaptions -- every caption ever produced, the settings,
    the glossaries and the log. Cannot be undone.

.PARAMETER CheckOnly
    Report what this run *would* remove, as JSON, and exit without changing
    anything. Used by the automated test suite.
#>
[CmdletBinding()]
param(
    [switch]$RemoveData,
    [switch]$CheckOnly,

    # Advanced / test-only overrides -- the same set install.ps1 takes, with
    # the same defaults, so a test can uninstall exactly the scratch install
    # it made and never touch the real one.
    [string]$InstallDir,
    [string]$DataRoot = 'C:\AshCaptions',
    [string]$TaskName = 'AshCaptionsTray',
    [string]$DesktopDir,
    [string]$StartMenuProgramsDir,
    [string]$StartupDir
)

$ErrorActionPreference = 'Stop'

# --- constants ---------------------------------------------------------------

$AppName = 'AshCaptions'
$ProcessName = 'AshCaptions'
$ShortcutName = 'ASH Captions.lnk'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA $AppName }
if (-not $DesktopDir) { $DesktopDir = [Environment]::GetFolderPath('Desktop') }
if (-not $StartMenuProgramsDir) { $StartMenuProgramsDir = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs' }
if (-not $StartupDir) { $StartupDir = [Environment]::GetFolderPath('Startup') }

$DesktopShortcut = Join-Path $DesktopDir $ShortcutName
$StartMenuShortcut = Join-Path $StartMenuProgramsDir $ShortcutName
$StartupShortcut = Join-Path $StartupDir $ShortcutName

# --- small helpers -----------------------------------------------------------

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Done { param([string]$Message) Write-Host "    $Message" -ForegroundColor Green }
function Write-Info { param([string]$Message) Write-Host "    $Message" }
function Write-Warn { param([string]$Message) Write-Host "    WARNING: $Message" -ForegroundColor Yellow }

function Get-RunningApp {
    <# Only processes started from $InstallDir: a scratch uninstall must never
       quit a real install, and vice versa. Path is $null for processes we
       cannot inspect; those are skipped. #>
    Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) }
}

function Stop-RunningApp {
    $running = @(Get-RunningApp)
    if ($running.Count -eq 0) {
        Write-Info "ASH Captions is not running."
        return
    }
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    # The tray process needs a moment to release the exe and its lock file.
    $deadline = (Get-Date).AddSeconds(10)
    while ((@(Get-RunningApp).Count -gt 0) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Milliseconds 250 }
    Write-Done "Quit ASH Captions"
}

function Remove-LogonEntry {
    $removed = @()
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        $removed += "the start-at-logon task '$TaskName'"
    }
    if (Test-Path -PathType Leaf $StartupShortcut) {
        Remove-Item -Force -LiteralPath $StartupShortcut
        $removed += "the Startup-folder shortcut $StartupShortcut"
    }
    return ,$removed
}

function Remove-Shortcuts {
    $removed = @()
    foreach ($shortcut in @($DesktopShortcut, $StartMenuShortcut)) {
        if (Test-Path -PathType Leaf $shortcut) {
            Remove-Item -Force -LiteralPath $shortcut
            $removed += $shortcut
        }
    }
    return ,$removed
}

function Remove-Folder {
    <# Windows can hold a just-quit exe's files open for a moment; retry. #>
    param([string]$Path)
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -Recurse -Force -LiteralPath $Path -ErrorAction Stop
            return
        } catch {
            if ($attempt -eq 5) { throw }
            Start-Sleep -Seconds 1
        }
    }
}

# --- main --------------------------------------------------------------------

if ($CheckOnly) {
    $plan = [ordered]@{
        install_dir              = $InstallDir
        install_dir_exists       = [bool](Test-Path -PathType Container $InstallDir)
        data_root                = $DataRoot
        data_root_exists         = [bool](Test-Path -PathType Container $DataRoot)
        remove_data               = [bool]$RemoveData
        task_name                = $TaskName
        task_exists               = [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
        startup_shortcut          = $StartupShortcut
        startup_shortcut_exists  = [bool](Test-Path -PathType Leaf $StartupShortcut)
        shortcuts                = @(@($DesktopShortcut, $StartMenuShortcut) | Where-Object { Test-Path -PathType Leaf $_ })
        app_running               = (@(Get-RunningApp).Count -gt 0)
    }
    $plan | ConvertTo-Json -Depth 4
    exit 0
}

Write-Host ""
Write-Host "ASH Captions -- uninstalling for $env:USERNAME" -ForegroundColor White
Write-Host ""

Write-Step "Quitting ASH Captions"
Stop-RunningApp

Write-Step "Removing the start-at-logon entry"
$logonRemoved = Remove-LogonEntry
if ($logonRemoved.Count -eq 0) {
    Write-Info "No start-at-logon entry found."
} else {
    foreach ($item in $logonRemoved) { Write-Done "Removed $item" }
}

Write-Step "Removing shortcuts"
$shortcutsRemoved = Remove-Shortcuts
if ($shortcutsRemoved.Count -eq 0) {
    Write-Info "No Desktop or Start Menu shortcut found."
} else {
    foreach ($item in $shortcutsRemoved) { Write-Done "Removed $item" }
}

Write-Step "Removing the app from $InstallDir"
if (Test-Path -PathType Container $InstallDir) {
    Remove-Folder -Path $InstallDir
    Write-Done "Removed $InstallDir"
} else {
    Write-Info "Nothing at $InstallDir -- already removed."
}

if ($RemoveData) {
    Write-Step "Removing your data folder $DataRoot"
    if (Test-Path -PathType Container $DataRoot) {
        Remove-Folder -Path $DataRoot
        Write-Done "Removed $DataRoot"
    } else {
        Write-Info "Nothing at $DataRoot -- already removed."
    }
}

Write-Host ""
Write-Host "ASH Captions has been uninstalled." -ForegroundColor Green
if (-not $RemoveData) {
    Write-Host ""
    Write-Host "Kept (not deleted):" -ForegroundColor Yellow
    Write-Host "  - $DataRoot"
    Write-Host "    Your captions (out), the drop folder (in), glossaries, settings.json and the log."
    Write-Host "    Delete that folder yourself if you no longer want them, or run the"
    Write-Host "    uninstaller again with -RemoveData."
}
Write-Host ""
