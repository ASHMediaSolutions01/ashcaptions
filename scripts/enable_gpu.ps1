<#
.SYNOPSIS
    The opt-in GPU step. Run by Ghazi, per-machine, never by an editor.

.DESCRIPTION
    Design spec section 11.2: ctranslate2 4.8.x (the pinned build) requires
    cuDNN 9 + cuBLAS 12 and a driver supporting CUDA >= 12.3. Across six PCs
    with six different GPUs and driver vintages, that version matrix is the
    single most likely thing to turn rollout into a support week -- so every
    machine installs CPU-only by default (installer/install.ps1), and GPU is
    switched on afterwards, one machine at a time, with eyes on the actual
    driver version AND on the actual DLLs present.

    Two checks, both must pass:

    1. Driver. A driver's "CUDA Version" (from `nvidia-smi`) is the newest
       CUDA runtime it can run -- CUDA is backward compatible -- so comparing
       that single number against 12.3 says whether our CUDA-12 wheels can
       work here.
    2. Runtime DLLs. The CPU bundle ships NO cuBLAS and only the thin
       `cudnn64_9.dll` loader inside the ctranslate2 package: ctranslate2.dll
       loads `cublas64_12.dll` at runtime and that loader pulls in the
       `cudnn_*64_9.dll` sub-libraries. Every DLL in $RequiredCudaDlls must
       be beside AshCaptions.exe (or on PATH), or the first job dies with
       `cudnn_ops64_9.dll is not found`. Until a GPU build ships them this
       refuses everywhere -- which is the truthful answer, not a bug.

    Only when both pass does it flip the running config to device=cuda and
    model=large-v3, by editing C:\AshCaptions\settings.json directly (the
    same file config.py's Settings.load()/save() reads and writes) -- no
    Python invocation needed on the editor's machine.

    scripts/pkgtools/gpu_matrix.py holds the same decision table in Python
    and the test suite cross-checks the two.

.PARAMETER CheckOnly
    Report the decision as JSON without changing settings.json. Also the
    mode the automated test suite drives.

.PARAMETER InstallDir
    Where AshCaptions.exe is installed (installer/install.ps1's default is
    %LOCALAPPDATA%\AshCaptions). The DLL check looks here, in the bundled
    ctranslate2\ package folder beside it, and on PATH.

.PARAMETER SimulateCudaVersion
    Pretend nvidia-smi reported this "CUDA Version" string (e.g. "12.4"),
    instead of actually calling nvidia-smi. For testing the decision table
    without real GPU hardware.

.PARAMETER SimulateNoGpu
    Pretend nvidia-smi is not present / reports no GPU.

.PARAMETER SimulateDllsPresent
    Pretend every required CUDA DLL was found. For testing the driver half
    of the decision table in isolation.

.PARAMETER SettingsPath
    Override C:\AshCaptions\settings.json -- used by tests so this script
    never touches a real install.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'AshCaptions'),
    [string]$SimulateCudaVersion,
    [switch]$SimulateNoGpu,
    [switch]$SimulateDllsPresent,
    [string]$SettingsPath = 'C:\AshCaptions\settings.json'
)

$ErrorActionPreference = 'Stop'

# The one combination our GPU build ships -- keep this in lockstep with
# scripts/pkgtools/gpu_matrix.py's PINNED_* / MIN_DRIVER_CUDA_VERSION /
# REQUIRED_CUDA_DLLS (test_enable_gpu_ps1.py checks they agree).
$PinnedCtranslate2Version = '4.8.2'
$PinnedCudnnMajor = 9
$PinnedCublasMajor = 12
$MinDriverCudaVersion = [Version]'12.3'
$GpuModelSize = 'large-v3'
$RequiredCudaDlls = @(
    'cublas64_12.dll',
    'cublasLt64_12.dll',
    'cudnn64_9.dll',
    'cudnn_ops64_9.dll',
    'cudnn_cnn64_9.dll',
    'cudnn_adv64_9.dll',
    'cudnn_graph64_9.dll',
    'cudnn_engines_precompiled64_9.dll',
    'cudnn_engines_runtime_compiled64_9.dll',
    'cudnn_heuristic64_9.dll'
)

