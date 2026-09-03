# Pedantic — Exact Reconstruction and Release Guide

This document describes how to reconstruct, test, package, install, and troubleshoot the current Pedantic application from source. It is written for the current Windows release, version **0.1.2**.

## 1. What the software does

Pedantic is an always-running Windows system-tray application. The user selects text in any application and presses a configured global hotkey, such as `Ctrl+Shift+G`.

The runtime sequence is:

1. Detect the global profile hotkey.
2. Prevent the foreground application from receiving that profile hotkey.
3. Wait for the physical hotkey modifiers to be released.
4. Simulate a clean `Ctrl+C` using the Windows `SendInput` API.
5. Wait for the Windows clipboard sequence number to change.
6. Read plain Unicode text from the clipboard.
7. Validate and sanitize the copied text.
8. Send it to the configured Anthropic Claude Haiku model.
9. Clean the model response.
10. Put the response on the clipboard.
11. Simulate `Ctrl+V`.
12. Restore the clipboard content that existed before the hotkey was pressed.

The application supports multiple profiles. Each profile has its own hotkey, instruction prompt, temperature, and paste behavior.

## 2. Current release identity

Keep the following version values synchronized:

- `clipai/__init__.py`: `__version__ = "0.1.2"`
- `pyproject.toml`: `version = "0.1.2"`
- `installer/Pedantic.iss`: `MyAppVersion` and `MyAppSourceExeName`
- `scripts/build-release.ps1`: fallback version
- `README.md`: release artifact examples

The expected release files are:

- `dist/Pedantic.exe`
- `dist/Pedantic-0.1.2.exe`
- `dist/Pedantic-Setup-0.1.2.exe`
- `dist/Pedantic-0.1.2-portable.zip`
- `dist/Pedantic-0.1.2-source.zip`

The historical file `dist/Pedantic-0.1.0-source.zip` may contain newer source because it was explicitly retained under that filename. Do not infer the contained source version from that historical filename; inspect `pyproject.toml` inside the archive.

## 3. Supported build platform

Build the Windows executable on Windows. PyInstaller executables are platform-specific; a Windows executable must not be built on macOS or Linux.

Current known build environment:

- Operating system: Windows 11
- OS build observed during packaging: `10.0.26200`
- Architecture: 64-bit x86 Windows
- Python used for the current build: `3.14.7`
- Minimum Python declared by the project: `3.11`
- PyInstaller: `6.22.2`
- PyInstaller hooks: `2026.7`
- Inno Setup: `6.7.3`

Python 3.11 or later should work from source. For the closest reconstruction of the current binary, use 64-bit Python 3.14.7.

Exact byte-for-byte executable reproduction is not guaranteed because PyInstaller and Inno Setup embed build timestamps and environment-dependent metadata. Following this guide reproduces the same source, behavior, dependencies, packaging method, and artifact layout.

## 4. Required external software

Install:

1. 64-bit Python 3.14.7 from `https://www.python.org/downloads/windows/`
2. Inno Setup 6 from `https://jrsoftware.org/isinfo.php`
3. PowerShell

Git is optional if the source was received as a ZIP.

Inno Setup may be installed with Windows Package Manager:

```powershell
winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
```

Verify the tools:

```powershell
python --version
python -m pip --version
winget --version
```

Verify Inno Setup by checking one of these locations:

```text
%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
%ProgramFiles%\Inno Setup 6\ISCC.exe
```

## 5. Source tree and responsibilities

