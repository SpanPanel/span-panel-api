"""The panel's trust anchor: building a context from it, and naming it.

Both functions here take a CA in PEM form and nothing else. They make no network
call and hold no state, which is the point -- a trust anchor that is fetched at
the moment it is used is not an anchor, it is whatever answered. The fetching
lives in ``auth.download_ca_cert``, and deciding whether a fetched PEM may be
trusted lives with the caller.

Public rather than private (``_ssl`` is a module-name convention here, and both
names are re-exported from the package root) because the consumer needs exactly
these two: it builds the same context for its own HTTPS calls, and it prints and
compares the same fingerprint string. Two implementations of a fingerprint that
must agree byte-for-byte is a defect waiting for a firmware upgrade to find it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ssl

from .exceptions import SpanPanelValidationError

_PEM_HEADER = "-----BEGIN CERTIFICATE-----"
_PEM_FOOTER = "-----END CERTIFICATE-----"


def build_panel_ssl_context(ca_pem: str) -> ssl.SSLContext:
    """Build an SSLContext that trusts only the provided panel CA.

    The panel issues a private CA and a server cert signed by it. We do
    not want to trust system CAs for this connection, so the context is
    built fresh rather than via ``ssl.create_default_context()``.

    The panel's CA is a minimal self-signed certificate that omits the
    Authority Key Identifier (AKI) X.509v3 extension. Python 3.13 enabled
    ``VERIFY_X509_STRICT`` by default, and that flag rejects such a
    certificate with "Missing Authority Key Identifier", which makes the
    MQTTS handshake fail on otherwise healthy panels. The flag is cleared
    here so the library keeps working across Python versions.

    This does not weaken the parts of verification that matter for this
    connection: the trust anchor is still only the panel's own CA, hostname
    checking stays enabled, and signature/expiry validation is unchanged.

    Raises:
        ssl.SSLError: ``ca_pem`` is not a certificate the ssl module accepts.
        ValueError: ``ca_pem`` is malformed in a way ``ssl`` reports as such.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    ctx.load_verify_locations(cadata=ca_pem)
    return ctx


def ca_fingerprint(ca_pem: str) -> str:
    """SHA-256 over the certificate's DER bytes, lowercase hex, no separators.

    The identity of a trust anchor, in a form a user can compare by eye against
    what the panel's label or another install reports, and a consumer can store
    in a config entry.

    Taken over the DER rather than over the PEM text on purpose. PEM is a
    presentation of the same bytes -- line width, line endings, surrounding
    blank lines and any explanatory text a firmware chooses to put above the
    header all vary without the certificate changing -- so a hash of the text
    would report a rotation that did not happen. That is the worse error of the
    two available: an integration that raises "your panel's CA changed" every
    time a firmware reflows its PEM teaches its users to dismiss the one time it
    matters.

    Only the first certificate in the PEM is read. The panel serves a single
    self-signed CA; if a future firmware appends a chain, the anchor is still the
    first element, and silently hashing a concatenation would change the
    fingerprint of an unchanged anchor.

    Raises:
        SpanPanelValidationError: no certificate block, or one whose body is not
            valid base64. Distinct from an ``ssl`` error because nothing has been
            asked of ``ssl`` yet -- this is a malformed input, and the caller
            handling it has a different remedy from one whose certificate is
            well-formed and unacceptable.
    """
    start = ca_pem.find(_PEM_HEADER)
    if start == -1:
        raise SpanPanelValidationError("No PEM certificate block found; cannot fingerprint the CA")
    body_start = start + len(_PEM_HEADER)
    end = ca_pem.find(_PEM_FOOTER, body_start)
    if end == -1:
        raise SpanPanelValidationError("PEM certificate block is not terminated; cannot fingerprint the CA")

    # Every run of whitespace is dropped rather than only line breaks, so a PEM
    # reflowed, re-indented or converted to CRLF fingerprints identically.
    body = "".join(ca_pem[body_start:end].split())
    try:
        der = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SpanPanelValidationError("PEM certificate body is not valid base64; cannot fingerprint the CA") from exc
    if not der:
        raise SpanPanelValidationError("PEM certificate block is empty; cannot fingerprint the CA")
    return hashlib.sha256(der).hexdigest()
