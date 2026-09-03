<#
.SYNOPSIS
    Installs or updates ASH Captions for the current Windows user.

.DESCRIPTION
    No admin rights, no terminal knowledge and no PATH changes required --
    this is the script an editor runs by double-clicking
    Install-AshCaptions.bat. It:

      1. Reports (but does not act on) whether an NVIDIA GPU is present --
         GPU is always a separate opt-in step run later by Ghazi, on purpose
         (see scripts/enable_gpu.ps1 and design spec section 11.2: the
         ctranslate2/CUDA/cuDNN matrix across six different machines is the
         single most likely thing to turn rollout into a support week, so
         every machine gets the CPU build first, no exceptions).
      2. Unpacks the app bundle into %LOCALAPPDATA%\AshCaptions.
      3. Creates C:\AshCaptions\in, \out and \glossaries.
      4. Adds a desktop shortcut and a Start Menu entry.
      5. Registers a per-user "run at logon" Task Scheduler entry (no admin)
         so the tray app starts with Windows.

    Safe to run more than once: re-running updates the install in place,
    leaves an existing logon task alone rather than duplicating it, and
    overwrites (not duplicates) the shortcuts.

.PARAMETER Source
    Path to a local bundle to install from -- either the zip `build.py`
    produces (AshCaptions-<version>-win64.zip) or an already-unzipped
    AshCaptions folder. Use this for an offline install (USB stick, network
    share) or when testing a build before it is published. When omitted,
    the script downloads the newest release from -ManifestUrl.

.PARAMETER ManifestUrl
    Override the release manifest URL (see scripts/release.py and
    docs/INSTALL.md for the schema). Defaults to $DefaultManifestUrl below.

.PARAMETER CheckOnly
    Report GPU detection and the actions this run *would* take, as JSON, and
    exit without changing anything. Used by the automated test suite and
    safe for a curious editor to run by hand.
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$ManifestUrl,
    [switch]$CheckOnly,

    # Advanced / test-only overrides. Editors never pass these -- the
    # defaults are the real install locations. The automated test suite
    # points these at a temp directory and a throwaway task name so a test
    # run never touches a real machine's actual install, data folders or
    # Task Scheduler.
    [string]$InstallDir,
    [string]$DataRoot = 'C:\AshCaptions',
    [string]$TaskName = 'AshCaptionsTray',
    [string]$DesktopDir,
    [string]$StartMenuProgramsDir,
    [string]$StartupDir
)

$ErrorActionPreference = 'Stop'

# --- constants ---------------------------------------------------------

$AppName = 'AshCaptions'
$ExeName = 'AshCaptions.exe'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA $AppName }
$InDir = Join-Path $DataRoot 'in'
$OutDir = Join-Path $DataRoot 'out'
$GlossaryDir = Join-Path $DataRoot 'glossaries'

# The public artifacts repo (spec section 11.4: no source, no secrets, so this
# URL needs no auth). Matches scripts/release.py's DEFAULT_RELEASES_REPO --
# keep the two in sync if this repo is ever renamed or moved.
$DefaultManifestUrl = 'https://github.com/ASHMediaSolutions01/ashcaptions-releases/releases/latest/download/manifest.json'
if (-not $ManifestUrl) { $ManifestUrl = $DefaultManifestUrl }

# --- small helpers -------------------------------------------------------

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Done { param([string]$Message) Write-Host "    $Message" -ForegroundColor Green }
function Write-Info { param([string]$Message) Write-Host "    $Message" }
function Write-Warn { param([string]$Message) Write-Host "    WARNING: $Message" -ForegroundColor Yellow }

function Test-NvidiaGpu {
    <# Returns a hashtable describing what nvidia-smi reports, or Present=$false
       if it is missing or reports nothing. Detection only -- never used here
       to change what gets installed; see scripts/enable_gpu.ps1 for the
       opt-in step that actually reads this seriously. #>
    $cmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $cmd) {
        return [ordered]@{ Present = $false }
    }
    try {
        $name = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1
    } catch {
        return [ordered]@{ Present = $false }
    }
    if (-not $name) {
        return [ordered]@{ Present = $false }
    }
    return [ordered]@{ Present = $true; Name = $name.Trim() }
}

