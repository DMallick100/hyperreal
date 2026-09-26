"""The one SSL context every HTTPS caller in this repo builds its requests from.

WHY THIS IS A MODULE AND NOT A PARAGRAPH IN EACH CALLER. `urllib.request.urlopen`
uses the system trust store unless it is handed a `context=`, and the system
python on this machine has none: every HTTPS call returned
`CERTIFICATE_VERIFY_FAILED` until an explicit context existed. That was recorded
as a lesson on 2026-09-23 ("build the SSL context from `certifi` when importable,
honour a `*_CA_BUNDLE` override, never ship an insecure-skip flag") - and
`measurements/provider_preflight.py` was then written **after** it and still
called `urlopen` with no `context=`, so a FREE catalogue GET reported the provider
unreachable when the defect was ours.

The second-order lesson is `CLAUDE.md` 8.A's: **a rule that lives only in a
lessons file is not a gate.** A paragraph each new file has to have read is the
same defect waiting for the next file. So the durable form is this: one helper,
imported, with the fix applied to the class rather than to a call site
(`CLAUDE.md` 8.0 #2 - fix it in the shared place, never at the call site).

There is deliberately **no insecure-skip flag**. A reachability tool that can be
told to stop verifying will be, and then every row it writes is unattributable.

The trust store is RETURNED ALONGSIDE the context and is meant to be printed.
"Verified against certifi" and "verified against whatever was lying around" are
different claims, and a caller that cannot say which one it made cannot defend a
reachability result in either direction.
"""

from __future__ import annotations

import os
import pathlib
import ssl

# Checked in this order. An operator-supplied bundle outranks `certifi` because
# the override exists for a machine whose CA set we do not control; `certifi`
# outranks the system default because on this interpreter the system default is
# empty and fails every call.
CA_BUNDLE_ENV_NAMES = ("HYPERREAL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")


def ssl_context() -> tuple[ssl.SSLContext, str]:
    """An SSL context on a trust store this machine actually has, plus its name.

    The third branch returns the system default and **reports it as such** rather
    than silently trusting it, so an HTTPS failure under that branch is legible as
    a fact about the interpreter rather than about the provider.
    """
    for name in CA_BUNDLE_ENV_NAMES:
        override = os.environ.get(name)
        if override and pathlib.Path(override).exists():
            return ssl.create_default_context(cafile=override), f"{name}={override}"
    try:
        import certifi
    except ImportError:
        return (
            ssl.create_default_context(),
            "system default - certifi NOT importable, so an HTTPS failure here is "
            "about this interpreter and not about the provider",
        )
    return ssl.create_default_context(cafile=certifi.where()), f"certifi {certifi.where()}"