```text
Pedantic/
├── clipai/
│   ├── __init__.py                 Application version
│   ├── __main__.py                 CLI entry point and startup sequence
│   ├── app.py                      Hotkey-to-copy-to-API-to-paste orchestration
│   ├── hotkeys.py                  Global hotkey listener and Windows suppression
│   ├── keys.py                     Synthetic Ctrl+C/Ctrl+V via Windows SendInput
│   ├── clipboard.py                Clipboard read/write/snapshot/poll/restore
│   ├── selection.py                Win32 text-control helpers retained for diagnostics
│   ├── config.py                   Default TOML, validation, and live reload
│   ├── credentials.py              Windows Credential Manager and environment fallback
│   ├── cleaning.py                 Input/output sanitization
│   ├── prompts.py                  System prompt assembly and user-text wrapping
│   ├── tray.py                     Tray icon, menu, notifications, and history UI
│   ├── history.py                  Local history
│   ├── usage.py                    Token/cost records and budget enforcement
│   ├── paths.py                    Per-user data locations
│   ├── platform.py                 Single-instance lock and OS actions
│   ├── runtime_frozen.py           Frozen-runtime certificate/keyring setup
│   ├── pyi_rth_frozen.py           PyInstaller runtime-hook entry point
│   ├── logging_setup.py            Rotating logging and secret redaction
│   └── backends/
│       ├── base.py                 Backend interface and output-token sizing
│       ├── anthropic_api.py        Anthropic client and Windows network configuration
│       └── retry.py                Retry policy and error classification
├── tests/                           Unit and optional integration tests
├── installer/
│   ├── Pedantic.iss                Inno Setup definition
│   ├── Install-Pedantic.ps1        Portable user-level installer
│   └── INSTALL.txt                 End-user instructions
├── scripts/
│   └── build-release.ps1           Complete release build
├── clipai.spec                     PyInstaller one-file build definition
├── pyproject.toml                  Package metadata and dependencies
├── README.md                       User/developer overview
└── detailed.md                     This reconstruction guide
```

Generated directories must not be treated as source:

```text
.venv/
build/
dist/
.pytest_cache/
clipai.egg-info/
**/__pycache__/
```

Do not distribute:

- `.env`
- API keys
- user `config.toml`
- `clipai.log`
- `history.json`
- `usage-*.jsonl`
- `clipai.lock`
- `debug-*.log`

## 6. Python dependencies

The authoritative dependency declarations are in `pyproject.toml`.

Runtime dependencies:

```text
anthropic>=0.40
certifi>=2024.0
httpx2
keyring>=25.0
pystray>=0.19
pillow>=10.0
pynput>=1.7
pyperclip>=1.9
watchdog>=4.0
tomli-w>=1.0
pywin32>=306 on Windows
```

Development/build dependencies:

```text
pytest>=8.0
pyinstaller>=6.0
```

Versions used by the latest local release build:

```text
anthropic==1.2.0
certifi==2026.7.22
httpx2==2.12.0
httpcore2==2.12.0
truststore==0.10.4
keyring==25.7.0
pystray==0.19.5
pillow==12.3.0
pynput==1.8.2
pyperclip==1.11.0
watchdog==6.0.0
tomli-w==1.2.0
pywin32==312
pytest==9.1.1
pyinstaller==6.22.2
pyinstaller-hooks-contrib==2026.7
setuptools==84.0.0
```

Use `pyproject.toml` for normal builds. Use the exact versions above only when trying to match the known build environment as closely as possible.

## 7. Clean environment setup

Open PowerShell in the source root:

```powershell
Set-Location "C:\path\to\Pedantic"
```

Create an isolated environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Confirm the application version:

```powershell
python -m clipai --version
```

Expected:

```text
clipai 0.1.2
```

## 8. Configuration and user data

On Windows, Pedantic stores per-user data under:

```text
%APPDATA%\clipai
```

Files:

```text
%APPDATA%\clipai\config.toml
%APPDATA%\clipai\clipai.log
%APPDATA%\clipai\history.json
%APPDATA%\clipai\usage-YYYY-MM.jsonl
%APPDATA%\clipai\clipai.lock
```

The default configuration is created only when `config.toml` does not exist. Upgrading the executable does not overwrite an existing configuration.

Current recommended API configuration:

```toml
[api]
model = "claude-haiku-4-5-20251001"
timeout_seconds = 30
max_attempts = 3
max_tokens_ceiling = 2048
max_input_chars = 8000
```

Current recommended behavior configuration:

```toml
[behavior]
paste_delay_ms = 300
clipboard_poll_timeout_ms = 700
restore_clipboard = true
notify_on_success = false
notify_on_error = true
```

Default profiles:

