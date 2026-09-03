"""PyInstaller runtime-hook entry point.

PyInstaller executes this file before the application's entry point, which is
the only moment early enough to fix certificate and keyring discovery for every
library that later reads them at import time.

The hook must never abort startup: a failure here should degrade into a normal
runtime error with a log entry, not a silent executable that exits before it
can report anything.
"""

import os
import sys


def _configure() -> None:
    try:
        from clipai.runtime_frozen import configure

        configure()
        return
    except Exception:
        pass

    # Fallback if the package is not importable this early: do the one fix that
    # matters most, so HTTPS still works.
    try:
        base = getattr(sys, "_MEIPASS", None)
        if not base:
            return
        for candidate in (
            os.path.join(base, "certifi", "cacert.pem"),
            os.path.join(base, "cacert.pem"),
        ):
            if os.path.isfile(candidate):
                for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
                    os.environ.setdefault(name, candidate)
                break
    except Exception:
        pass


_configure()
