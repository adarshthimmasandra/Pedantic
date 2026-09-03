# Pedantic

Pedantic is an always-running Windows system-tray application that rewrites the
text you have selected in any application. Select text, press a global hotkey
such as `Ctrl+Shift+G`, and the selection is replaced by a cleaned-up version
produced by Anthropic Claude Haiku.

Current release: **0.1.2**

## How it works

1. A global hotkey is detected and suppressed so the foreground application
   never sees it.
2. Pedantic waits for the physical modifiers to be released, then simulates a
   clean `Ctrl+C` with the Windows `SendInput` API.
3. It waits for the clipboard sequence number to change and reads plain Unicode
   text.
4. The text is validated, sanitized, and sent to the configured Claude model.
5. The response is cleaned, placed on the clipboard, and pasted with `Ctrl+V`.
6. The clipboard content that existed before the hotkey is restored.

Only one transformation runs at a time.

## Profiles

Each profile has its own hotkey, prompt, temperature, and paste behavior. The
defaults are:

| Profile   | Hotkey         | Purpose                                  |
| --------- | -------------- | ---------------------------------------- |
| `grammar` | `Ctrl+Shift+G` | Fix spelling, grammar, punctuation only  |
| `formal`  | `Ctrl+Shift+F` | Rewrite in a professional register       |
| `concise` | `Ctrl+Shift+C` | Shorten while keeping every point        |
| `bullets` | `Ctrl+Shift+B` | Convert prose into a bulleted list       |
| `reply`   | `Ctrl+Shift+R` | Draft a short professional reply         |

## Install

Use the installer, which installs for the current user and needs no
administrator privileges:

```text
dist\Pedantic-Setup-0.1.2.exe
```

It places the runtime executable at:

```text
%LOCALAPPDATA%\Programs\Pedantic\Pedantic.exe
```

Alternatively, unpack `Pedantic-0.1.2-portable.zip` and either run
`Pedantic.exe` directly or install it for the current user:

```powershell
powershell -ExecutionPolicy Bypass -File Install-Pedantic.ps1 -DesktopShortcut -StartWithWindows
```

## API key

Pedantic never stores the Anthropic API key in source, configuration, or logs.
On first launch it asks for the key, validates it against the Anthropic API,
and stores it through `keyring`:

```text
Windows Credential Manager
Service: Pedanticai
Username: anthropic_api_key
```

The service name `clipai` is still read for backward compatibility, and
`ANTHROPIC_API_KEY` is honored as a fallback.

## Configuration

Per-user data lives under `%APPDATA%\clipai`:

```text
%APPDATA%\clipai\config.toml
%APPDATA%\clipai\clipai.log
%APPDATA%\clipai\history.json
%APPDATA%\clipai\usage-YYYY-MM.jsonl
%APPDATA%\clipai\clipai.lock
```

`config.toml` is created only when it does not already exist, so upgrading the
executable never overwrites your settings. Saving the file makes the running
process validate and reload it automatically.

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools
python -m pip install -e ".[dev]"
python -m clipai
```

Useful commands:

```powershell
python -m clipai --version
python -m clipai --print-config-path
python -m clipai --usage-summary
python -m clipai --debug
```

`--debug` can log captured work text; do not use it when logs may be shared.

## Build a release

```powershell
python -m pytest
powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
```

Release artifacts:

```text
dist\Pedantic.exe
dist\Pedantic-0.1.2.exe
dist\Pedantic-Setup-0.1.2.exe
dist\Pedantic-0.1.2-portable.zip
dist\Pedantic-0.1.2-source.zip
dist\CHECKSUMS.txt
```

`CHECKSUMS.txt` holds SHA-256 hashes for the downloadable artifacts. Publish it
alongside them so people can confirm a download arrived intact:

```powershell
Get-FileHash .\Pedantic-Setup-0.1.2.exe -Algorithm SHA256
```

The executable and installer are unsigned, so Windows may show an "Unknown
publisher" warning.

`detailed.md` documents the full reconstruction, packaging, and troubleshooting
procedure.