```toml
[[profile]]
name = "grammar"
hotkey = "ctrl+shift+g"
paste = true
temperature = 0.1
prompt = "Fix spelling, grammar, and punctuation only. Preserve wording, tone, and register exactly. Do not rephrase for style."

[[profile]]
name = "formal"
hotkey = "ctrl+shift+f"
paste = true
temperature = 0.4
prompt = "Rewrite the text in a polished, professional register suitable for a workplace email or Teams message. Keep the original meaning and all specifics."

[[profile]]
name = "concise"
hotkey = "ctrl+shift+c"
paste = true
temperature = 0.3
prompt = "Shorten the text significantly while keeping every substantive point. Drop filler, hedges, and repetition. Do not drop facts, names, numbers, or asks."

[[profile]]
name = "bullets"
hotkey = "ctrl+shift+b"
paste = true
temperature = 0.3
prompt = "Convert the prose into a tight bulleted list. Each bullet is one idea. Keep every substantive point. Use a hyphen-plus-space bullet marker."

[[profile]]
name = "reply"
hotkey = "ctrl+shift+r"
paste = true
temperature = 0.5
prompt = "Draft a short, professional reply to the selected message. Match the sender's language. Do not invent facts, commitments, or availability that the input does not support."
```

Saving `config.toml` causes the running process to validate and reload it automatically.

## 9. API key storage

Never place the Anthropic API key in source code, `config.toml`, a build script, a ZIP, or a log.

Primary storage:

```text
Windows Credential Manager
Service: Pedanticai
Username: anthropic_api_key
```

Backward-compatible service alias:

```text
clipai
```

Environment fallback:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

On first launch, if no key is found, Pedantic displays a key-entry dialog, validates the key with the Anthropic API, and stores it through `keyring`.

## 10. Windows network portability

The production Anthropic client is created in `clipai/backends/anthropic_api.py`.

On Windows it:

1. Creates an SSL context from the Windows trusted-certificate stores.
2. Includes organization-installed certificate authorities used by TLS-inspecting corporate proxies.
3. Reads HTTPS/HTTP proxy settings through Python's Windows-aware proxy discovery.
4. Gives an explicitly detected HTTPS proxy priority through an `httpx2` transport mount.
5. Retains environment-proxy support.
6. Uses the configured timeout and application-level retry policy.

Error categories are kept distinct:

- Timeout: suggests checking network or proxy and retrying.
- Certificate failure: suggests trusting the corporate certificate in Windows.
- Proxy failure: suggests checking Windows proxy settings.
- HTTP 401: API key rejected.
- HTTP 400: request error; not retried.
- HTTP 429, 500, and 529: retried.

The default application retry configuration is three attempts. Retries use exponential backoff starting at one second and honor `Retry-After` when the server supplies it.

## 11. Run from source

With the virtual environment active:

```powershell
python -m clipai
```

Useful commands:

```powershell
python -m clipai --version
python -m clipai --print-config-path
python -m clipai --usage-summary
python -m clipai --debug
```

`--debug` can log captured work text. Do not use it when logs may be shared externally.

Only one Pedantic instance is allowed per user/configuration directory.

## 12. Test before packaging

Run the normal unit test suite:

```powershell
python -m pytest
```

Expected current result:

```text
64 passed, 3 deselected
```

The default pytest configuration excludes tests marked:

```text
integration
live_api
```

Those tests may open real editor windows or call the paid Anthropic API.

Compile Python modules to catch syntax errors:

```powershell
python -m compileall -q clipai
```

Do not package a release if either command fails.

## 13. PyInstaller build

The PyInstaller definition is `clipai.spec`.

Important settings:

- Entry point: `clipai/__main__.py`
- Output name: `Pedantic`
- One-file executable
- Windowed mode: `console=False`
- UPX requested: `upx=True`
- Runtime hook: `clipai/pyi_rth_frozen.py`
- Explicit hidden imports for tray, keyring, Anthropic, `httpx2`, `httpcore2`, certificates, and Win32 modules

Standard build:

```powershell
python -m PyInstaller clipai.spec --noconfirm --clean
```

Output:

```text
dist\Pedantic.exe
```

If OneDrive, Defender, or a running process locks `build` or `dist\Pedantic.exe`, use temporary build and output directories:

