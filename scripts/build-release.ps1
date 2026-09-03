<#
.SYNOPSIS
    Builds every Pedantic release artifact.

.DESCRIPTION
    Runs the full release procedure in order:

      1. Read the version from clipai.__version__.
      2. Install the project and its build dependencies.
      3. Run the unit tests.
      4. Build dist\Pedantic.exe with PyInstaller.
      5. Copy it to dist\Pedantic-<version>.exe.
      6. Create a source archive, excluding generated and private files.
      7. Build the Inno Setup installer if ISCC.exe is available.
      8. Build a portable ZIP.
      9. Write SHA-256 checksums for the downloadable artifacts.
     10. List the artifacts.

    The build fails rather than shipping if the tests fail, because an
    executable is far harder to check after the fact than a test run.

.PARAMETER SkipTests
    Skip the unit tests. Use only for iterating on packaging itself.

.PARAMETER SkipInstaller
    Do not build the Inno Setup installer.

.PARAMETER UseTempBuildDirs
    Build through directories under %TEMP%. Use this when OneDrive, Defender,
    or a running Pedantic locks build\ or dist\Pedantic.exe.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$UseTempBuildDirs
)

$ErrorActionPreference = "Stop"

# Fallback if clipai.__version__ cannot be read; keep in sync with the package.
$FallbackVersion = "0.1.2"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$DistDir = Join-Path $RepoRoot "dist"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "== $Title" -ForegroundColor Cyan
}

function Get-PythonExe {
    $venv = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venv) {
        return $venv
    }
    foreach ($candidate in @("python", "py")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            return $found.Source
        }
    }
    throw "No Python interpreter was found. Create .venv first."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$What
    )
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed with exit code $LASTEXITCODE"
    }
}

function Get-ProjectVersion {
    param([string]$Python)
    try {
        $value = & $Python -c "import clipai; print(clipai.__version__)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $value) {
            return $value.Trim()
        }
    }
    catch {
        Write-Host "  could not import clipai; using the fallback version"
    }
    return $FallbackVersion
}