function Resolve-BundleSource {
    <# Returns a path to a directory ready to be mirrored into $InstallDir:
       - -Source pointing at a folder is used as-is.
       - -Source pointing at a .zip is extracted to a temp folder.
       - no -Source: download the manifest, verify the artifact hash, extract. #>
    param([string]$Source, [string]$ManifestUrl)

    if ($Source) {
        if (Test-Path -PathType Container $Source) {
            return (Resolve-Path $Source).Path
        }
        if (Test-Path -PathType Leaf $Source) {
            $extractDir = Join-Path $env:TEMP "AshCaptionsInstall_$([Guid]::NewGuid().ToString('N'))"
            Expand-Archive -Path $Source -DestinationPath $extractDir -Force
            return $extractDir
        }
        throw "Source not found: $Source"
    }

    Write-Step "Downloading the latest release manifest"
    $manifestPath = Join-Path $env:TEMP "ash-captions-manifest.json"
    Invoke-WebRequest -Uri $ManifestUrl -OutFile $manifestPath -UseBasicParsing
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json

    Write-Step "Downloading AshCaptions $($manifest.version)"
    $zipPath = Join-Path $env:TEMP $manifest.artifact.filename
    Invoke-WebRequest -Uri $manifest.artifact.url -OutFile $zipPath -UseBasicParsing

    $actualHash = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedHash = $manifest.artifact.sha256.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Downloaded file does not match the release manifest (sha256 mismatch). " +
              "Delete $zipPath and try again; if this repeats, the download was corrupted or tampered with."
    }
    Write-Done "Verified download integrity"

    $extractDir = Join-Path $env:TEMP "AshCaptionsInstall_$([Guid]::NewGuid().ToString('N'))"
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    return $extractDir
}

function Install-Bundle {
    <# Mirrors the extracted bundle into $InstallDir. Uses robocopy /MIR so a
       re-run cleanly replaces stale files from a previous version rather than
       just layering new files on top. #>
    param([string]$ExtractedRoot, [string]$InstallDir)

    # build.py's zip contains a single top-level "AshCaptions" folder; a plain
    # -Source folder passed by hand might already *be* that folder. Handle both.
    $candidate = Join-Path $ExtractedRoot $AppName
    $bundleRoot = if (Test-Path -PathType Container $candidate) { $candidate } else { $ExtractedRoot }

    if (-not (Test-Path (Join-Path $bundleRoot $ExeName))) {
        throw "Bundle at $bundleRoot does not contain $ExeName -- not a valid AshCaptions build."
    }

    Get-Process -Name 'AshCaptions' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    robocopy $bundleRoot $InstallDir /MIR /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    # Robocopy's exit codes 0-7 are all success ("files copied" etc.); 8+ is a
    # real failure.
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed while installing to $InstallDir (exit $LASTEXITCODE)"
    }
}