```powershell
$Version = "0.1.2"
$Work = Join-Path $env:TEMP "pedantic-$Version-build"
$Output = Join-Path $env:TEMP "pedantic-$Version-dist"

python -m PyInstaller clipai.spec `
  --noconfirm `
  --clean `
  --workpath $Work `
  --distpath $Output

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed"
}

Copy-Item `
  (Join-Path $Output "Pedantic.exe") `
  "dist\Pedantic-$Version.exe" `
  -Force
```

## 14. Inno Setup installer build

The installer definition is:

```text
installer\Pedantic.iss
```

Before compiling, confirm that these definitions match the application version:

```iss
#define MyAppVersion "0.1.2"
#define MyAppExeName "Pedantic.exe"
#define MyAppSourceExeName "Pedantic-0.1.2.exe"
```

The source executable must exist:

```powershell
Test-Path "dist\Pedantic-0.1.2.exe"
```

Compile:

```powershell
$Iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    throw "Inno Setup 6 ISCC.exe was not found"
}

& $Iscc "installer\Pedantic.iss"

if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed"
}
```

Output:

```text
dist\Pedantic-Setup-0.1.2.exe
```

The installer places the runtime executable at:

```text
%LOCALAPPDATA%\Programs\Pedantic\Pedantic.exe
```

It can create Start Menu, desktop, and startup shortcuts without requiring administrator privileges.

## 15. One-command release build

The preferred release command is:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
```

The release script:

1. Reads the version from `clipai.__version__`.
2. Installs the project and build dependencies.
3. Runs the unit tests.
4. Builds `dist\Pedantic.exe`.
5. Copies it to `dist\Pedantic-<version>.exe`.
6. Creates a source archive while excluding generated and private runtime files.
7. Builds the Inno Setup installer if `ISCC.exe` is available.
8. Builds a portable ZIP containing the executable and install instructions.
9. Lists the release artifacts.

If the unversioned `dist\Pedantic.exe` is running or locked, exit Pedantic from the tray and wait for OneDrive/Defender scanning to complete. If the lock remains, use the temporary-directory PyInstaller procedure in section 13.

## 16. Portable package

The portable package contains:

```text
Pedantic.exe
INSTALL.txt
Install-Pedantic.ps1
```

The user can run `Pedantic.exe` directly or install for the current user:

```powershell
powershell -ExecutionPolicy Bypass -File Install-Pedantic.ps1
```

Optional shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File Install-Pedantic.ps1 `
  -DesktopShortcut `
  -StartWithWindows
```

## 17. Source archive procedure

The source archive must exclude build output, caches, logs, runtime state, and secrets.

The release script excludes:

```text
.venv
venv
dist
build
.pytest_cache
.mypy_cache
__pycache__
clipai.egg-info
.git
debug-*.log
clipai.lock
*.pyc
```

After creating a source ZIP, verify the version inside its `pyproject.toml`; do not rely only on the ZIP filename.

## 18. Release verification checklist

Run:

```powershell
python -m pytest
python -m compileall -q clipai
python -m clipai --version
```

Verify artifacts:

```powershell
Get-Item `
  "dist\Pedantic-0.1.2.exe", `
  "dist\Pedantic-Setup-0.1.2.exe"
```

Smoke-test the frozen executable without starting the tray:

```powershell
$Process = Start-Process `
  -FilePath "dist\Pedantic-0.1.2.exe" `
  -ArgumentList "--version" `
  -PassThru `
  -Wait

if ($Process.ExitCode -ne 0) {
    throw "Frozen executable smoke test failed"
}
```

Manual functional test:

1. Exit all older Pedantic instances.
2. Start the newly built executable.
3. Confirm one green `P` tray icon appears.
4. Open Notepad.
5. Type a misspelled sentence.
6. Select the sentence.
7. Press `Ctrl+Shift+G`.
8. Confirm the selection is copied automatically.
9. Confirm the rewritten result replaces the selection.
10. Confirm the original clipboard content is restored after pasting.
11. Repeat from another application such as Outlook, Teams, Chrome, or Notepad++.
12. Inspect `%APPDATA%\clipai\clipai.log`.

## 19. Runtime troubleshooting

### No tray icon

Check:

```text
%APPDATA%\clipai\clipai.log
```

Ensure another Pedantic instance is not running.

### “Could not copy selected text”

This means no usable clipboard change was detected after simulated `Ctrl+C`. It is not an API request failure.

Check:

1. Text was selected.
2. The foreground application accepts `Ctrl+C`.
3. Pedantic and the target application run at the same privilege level.
4. The target application is not running as Administrator while Pedantic is non-elevated.
5. Clipboard security software is not delaying or blocking access.

Increase this value if clipboard updates are slow:

```toml
clipboard_poll_timeout_ms = 1500
```

### “AI request timed out”

Check:

```toml
timeout_seconds = 30
max_attempts = 3
```

Test endpoint reachability:

```powershell
curl.exe --max-time 20 -sS -o NUL -w "%{http_code} %{time_total}s" `
  https://api.anthropic.com/v1/messages
```

