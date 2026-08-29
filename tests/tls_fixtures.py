"""Minting a panel-shaped certificate chain, and serving one over TLS.

Shared rather than per-module because more than one suite now needs a *real*
handshake to say anything: `test_ssl_context.py` proves the context and the SAN
matcher behave, and `test_leaf_name_mismatch.py` proves the transport draws the
right conclusion from what a live peer serves. Two implementations of "mint a CA
and stand a server up on it" would drift, and the certificates are the premise of
both -- a second, subtly different chain would leave one suite testing something
the other does not.

Everything here is a throwaway: keys are generated per call, the server binds an
ephemeral port on the loopback, and nothing is written outside a temporary
directory that goes away with the context manager.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
import contextlib
from dataclasses import dataclass
import datetime
import ipaddress
from pathlib import Path
import socket
import ssl
import tempfile
import threading

import pytest

pytest.importorskip("cryptography", reason="cryptography needed to mint a test CA")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

_CA_COMMON_NAME = "test-panel CA"


@dataclass(frozen=True)
class Chain:
    """A CA and one leaf signed by it, all in PEM form."""

    ca_pem: str
    leaf_pem: str
    leaf_key_pem: str


def mint_ca(*, with_aki: bool) -> str:
    """Mint a self-signed CA, mirroring what the panel serves.

    With ``with_aki=False`` the certificate omits the Authority Key Identifier
    extension, exactly like the SPAN panel's CA.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SPAN.io"),
            x509.NameAttribute(NameOID.COMMON_NAME, _CA_COMMON_NAME),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if with_aki:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )

    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _san(names: Sequence[str]) -> x509.SubjectAlternativeName:
    """Build a SAN from plain strings, in the order given.

    An entry that parses as an address becomes an ``IP Address`` and everything
    else becomes a ``DNS`` name, which is the split the panel's own template
    makes and the one `leaf_names_host` refuses to blur. Order is preserved
    because a caller asserting on what a certificate reports back is asserting on
    certificate order.
    """
    entries: list[x509.GeneralName] = []
    for name in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    return x509.SubjectAlternativeName(entries)


def mint_chain(*, names: Sequence[str] = ("localhost",), with_aki: bool = False, expired: bool = False) -> Chain:
    """Mint a CA plus a leaf signed by it that names ``names``.

    ``with_aki=False`` reproduces the chain a SPAN panel actually presents:
    neither certificate carries an Authority Key Identifier.

    ``expired=True`` back-dates the leaf's validity window so the chain is
    rejected under its own CA -- the panel whose clock reset after a power cut,
    which is one of the two failures a pinned handshake cannot tell apart on its
    own. The CA stays valid, so what fails is the leaf and only the leaf.

    An empty ``names`` omits the SAN extension entirely rather than adding an
    empty one, because "a certificate that names nothing" is the case worth
    reproducing and that is the shape it takes in the field.
    """
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _CA_COMMON_NAME)])
    now = datetime.datetime.now(datetime.timezone.utc)

    ca_builder = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    )
    if with_aki:
        ca_builder = ca_builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
    ca_cert = ca_builder.sign(ca_key, hashes.SHA256())

    leaf_from = now - datetime.timedelta(days=30) if expired else now - datetime.timedelta(days=1)
    leaf_until = now - datetime.timedelta(days=1) if expired else now + datetime.timedelta(days=365)

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else "unnamed")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(leaf_from)
        .not_valid_after(leaf_until)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if names:
        leaf_builder = leaf_builder.add_extension(_san(names), critical=False)
    if with_aki:
        leaf_builder = leaf_builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
    leaf_cert = leaf_builder.sign(ca_key, hashes.SHA256())

    return Chain(
        ca_pem=ca_cert.public_bytes(serialization.Encoding.PEM).decode(),
        leaf_pem=leaf_cert.public_bytes(serialization.Encoding.PEM).decode(),
        leaf_key_pem=leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode(),
    )


@contextlib.contextmanager
def tls_server(chain: Chain) -> Iterator[tuple[str, int]]:
    """Serve ``chain``'s leaf on the loopback until the block exits.

    Yields ``(host, port)``. Accepts repeatedly rather than once, because a
    caller under test may handshake several times -- a reconnect loop that
    diagnoses on every backoff tick does exactly that -- and a server that served
    a single connection would make the second attempt look like a panel that had
    gone away, which is a different verdict entirely.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cert_path = Path(tmp) / "leaf.pem"
        key_path = Path(tmp) / "leaf.key"
        cert_path.write_text(chain.leaf_pem)
        key_path.write_text(chain.leaf_key_pem)

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        # Short, and looped: the accept has to wake often enough to notice the
        # stop flag, without putting a ceiling on how long the block may run.
        listener.settimeout(0.25)
        stop = threading.Event()

        def _serve() -> None:
            while not stop.is_set():
                try:
                    raw, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    return
                try:
                    with server_ctx.wrap_socket(raw, server_side=True):
                        pass
                except OSError:
                    # A client that rejects the certificate aborts the
                    # handshake, which is the point of several of these tests.
                    pass
                finally:
                    raw.close()

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()
        finally:
            stop.set()
            listener.close()
            thread.join(timeout=5)


@contextlib.contextmanager
def closed_port() -> Iterator[tuple[str, int]]:
    """Yield a loopback ``(host, port)`` that nothing is listening on."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    address = probe.getsockname()
    probe.close()
    yield address