function New-DataDirs {
    foreach ($dir in @($InDir, $OutDir, $GlossaryDir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function New-Shortcut {
    param([string]$ShortcutPath, [string]$TargetPath, [string]$WorkingDirectory, [string]$Arguments)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = $TargetPath
    $shortcut.Description = 'ASH Captions'
    $shortcut.Save()
}

function New-Shortcuts {
    # $DesktopDir/$StartMenuProgramsDir default to the real per-user folders;
    # the test suite overrides them so a test run never writes a .lnk onto
    # whichever machine happens to run pytest.
    #
    # These shortcuts pass --open: same startup as the logon task (worker +
    # watcher + server + tray icon) plus opening the control page. The logon
    # task itself launches with NO arguments -- see Register-LogonTask below
    # -- so six editors don't get a browser tab popping up on every login;
    # the shortcut is the one place that's actually wanted.
    param([string]$InstallDir, [string]$DesktopDir, [string]$StartMenuProgramsDir)
    $exePath = Join-Path $InstallDir $ExeName

    if (-not $DesktopDir) { $DesktopDir = [Environment]::GetFolderPath('Desktop') }
    New-Item -ItemType Directory -Force -Path $DesktopDir | Out-Null
    New-Shortcut -ShortcutPath (Join-Path $DesktopDir 'ASH Captions.lnk') -TargetPath $exePath -WorkingDirectory $InstallDir -Arguments '--open'

    if (-not $StartMenuProgramsDir) {
        $StartMenuProgramsDir = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'
    }
    New-Item -ItemType Directory -Force -Path $StartMenuProgramsDir | Out-Null
    New-Shortcut -ShortcutPath (Join-Path $StartMenuProgramsDir 'ASH Captions.lnk') -TargetPath $exePath -WorkingDirectory $InstallDir -Arguments '--open'
}

function Register-LogonTask {
    <# Per-user, no admin: Register-ScheduledTask with -RunLevel Limited and no
       explicit -User registers into the current user's own task folder and
       does not require an elevation prompt. Left alone if it already exists,
       so re-running the installer never duplicates it.

       Deliberately launched with NO arguments (unlike the shortcuts, which
       pass --open): bare invocation already starts worker + watcher + server
       + tray icon, just without opening a browser tab. Six editors logging in
       every morning to an unrequested browser tab would be exactly the kind
       of thing that gets the tool turned off. #>
    param([string]$InstallDir)

    $exePath = Join-Path $InstallDir $ExeName
    $action = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $InstallDir

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        # An existing task is only "correct" if it still points at this exe;
        # a reinstall to a different folder must repair it, not trust it.
        $current = ($existing.Actions | Select-Object -First 1).Execute
        if ($current -and ($current.Trim('"') -ieq $exePath)) {
            Write-Info "Startup entry already exists -- leaving it as is."
            return $true
        }
        try {
            Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
            Write-Info "Startup entry updated to point at $exePath."
            return $true
        } catch {
            Write-Warn "Could not update the existing startup entry: $($_.Exception.Message)"
            return (Register-StartupFolderFallback -InstallDir $InstallDir)
        }
    }

    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Description 'Starts the ASH Captions tray app at logon.' `
            -ErrorAction Stop | Out-Null
        return $true
    } catch {
        # Some managed/restricted accounts deny Task Scheduler registration.
        # The app is already installed and works from the shortcuts, so this
        # must not abort the install; fall back to the per-user Startup folder.
        Write-Warn "Task Scheduler refused to register the startup entry: $($_.Exception.Message)"
        return (Register-StartupFolderFallback -InstallDir $InstallDir)
    }
}

function Register-StartupFolderFallback {
    param([string]$InstallDir)
    $folder = $StartupDir
    if (-not $folder) { $folder = [Environment]::GetFolderPath('Startup') }
    try {
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        New-Shortcut -ShortcutPath (Join-Path $folder 'ASH Captions.lnk') -TargetPath (Join-Path $InstallDir $ExeName) -WorkingDirectory $InstallDir -Arguments ''
        Write-Info "Added a Startup-folder shortcut instead: $folder"
        return $true
    } catch {
        Write-Warn "Could not add a Startup-folder shortcut either: $($_.Exception.Message)"
        return $false
    }
}

# --- main ------------------------------------------------------------------

$gpu = Test-NvidiaGpu

if ($CheckOnly) {
    $plan = [ordered]@{
        gpu             = $gpu
        install_dir     = $InstallDir
        data_root       = $DataRoot
        in_dir          = $InDir
        out_dir         = $OutDir
        glossary_dir    = $GlossaryDir
        task_name       = $TaskName
        task_exists     = [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
        manifest_url    = $ManifestUrl
        source_override = [bool]$Source
    }
    $plan | ConvertTo-Json -Depth 4
    exit 0
}

Write-Host ""
Write-Host "ASH Captions -- installing for $env:USERNAME" -ForegroundColor White
Write-Host ""

if ($gpu.Present) {
    Write-Step "NVIDIA GPU detected: $($gpu.Name)"
    Write-Info "Installing the standard (CPU) version now -- this is deliberate,"
    Write-Info "not a fallback. Ask Ghazi to run scripts/enable_gpu.ps1 on this"
    Write-Info "machine afterwards if you want GPU speed; it checks driver"
    Write-Info "compatibility first so it doesn't break instead of speeding things up."
} else {
    Write-Step "No NVIDIA GPU detected -- installing the standard (CPU) version"
}

Write-Step "Getting the app"
$extractedRoot = Resolve-BundleSource -Source $Source -ManifestUrl $ManifestUrl
Write-Done "Ready to install"

Write-Step "Installing to $InstallDir"
Install-Bundle -ExtractedRoot $extractedRoot -InstallDir $InstallDir
Write-Done "Installed"

Write-Step "Setting up your caption folders"
New-DataDirs
Write-Done "Created $InDir, $OutDir and $GlossaryDir"

Write-Step "Adding shortcuts"
New-Shortcuts -InstallDir $InstallDir -DesktopDir $DesktopDir -StartMenuProgramsDir $StartMenuProgramsDir
Write-Done "Added a Desktop shortcut and a Start Menu entry"

Write-Step "Setting ASH Captions to start automatically when you log in"
$autoStart = Register-LogonTask -InstallDir $InstallDir
if ($autoStart) {
    Write-Done "Done"
} else {
    Write-Warn "ASH Captions is installed and works from the shortcuts, but will NOT start by itself at logon on this account. Start it from the Desktop icon, or ask Ghazi."
}

Write-Host ""
Write-Host "You're all set!" -ForegroundColor Green
Write-Host ""
Write-Host "  - Drop a video into: $InDir"
Write-Host "  - Your captions will appear in: $OutDir"
Write-Host "  - Or double-click the 'ASH Captions' icon on your Desktop to"
Write-Host "    open the control page and pick a language, dialect or style."
Write-Host ""
Write-Host "First run: Windows may show a 'Windows protected your PC' warning" -ForegroundColor Yellow
Write-Host "  the very first time you open ASH Captions. That's expected -- this" -ForegroundColor Yellow
Write-Host "  is an internal tool, not something from the Windows Store, so it" -ForegroundColor Yellow
Write-Host "  isn't signed. Click 'More info' then 'Run anyway'." -ForegroundColor Yellow
Write-Host ""
