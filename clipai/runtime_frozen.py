"""Frozen-runtime certificate and keyring setup.

Two things that work from source break inside a PyInstaller one-file build, and
both are fixed here before any application code runs.

*Certificates.* ``certifi`` locates its CA bundle relative to its own module
file. Inside the bundle that path exists only while the executable is
unpacked, and libraries that read ``SSL_CERT_FILE`` from the environment find
nothing at all. The bundled ``cacert.pem`` is therefore located and exported
explicitly.

*Keyring.* ``keyring`` discovers backends through package entry-point metadata,
which PyInstaller does not reproduce faithfully. Without help it reports that no
recommended backend is available and the API key cannot be read, so the Windows
credential backend is selected directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

CERT_ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))


def find_certificate_bundle() -> Path | None:
    """Locate the CA bundle inside the frozen application."""
    candidates = [
        bundle_dir() / "certifi" / "cacert.pem",
        bundle_dir() / "cacert.pem",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    try:
        import certifi

        path = Path(certifi.where())
        if path.is_file():
            return path
    except Exception:
        pass
    return None


def configure_certificates() -> Path | None:
    """Export the bundled CA bundle through the standard environment variables."""
    bundle = find_certificate_bundle()
    if bundle is None:
        return None
    for name in CERT_ENV_VARS:
        # An explicitly configured corporate bundle must win over ours.
        os.environ.setdefault(name, str(bundle))
    return bundle


def configure_keyring() -> bool:
    """Force the Windows credential backend. Returns True on success."""
    if sys.platform != "win32":
        return False
    try:
        import keyring
        from keyring.backends import Windows

        if not Windows.WinVaultKeyring.priority:  # pragma: no cover
            return False
        keyring.set_keyring(Windows.WinVaultKeyring())
        return True
    except Exception:
        return False


def configure() -> None:
    """Run every frozen-runtime fix. Safe to call when not frozen."""
    configure_certificates()
    if is_frozen():
        configure_keyring()