function Get-GpuInfo {
    <# Returns a hashtable: Present (bool), Name, CudaVersion (raw string as
       reported by nvidia-smi, or $null). Simulation params bypass the real
       hardware check entirely so this is testable on a machine with no GPU. #>
    param([string]$SimulateCudaVersion, [switch]$SimulateNoGpu)

    if ($SimulateNoGpu) {
        return [ordered]@{ Present = $false; Name = $null; CudaVersion = $null }
    }
    if ($SimulateCudaVersion) {
        return [ordered]@{ Present = $true; Name = '(simulated GPU)'; CudaVersion = $SimulateCudaVersion }
    }

    $cmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $cmd) {
        return [ordered]@{ Present = $false; Name = $null; CudaVersion = $null }
    }

    $name = $null
    try {
        $name = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
    } catch { }
    if (-not $name) {
        return [ordered]@{ Present = $false; Name = $null; CudaVersion = $null }
    }

    # The banner line (not available via --query-gpu) carries "CUDA Version:
    # X.Y" -- that field, not a per-GPU query, is what reflects the driver's
    # max supported CUDA runtime.
    $cudaVersion = $null
    try {
        $banner = & nvidia-smi 2>$null
        $match = ($banner | Select-String -Pattern 'CUDA Version:\s*([\d.]+)').Matches
        if ($match.Count -gt 0) { $cudaVersion = $match[0].Groups[1].Value }
    } catch { }

    return [ordered]@{ Present = $true; Name = $name.Trim(); CudaVersion = $cudaVersion }
}

function Get-CudaDllStatus {
    <# Returns a hashtable: Required, Searched (directories), Missing. The
       search set is the install dir, the ctranslate2 package folder the
       bundle places beside the exe, and every PATH entry -- the places
       LoadLibrary will actually look. #>
    param([string]$InstallDir, [switch]$SimulateDllsPresent)

    if ($SimulateDllsPresent) {
        return [ordered]@{ Required = $RequiredCudaDlls; Searched = @('(simulated)'); Missing = @() }
    }

    $searched = @()
    if ($InstallDir) {
        $searched += $InstallDir
        $searched += (Join-Path $InstallDir 'ctranslate2')
    }
    foreach ($entry in ($env:PATH -split ';')) {
        if ($entry -and $entry.Trim()) { $searched += $entry.Trim() }
    }

    $present = @{}
    foreach ($dir in $searched) {
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
        try {
            Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue | ForEach-Object {
                $present[$_.Name.ToLowerInvariant()] = $true
            }
        } catch { }
    }
    $missing = @($RequiredCudaDlls | Where-Object { -not $present.ContainsKey($_.ToLowerInvariant()) })
    return [ordered]@{ Required = $RequiredCudaDlls; Searched = $searched; Missing = $missing }
}

function Test-GpuSupport {
    <# Pure decision function -- mirrors
       scripts/pkgtools/gpu_matrix.py:evaluate_gpu_support() so both
       implementations can be tested against the same cases. #>
    param([hashtable]$Gpu, [hashtable]$Dlls)

    if (-not $Gpu.Present) {
        return [ordered]@{
            supported = $false
            reason    = 'No NVIDIA GPU detected (nvidia-smi did not report one) -- staying on CPU.'
        }
    }

    if (-not $Gpu.CudaVersion) {
        return [ordered]@{
            supported = $false
            reason    = 'Could not read a CUDA version from nvidia-smi -- refusing to guess. ' +
                        'Update the NVIDIA driver and re-run this script.'
        }
    }

    $parsed = $null
    try { $parsed = [Version]$Gpu.CudaVersion } catch { $parsed = $null }
    if (-not $parsed) {
        return [ordered]@{
            supported = $false
            reason    = "Could not parse nvidia-smi's CUDA version '$($Gpu.CudaVersion)' -- refusing to guess."
        }
    }

    if ($parsed -lt $MinDriverCudaVersion) {
        return [ordered]@{
            supported = $false
            reason    = "Driver supports CUDA $($Gpu.CudaVersion), but ctranslate2 $PinnedCtranslate2Version " +
                        "needs cuDNN $PinnedCudnnMajor + CUDA >= $MinDriverCudaVersion. Installing anyway " +
                        "reproduces the 'cudnn_ops64_9.dll is not found' failure at the first job. " +
                        "Update the NVIDIA driver to a version reporting CUDA $MinDriverCudaVersion or newer, " +
                        "then re-run this script."
        }
    }

    if ($Dlls.Missing.Count -gt 0) {
        return [ordered]@{
            supported = $false
            reason    = "Driver supports CUDA $($Gpu.CudaVersion), but the installed bundle has no CUDA runtime: " +
                        "missing $($Dlls.Missing -join ', '). The CPU build ships no cuBLAS $PinnedCublasMajor / " +
                        "cuDNN $PinnedCudnnMajor libraries, so device=cuda would fail at the first job with " +
                        "'cudnn_ops64_9.dll is not found'. Copy the DLLs from NVIDIA's nvidia-cublas-cu12 and " +
                        "nvidia-cudnn-cu12 wheels (their bin\ folders) beside AshCaptions.exe in $InstallDir, " +
                        "then re-run this script."
        }
    }

    return [ordered]@{
        supported = $true
        reason    = "Driver supports CUDA $($Gpu.CudaVersion) >= required $MinDriverCudaVersion and every " +
                    "cuBLAS $PinnedCublasMajor / cuDNN $PinnedCudnnMajor DLL ctranslate2 $PinnedCtranslate2Version " +
                    "loads is present -- safe to switch to device=cuda, model=$GpuModelSize."
    }
}