An unauthenticated `405` response confirms DNS, TCP, and TLS connectivity to the endpoint.

### Certificate failure

Corporate TLS inspection may require an organization certificate in the Windows trusted root store. Pedantic uses the Windows trust store; the certificate must be trusted for the current Windows user or local computer.

### Proxy failure

Check Windows proxy configuration:

```powershell
netsh winhttp show proxy
```

Also check:

```powershell
Get-ChildItem Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:NO_PROXY -ErrorAction SilentlyContinue
```

### API key rejected

Open Windows Credential Manager and inspect the `Pedanticai` generic credential for username `anthropic_api_key`. Alternatively set `ANTHROPIC_API_KEY`.

### Hotkey ignored

The log message `transform already in flight` means a previous request is still running. Pedantic intentionally allows only one transformation at a time.

### Clipboard limitations

Only plain Unicode text is saved and restored. Images, files, and rich clipboard formats are not reconstructed.

### Elevated applications

Windows normally blocks a non-elevated process from injecting input into an elevated process. Run both applications at the same integrity level.

## 20. Logging and privacy

Default log:

```text
%APPDATA%\clipai\clipai.log
```

Normal logging stores operational information and short text previews. Debug logging may include captured input.

Anthropic-looking keys matching `sk-ant-...` are redacted by `clipai/credentials.py` and the logging setup. This is defense in depth, not permission to log or distribute secrets.

Before sharing a source archive or logs:

1. Confirm no `.env` file is included.
2. Confirm no API key appears.
3. Exclude `clipai.log`, `history.json`, and usage files.
4. Exclude debug logs containing work content.

## 21. Code-signing limitation

The current executable and installer are unsigned. `AppPublisher=Pedantic` in the Inno Setup script is display metadata; it does not create an Authenticode signature and does not make Windows trust the publisher.

To remove “Unknown publisher” warnings, obtain a trusted Windows code-signing certificate and sign:

1. `Pedantic-<version>.exe`
2. `Pedantic-Setup-<version>.exe`

Use SHA-256 and an RFC 3161 timestamp server. Never commit a `.pfx` file or certificate password to the repository.

Without a trusted signature, Microsoft Defender SmartScreen or enterprise application-control policy may warn or block the executable even when the build is correct.

## 22. Safely incrementing the version

For a patch release, for example `0.1.2` to `0.1.3`:

1. Update `clipai/__init__.py`.
2. Update `pyproject.toml`.
3. Update `installer/Pedantic.iss`.
4. Update the fallback in `scripts/build-release.ps1`.
5. Update release artifact references in `README.md`.
6. Update this guide.
7. Run all tests.
8. Build the versioned executable.
9. Build the versioned installer.
10. Verify both artifacts.

Search for stale versions:

```powershell
rg "0\.1\.2" .
```

Do not replace version-like strings blindly in logs, history, generated output, or archived releases.

## 23. Minimal reconstruction summary

From a clean Windows machine with the source:

```powershell
Set-Location "C:\path\to\Pedantic"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
python -m pip install -e ".[dev]"
python -m pytest
powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
```

Then verify:

```powershell
Get-ChildItem "dist"
```

The functional release is complete when:

- All unit tests pass.
- The versioned portable EXE exists.
- The versioned setup EXE exists.
- The frozen EXE exits successfully for `--version`.
- A manual selected-text hotkey test succeeds.
- No API key or private runtime data is present in the source or portable archives.