function Find-Iscc {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    return $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

$Python = Get-PythonExe
Write-Host "Pedantic release build" -ForegroundColor Green
Write-Host "  python: $Python"

$Version = Get-ProjectVersion -Python $Python
Write-Host "  version: $Version"

if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

# -- 2. dependencies ---------------------------------------------------------

Write-Section "Installing the project and build dependencies"
Invoke-Checked -Exe $Python -Arguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools") -What "pip upgrade"
Invoke-Checked -Exe $Python -Arguments @("-m", "pip", "install", "-e", ".[dev]") -What "dependency install"

# -- 3. tests ----------------------------------------------------------------

if ($SkipTests) {
    Write-Section "Skipping tests (-SkipTests)"
}
else {
    Write-Section "Running the unit tests"
    Invoke-Checked -Exe $Python -Arguments @("-m", "pytest", "-q") -What "unit tests"

    Write-Section "Compiling every module"
    Invoke-Checked -Exe $Python -Arguments @("-m", "compileall", "-q", "clipai") -What "compileall"
}

# -- 4. executable -----------------------------------------------------------

Write-Section "Building the executable with PyInstaller"

$PyInstallerArgs = @("-m", "PyInstaller", "clipai.spec", "--noconfirm", "--clean")
$TempDist = $null
if ($UseTempBuildDirs) {
    $work = Join-Path $env:TEMP "pedantic-$Version-build"
    $TempDist = Join-Path $env:TEMP "pedantic-$Version-dist"
    Write-Host "  workpath: $work"
    Write-Host "  distpath: $TempDist"
    $PyInstallerArgs += @("--workpath", $work, "--distpath", $TempDist)
}
Invoke-Checked -Exe $Python -Arguments $PyInstallerArgs -What "PyInstaller"

$BuiltExe = if ($TempDist) { Join-Path $TempDist "Pedantic.exe" } else { Join-Path $DistDir "Pedantic.exe" }
if (-not (Test-Path $BuiltExe)) {
    throw "PyInstaller reported success but $BuiltExe does not exist"
}
if ($TempDist) {
    Copy-Item $BuiltExe (Join-Path $DistDir "Pedantic.exe") -Force
}

# -- 5. versioned copy -------------------------------------------------------

$VersionedExe = Join-Path $DistDir "Pedantic-$Version.exe"
Copy-Item $BuiltExe $VersionedExe -Force
Write-Host "  built $VersionedExe"

Write-Section "Smoke-testing the frozen executable"
$smoke = Start-Process -FilePath $VersionedExe -ArgumentList "--version" -PassThru -Wait
if ($smoke.ExitCode -ne 0) {
    throw "the frozen executable failed --version with exit code $($smoke.ExitCode)"
}
Write-Host "  --version exited cleanly"

# -- 6. source archive -------------------------------------------------------

Write-Section "Creating the source archive"

# Generated output, caches, runtime state, and secrets must never ship.
$SourceExcludes = @(
    ".venv", "venv", "dist", "build", ".pytest_cache", ".mypy_cache",
    "__pycache__", "clipai.egg-info", ".git", "debug-*.log", "clipai.lock",
    "*.pyc", ".env", "config.toml", "clipai.log", "history.json",
    "usage-*.jsonl"
)

$staging = Join-Path $env:TEMP "pedantic-$Version-source"
if (Test-Path $staging) {
    Remove-Item $staging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging -Force | Out-Null

function Test-Excluded {
    param([string]$RelativePath)
    foreach ($part in ($RelativePath -split '[\\/]')) {
        foreach ($pattern in $SourceExcludes) {
            if ($part -like $pattern) {
                return $true
            }
        }
    }
    return $false
}

Get-ChildItem -Path $RepoRoot -Recurse -File -Force | ForEach-Object {
    $relative = $_.FullName.Substring($RepoRoot.Length).TrimStart('\', '/')
    if (Test-Excluded -RelativePath $relative) {
        return
    }
    $target = Join-Path $staging $relative
    $parent = Split-Path -Parent $target
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item $_.FullName $target -Force
}

$SourceZip = Join-Path $DistDir "Pedantic-$Version-source.zip"
if (Test-Path $SourceZip) {
    Remove-Item $SourceZip -Force
}
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $SourceZip -CompressionLevel Optimal
Remove-Item $staging -Recurse -Force
Write-Host "  built $SourceZip"

# -- 7. installer ------------------------------------------------------------

if ($SkipInstaller) {
    Write-Section "Skipping the installer (-SkipInstaller)"
}
else {
    Write-Section "Building the Inno Setup installer"
    $Iscc = Find-Iscc
    if (-not $Iscc) {
        Write-Warning "Inno Setup 6 ISCC.exe was not found; skipping the installer."
        Write-Warning "Install it with: winget install --id JRSoftware.InnoSetup -e"
    }
    else {
        Write-Host "  iscc: $Iscc"
        Invoke-Checked -Exe $Iscc -Arguments @("installer\Pedantic.iss") -What "Inno Setup compilation"
        $SetupExe = Join-Path $DistDir "Pedantic-Setup-$Version.exe"
        if (-not (Test-Path $SetupExe)) {
            throw "Inno Setup reported success but $SetupExe does not exist"
        }
        Write-Host "  built $SetupExe"
    }
}

# -- 8. portable package -----------------------------------------------------

Write-Section "Building the portable package"

$portableStaging = Join-Path $env:TEMP "pedantic-$Version-portable"
if (Test-Path $portableStaging) {
    Remove-Item $portableStaging -Recurse -Force
}
New-Item -ItemType Directory -Path $portableStaging -Force | Out-Null

Copy-Item $VersionedExe (Join-Path $portableStaging "Pedantic.exe") -Force
Copy-Item (Join-Path $RepoRoot "installer\INSTALL.txt") $portableStaging -Force
Copy-Item (Join-Path $RepoRoot "installer\Install-Pedantic.ps1") $portableStaging -Force

$PortableZip = Join-Path $DistDir "Pedantic-$Version-portable.zip"
if (Test-Path $PortableZip) {
    Remove-Item $PortableZip -Force
}
Compress-Archive -Path (Join-Path $portableStaging "*") -DestinationPath $PortableZip -CompressionLevel Optimal
Remove-Item $portableStaging -Recurse -Force
Write-Host "  built $PortableZip"

# -- 9. checksums ------------------------------------------------------------

Write-Section "Recording SHA-256 checksums"

# Published so downloaders can confirm a file arrived intact, and because the
# winget manifest requires the installer hash. The unversioned Pedantic.exe is
# left out: it is a build intermediate, not something anyone should download.
$ChecksumFile = Join-Path $DistDir "CHECKSUMS.txt"
$ArtifactNames = @(
    "Pedantic-$Version.exe",
    "Pedantic-Setup-$Version.exe",
    "Pedantic-$Version-portable.zip",
    "Pedantic-$Version-source.zip"
)

$ChecksumLines = @(
    "# Pedantic $Version release checksums (SHA-256)",
    "#",
    "# Verify a download in PowerShell:",
    "#   Get-FileHash .\Pedantic-Setup-$Version.exe -Algorithm SHA256",
    "#",
    "# and compare the result with the matching line below. A checksum proves",
    "# the file is intact and unmodified in transit. It is not a signature: it",
    "# says nothing about who built the file.",
    ""
)

foreach ($name in $ArtifactNames) {
    $artifact = Join-Path $DistDir $name
    if (-not (Test-Path $artifact)) {
        Write-Host "  skipped $name (not built)"
        continue
    }
    $hash = (Get-FileHash -Path $artifact -Algorithm SHA256).Hash.ToLower()
    # Two spaces before the name keeps the file readable by sha256sum -c.
    $ChecksumLines += "$hash  $name"
    Write-Host "  $hash  $name"
}

Set-Content -Path $ChecksumFile -Value $ChecksumLines -Encoding ascii
Write-Host "  wrote $ChecksumFile"

# -- 10. summary -------------------------------------------------------------

Write-Section "Release artifacts"
Get-ChildItem -Path $DistDir -File |
    Where-Object { $_.Name -like "Pedantic*" -or $_.Name -eq "CHECKSUMS.txt" } |
    Sort-Object Name |
    Format-Table Name, @{ Name = "Size (MB)"; Expression = { [math]::Round($_.Length / 1MB, 2) } }, LastWriteTime -AutoSize

Write-Host "Release $Version is ready in $DistDir" -ForegroundColor Green