function Set-GpuConfig {
    <# Flip settings.json's device/model_size in place. Only ever called
       after Test-GpuSupport says supported=$true. Never touches any other
       key, and creates the file with sane defaults if it does not exist yet
       (matches config.py's Settings() defaults for every other field). #>
    param([string]$SettingsPath, [string]$ModelSize)

    $settings = [ordered]@{}
    if (Test-Path $SettingsPath) {
        $existing = Get-Content $SettingsPath -Raw | ConvertFrom-Json
        $existing.PSObject.Properties | ForEach-Object { $settings[$_.Name] = $_.Value }
    }
    $settings['device'] = 'cuda'
    $settings['model_size'] = $ModelSize

    $dir = Split-Path -Parent $SettingsPath
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    # Set-Content -Encoding utf8 writes a UTF-8 BOM on Windows PowerShell 5.1,
    # which Python's json.loads() (config.py's Settings.load()) rejects
    # outright. Write plain BOM-less UTF-8 via .NET directly instead.
    $json = $settings | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($SettingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
}

# --- main --------------------------------------------------------------

$gpu = Get-GpuInfo -SimulateCudaVersion $SimulateCudaVersion -SimulateNoGpu:$SimulateNoGpu
$dlls = Get-CudaDllStatus -InstallDir $InstallDir -SimulateDllsPresent:$SimulateDllsPresent
$decision = Test-GpuSupport -Gpu $gpu -Dlls $dlls

if ($CheckOnly) {
    [ordered]@{
        gpu      = $gpu
        dlls     = [ordered]@{ required = $dlls.Required; missing = $dlls.Missing }
        decision = $decision
    } | ConvertTo-Json -Depth 4
    exit 0
}

Write-Host ""
Write-Host "ASH Captions -- GPU opt-in check" -ForegroundColor White
Write-Host ""
if ($gpu.Present) {
    Write-Host "GPU: $($gpu.Name)"
    Write-Host "Driver-reported CUDA Version: $($gpu.CudaVersion)"
} else {
    Write-Host "No NVIDIA GPU detected."
}
if ($dlls.Missing.Count -gt 0) {
    Write-Host "CUDA runtime DLLs missing beside $InstallDir\AshCaptions.exe: $($dlls.Missing -join ', ')"
} else {
    Write-Host "CUDA runtime DLLs: all $($dlls.Required.Count) present."
}
Write-Host ""

if (-not $decision.supported) {
    Write-Host "REFUSING to enable GPU on this machine." -ForegroundColor Red
    Write-Host $decision.reason -ForegroundColor Yellow
    Write-Host ""
    Write-Host "This machine stays on the CPU build -- that's a safe, working state, not a failure." -ForegroundColor Green
    exit 1
}

Write-Host $decision.reason -ForegroundColor Green
Write-Host ""

Set-GpuConfig -SettingsPath $SettingsPath -ModelSize $GpuModelSize
Write-Host "Updated $SettingsPath -> device=cuda, model_size=$GpuModelSize" -ForegroundColor Green
Write-Host "Restart ASH Captions on this machine for the change to take effect."
