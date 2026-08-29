"""The panel's trust anchor: building a context from it, and naming it.

``build_panel_ssl_context``, ``leaf_names_host`` and ``ca_fingerprint`` take a CA
in PEM form and nothing else. They make no network call and hold no state, which
is the point -- a trust anchor that is fetched at the moment it is used is not an
anchor, it is whatever answered. The fetching lives in ``auth.download_ca_cert``,
and deciding whether a fetched PEM may be trusted lives with the caller.

Those three are public (``_ssl`` is a module-name convention here, and all three
are re-exported from the package root) because the consumer needs them: it builds
the same context for its own HTTPS calls, it prints and compares the same
fingerprint string, and it applies the same hostname rules when it has to judge a
name binding for itself. Two implementations of a fingerprint that must agree
byte-for-byte is a defect waiting for a firmware upgrade to find it, and the same
is true of a hand-written hostname matcher -- more so, since that one is
security-relevant and has no standard-library implementation left to defer to
since ``ssl.match_hostname`` was removed in Python 3.12.

``probe_leaf_name`` is the one thing here that does open a socket, and it is the
same argument carried one step further. A failed pinned handshake carries no
evidence about *why* it failed, so somebody has to ask the peer a second, narrower
question -- and that question is a composition of the anchor, the relaxed context
and the SAN matcher, all of which live in this module. Written once here rather
than at each caller for exactly the reason the matcher is: a second implementation
of "does this certificate name this host" is the drift the module exists to
prevent. It anchors on the CA it is handed and returns a verdict, never a
certificate to trust -- nothing it sees can become an anchor.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import hashlib
import ipaddress
import socket
import ssl

from .exceptions import SpanPanelValidationError

_PEM_HEADER = "-----BEGIN CERTIFICATE-----"
_PEM_FOOTER = "-----END CERTIFICATE-----"

#: The SAN entry kinds this library reads. A panel names literal addresses, so
#: these are the two that can carry one; anything else in a SAN (``email``, a
#: ``URI``) names something that is not a host and would only mislead a user
#: reading the list back.
_ADDRESSING_SAN_KINDS = ("DNS", "IP Address")


def build_panel_ssl_context(ca_pem: str, *, check_hostname: bool = True) -> ssl.SSLContext:
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
    checking stays enabled by default, and signature/expiry validation is
    unchanged.

    ``check_hostname=False`` asks a narrower question: *does the peer hold a
    private key whose certificate chains to this anchor?* The chain, the
    signature and the expiry are still verified -- only the binding between
    the certificate and the name used to dial it is left unasserted. That is
    a real distinction and not a relaxation of trust: an attacker without a
    CA-signed key cannot complete the handshake either way.

    It exists because the two failures are otherwise indistinguishable, and
    they call for opposite responses. A panel that has moved to a new DHCP
    lease serves a perfectly good certificate that no longer names the
    address it is reached at; something impersonating a panel serves one that
    chains to nothing. Collapsing both into "verification failed" tells a
    user their panel has been intercepted when its address merely changed.

    Never pass ``check_hostname=False`` for a connection that carries data.
    The name binding is what stops a validated certificate being replayed by
    a host it was not issued to, so a relaxed context belongs only in code
    that is deciding *which* host to talk to, paired with
    :func:`leaf_names_host` to establish the binding separately.

    Raises:
        ssl.SSLError: ``ca_pem`` is not a certificate the ssl module accepts.
        ValueError: ``ca_pem`` is malformed in a way ``ssl`` reports as such.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = check_hostname
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    ctx.load_verify_locations(cadata=ca_pem)
    return ctx


def leaf_names_host(peer_cert: Mapping[str, object], host: str) -> bool:
    """Whether a validated peer certificate names ``host`` in its SAN.

    The hostname half of what ``check_hostname=True`` does in one step, split
    out so a caller that built a relaxed context can still ask the question
    and act on the answer. ``peer_cert`` is what ``SSLSocket.getpeercert()``
    returns, which is populated only for a certificate the handshake already
    validated -- so this function decides naming, never trust.

    Hand-written because ``ssl.match_hostname`` was removed in Python 3.12
    and nothing replaced it as public API. The rules here are deliberately
    stricter than the ones it implemented, because a panel's leaf is
    machine-generated from a fixed template and needs none of the latitude a
    general-purpose matcher owes the public web:

    - **No wildcards.** ``*.example.com`` is not matched against anything. A
      panel names literal addresses, so a wildcard in one of its certificates
      would be an anomaly rather than a case to support.
    - **No ``commonName`` fallback.** Deprecated for two decades, and every
      certificate this library meets carries a SAN.
    - **IP and DNS entries are not interchangeable.** A host that parses as an
      IP address is matched only against ``IP Address`` entries and a name
      only against ``DNS`` entries, so a certificate naming the *string*
      "10.0.0.5" in a DNS entry does not authorise the address 10.0.0.5.
    - **Addresses compare parsed, names compare casefolded.** ``::1`` and
      ``0:0:0:0:0:0:0:1`` are one address; ``Panel.local`` and ``panel.local``
      are one name. A single trailing dot is insignificant on both sides.

    Returns False for anything it cannot read -- a certificate with no SAN, a
    malformed entry, an unparseable address. The caller's question is "may I
    treat this name as bound to this certificate", and the honest answer to a
    SAN that cannot be understood is no.
    """
    candidate = _without_root_dot(host)
    if not candidate:
        return False
    entries = list(_san_entries(peer_cert))
    try:
        wanted = ipaddress.ip_address(candidate)
    except ValueError:
        return _names_dns(entries, candidate)
    return _names_address(entries, wanted)


@dataclass(frozen=True, slots=True)
class LeafNameMismatch:
    """A peer whose certificate the pinned CA validates, and which does not name ``host``.

    The one thing that can be established about a failed pinned handshake beyond
    "something is wrong": the panel is who it says it is, and it is not where the
    configuration says it is. Not an exception, because it is not fatal and
    nothing is being refused -- the transport keeps retrying, and a DHCP lease
    that comes back or a panel that finishes registering its name fixes this with
    nobody's help. It is a fact reported to whoever asked to be told, so that a
    consumer can put the remedy in front of a person instead of leaving them to
    read a log.

    ``leaf_names`` is what the certificate actually carries -- its SAN ``DNS`` and
    ``IP Address`` entries, in certificate order -- because the remedy is to
    re-point the configuration at one of them, and a message that says only "the
    name is wrong" does not tell anyone what the right one is. Empty is possible
    and means the certificate names no address at all, which is a panel problem
    rather than an addressing one.
    """

    host: str
    leaf_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LeafProbe:
    """The result of one relaxed diagnostic handshake.

    ``mismatch`` is set for the single outcome that is actionable and is ``None``
    for every other, because every other one is transient and the caller's
    response to all of them is the same: keep retrying. ``detail`` says which,
    as a phrase fit to drop into a log line, so that a caller can be specific
    about a verdict it must not act on differently.
    """

    mismatch: LeafNameMismatch | None
    detail: str


def probe_leaf_name(ca_pem: str, host: str, port: int, *, timeout: float) -> LeafProbe:
    """Ask ``host`` directly whether the certificate it serves names ``host``.

    **Diagnostic only.** One handshake, under the CA it is handed, with hostname
    checking relaxed. Nothing it observes is stored, no context is built from it
    for any other use, and the anchor it verifies against is the caller's pin
    unchanged -- a peer cannot become trusted by answering this call. The chain,
    the signature and the expiry are all still verified, which is what makes the
    remaining question meaningful: a peer that gets as far as being *named
    wrongly* has already proved it holds a key the pin signed.

    Blocking, and deliberately so -- ``ssl`` offers no non-blocking handshake
    worth the machinery here, and the one caller has an executor. It is not
    exported from the package root for that reason: a blocking call on an async
    library's public surface is a footgun, and the consumer's own decisions about
    which host to talk to are made in a config flow that already composes
    :func:`build_panel_ssl_context` and :func:`leaf_names_host` for itself.

    Four outcomes, and only the last is not a shrug:

    - the peer rejects under the pin -- an expired leaf, most often a panel whose
      clock reset after a power cut, and nothing anyone can act on;
    - nothing answers -- a panel mid-reboot;
    - the certificate names ``host`` -- which cannot follow a strict handshake
      that failed, and is reported as transient rather than reasoned about,
      because a contradiction is not evidence of anything;
    - the certificate does not name ``host`` -- the mismatch.

    Args:
        ca_pem: The pinned CA, verified against and never replaced.
        host: The name to dial and the name to look for. Both, deliberately:
            the question is whether the peer reached *by this name* carries it.
        port: The port to dial.
        timeout: Seconds allowed for the connection and the handshake together.

    Raises:
        ssl.SSLError: ``ca_pem`` is not a certificate the ssl module accepts.
        ValueError: ``ca_pem`` is malformed in a way ``ssl`` reports as such.
    """
    context = build_panel_ssl_context(ca_pem, check_hostname=False)
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            peer = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        # Ahead of OSError because it is one: SSLCertVerificationError derives
        # from SSLError derives from OSError, and this is the branch that means
        # "the peer answered and the pin rejected it" rather than "nothing
        # answered".
        return LeafProbe(None, f"a second look with the hostname check relaxed was rejected too ({exc.verify_message})")
    except (OSError, ValueError) as exc:
        # Every remaining transport failure, including the non-verification TLS
        # errors: refused, unresolvable, timed out, a handshake that went wrong
        # for a reason the pin has no opinion about. ValueError because an empty
        # `host` is one, and an unusable configuration is still not evidence.
        return LeafProbe(None, f"a second look with the hostname check relaxed could not reach it ({exc})")
    if peer is None:
        # Only reachable with verification off, which this context never has.
        # Kept because the alternative is reading a mismatch out of an empty
        # certificate and naming no addresses in the report.
        return LeafProbe(None, "a second look with the hostname check relaxed produced no certificate to read")
    if leaf_names_host(peer, host):
        return LeafProbe(None, f"the certificate it serves does name {host}, so the failure was something else")
    return LeafProbe(
        LeafNameMismatch(host=host, leaf_names=_san_names(peer)),
        f"the certificate it serves does not name {host}",
    )


def _without_root_dot(name: str) -> str:
    """Strip surrounding space and a single root dot, which is not significant."""
    stripped = name.strip()
    return stripped[:-1] if stripped.endswith(".") else stripped


def _san_entries(peer_cert: Mapping[str, object]) -> Iterator[tuple[str, str]]:
    """Yield the readable ``(kind, value)`` pairs of a certificate's SAN.

    Anything malformed is skipped rather than rejected wholesale, so one broken
    entry cannot hide a good one sitting beside it.
    """
    san = peer_cert.get("subjectAltName")
    if not isinstance(san, tuple | list):
        return
    for entry in san:
        if not isinstance(entry, tuple | list) or len(entry) != 2:
            continue
        kind, value = entry
        if isinstance(kind, str) and isinstance(value, str):
            yield kind, value


def _san_names(peer_cert: Mapping[str, object]) -> tuple[str, ...]:
    """The addresses a certificate names, in certificate order.

    Verbatim, without normalisation: a user is going to read these back and type
    one of them into a configuration field, so what is reported has to be what
    the certificate says rather than a casefolded or dot-stripped rendering of
    it. Order is the certificate's because the first entry is conventionally the
    primary name, and re-sorting would lose that for nothing.
    """
    return tuple(value for kind, value in _san_entries(peer_cert) if kind in _ADDRESSING_SAN_KINDS)


def _names_address(entries: list[tuple[str, str]], wanted: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an ``IP Address`` entry denotes ``wanted``, compared as addresses."""
    for kind, value in entries:
        if kind != "IP Address":
            continue
        try:
            if ipaddress.ip_address(value.strip()) == wanted:
                return True
        except ValueError:
            continue
    return False


def _names_dns(entries: list[tuple[str, str]], candidate: str) -> bool:
    """Whether a ``DNS`` entry equals ``candidate``, casefolded and exact."""
    folded = candidate.casefold()
    for kind, value in entries:
        if kind != "DNS":
            continue
        named = _without_root_dot(value)
        if named and named.casefold() == folded:
            return True
    return False


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
