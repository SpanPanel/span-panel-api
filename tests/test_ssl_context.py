"""SSL context construction for the panel's private CA.

SPAN panels serve a minimal self-signed CA that omits the Authority Key
Identifier (AKI) X.509v3 extension. Python 3.13 turned on
``VERIFY_X509_STRICT`` by default, and that flag rejects such certificates
with "Missing Authority Key Identifier" — which broke MQTTS against
perfectly healthy panels. These tests pin the behaviour so the regression
cannot come back silently.
"""

from __future__ import annotations

import contextlib
import datetime
import socket
import ssl
import tempfile
import threading
from pathlib import Path

import pytest

from span_panel_api._ssl import build_panel_ssl_context, leaf_names_host

cryptography = pytest.importorskip("cryptography", reason="cryptography needed to mint a test CA")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402


def _self_signed_ca(*, with_aki: bool) -> str:
    """Mint a self-signed CA, mirroring what the panel serves.

    With ``with_aki=False`` the certificate omits the Authority Key
    Identifier extension, exactly like the SPAN panel's CA.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SPAN.io"),
            x509.NameAttribute(NameOID.COMMON_NAME, "test-panel CA"),
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


def _ca_and_leaf(*, with_aki: bool) -> tuple[str, str, str]:
    """Mint a CA plus a localhost leaf signed by it.

    Returns ``(ca_pem, leaf_pem, leaf_key_pem)``. When ``with_aki`` is False
    neither certificate carries an Authority Key Identifier, reproducing the
    chain a SPAN panel actually presents.
    """
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-panel CA")])
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

    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if with_aki:
        leaf_builder = leaf_builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
    leaf_cert = leaf_builder.sign(ca_key, hashes.SHA256())

    return (
        ca_cert.public_bytes(serialization.Encoding.PEM).decode(),
        leaf_cert.public_bytes(serialization.Encoding.PEM).decode(),
        leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode(),
    )


@contextlib.contextmanager
def _tls_server(leaf_pem: str, leaf_key_pem: str):
    """Run a throwaway TLS server on localhost, yielding ``(host, port)``."""
    with tempfile.TemporaryDirectory() as tmp:
        cert_path = Path(tmp) / "leaf.pem"
        key_path = Path(tmp) / "leaf.key"
        cert_path.write_text(leaf_pem)
        key_path.write_text(leaf_key_pem)

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def _serve() -> None:
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                with server_ctx.wrap_socket(raw, server_side=True):
                    pass
            except OSError:
                pass
            finally:
                raw.close()

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()
        finally:
            listener.close()
            thread.join(timeout=5)


class TestBuildSslContext:
    def test_loads_ca_without_authority_key_identifier(self) -> None:
        """The panel's AKI-less CA must load — this is the actual regression."""
        ctx = build_panel_ssl_context(_self_signed_ca(with_aki=False))

        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
        assert ctx.get_ca_certs(), "panel CA should be installed as a trust anchor"

    def test_handshake_succeeds_against_panel_style_cert(self) -> None:
        """End-to-end proof: a TLS handshake completes against an AKI-less chain."""
        ca_pem, leaf_pem, leaf_key_pem = _ca_and_leaf(with_aki=False)
        ctx = build_panel_ssl_context(ca_pem)

        with _tls_server(leaf_pem, leaf_key_pem) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname="localhost") as tls:
                    assert tls.getpeercert() is not None

    def test_strict_x509_would_reject_the_panel_chain(self) -> None:
        """Guard the premise: with VERIFY_X509_STRICT the same handshake fails.

        The AKI requirement is enforced during chain building at handshake
        time, not when the CA is loaded. If a future Python stops rejecting
        AKI-less chains this test fails loudly, telling us the workaround is
        no longer needed.
        """
        ca_pem, leaf_pem, leaf_key_pem = _ca_and_leaf(with_aki=False)
        strict = build_panel_ssl_context(ca_pem)
        strict.verify_flags |= ssl.VERIFY_X509_STRICT

        with _tls_server(leaf_pem, leaf_key_pem) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with pytest.raises(ssl.SSLCertVerificationError, match="Authority Key Identifier"):
                    strict.wrap_socket(raw, server_hostname="localhost")

    def test_strict_flag_is_cleared(self) -> None:
        ctx = build_panel_ssl_context(_self_signed_ca(with_aki=False))
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_hostname_and_peer_verification_stay_enabled(self) -> None:
        """Clearing the strict flag must not weaken the checks that matter."""
        ctx = build_panel_ssl_context(_self_signed_ca(with_aki=True))

        assert ctx.check_hostname is True
        assert ctx.verify_mode is ssl.CERT_REQUIRED

    def test_system_ca_bundle_is_not_trusted(self) -> None:
        """Only the panel CA is a trust anchor — no system roots."""
        ctx = build_panel_ssl_context(_self_signed_ca(with_aki=False))
        assert len(ctx.get_ca_certs()) == 1

    def test_conventional_ca_still_loads(self) -> None:
        ctx = build_panel_ssl_context(_self_signed_ca(with_aki=True))
        assert ctx.get_ca_certs()

    def test_malformed_pem_raises(self) -> None:
        with pytest.raises((ssl.SSLError, ValueError)):
            build_panel_ssl_context("-----BEGIN CERTIFICATE-----\nnot base64\n-----END CERTIFICATE-----\n")


class TestRelaxedHostnameContext:
    """`check_hostname=False` separates "is this the panel" from "is this its name".

    The scenario throughout is the one a DHCP move produces: a leaf that names
    `localhost` and nothing else, reached at `127.0.0.1`. The certificate is
    perfectly good and signed by the pinned anchor; only the name it was dialled
    by is absent from it.
    """

    def test_default_context_refuses_a_host_the_leaf_does_not_name(self) -> None:
        """The premise. With hostname checking on, the two failures look alike."""
        ca_pem, leaf_pem, leaf_key_pem = _ca_and_leaf(with_aki=False)
        ctx = build_panel_ssl_context(ca_pem)

        with _tls_server(leaf_pem, leaf_key_pem) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with pytest.raises(ssl.SSLCertVerificationError):
                    ctx.wrap_socket(raw, server_hostname="127.0.0.1")

    def test_relaxed_context_completes_and_yields_the_certificate(self) -> None:
        """The same connection succeeds, and hands back the leaf to judge."""
        ca_pem, leaf_pem, leaf_key_pem = _ca_and_leaf(with_aki=False)
        ctx = build_panel_ssl_context(ca_pem, check_hostname=False)

        with _tls_server(leaf_pem, leaf_key_pem) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                    peer = tls.getpeercert()

        assert peer is not None
        # Chain-valid, and demonstrably not named by the address it was reached
        # at -- the two facts the caller has to tell apart.
        assert leaf_names_host(peer, "localhost") is True
        assert leaf_names_host(peer, "127.0.0.1") is False

    def test_relaxed_context_still_rejects_an_untrusted_chain(self) -> None:
        """The load-bearing guarantee: relaxing the name does not relax trust.

        An attacker without a key the pinned CA signed must still fail, or the
        tri-state would be a hole rather than a classification.
        """
        ca_pem, _, _ = _ca_and_leaf(with_aki=False)
        _, other_leaf, other_key = _ca_and_leaf(with_aki=False)
        ctx = build_panel_ssl_context(ca_pem, check_hostname=False)

        with _tls_server(other_leaf, other_key) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with pytest.raises(ssl.SSLCertVerificationError):
                    ctx.wrap_socket(raw, server_hostname="localhost")

    def test_relaxed_context_keeps_peer_verification_required(self) -> None:
        ctx = build_panel_ssl_context(_self_signed_ca(with_aki=False), check_hostname=False)

        assert ctx.check_hostname is False
        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_default_is_unchanged(self) -> None:
        """Every existing caller keeps hostname checking without asking for it."""
        ctx = build_panel_ssl_context(_self_signed_ca(with_aki=False))
        assert ctx.check_hostname is True


class TestLeafNamesHost:
    def test_exact_dns_match(self) -> None:
        assert leaf_names_host({"subjectAltName": (("DNS", "panel.local"),)}, "panel.local")

    def test_dns_match_is_case_insensitive(self) -> None:
        assert leaf_names_host({"subjectAltName": (("DNS", "Panel.LOCAL"),)}, "panel.local")

    def test_trailing_dot_is_insignificant_on_both_sides(self) -> None:
        assert leaf_names_host({"subjectAltName": (("DNS", "panel.local."),)}, "panel.local")
        assert leaf_names_host({"subjectAltName": (("DNS", "panel.local"),)}, "panel.local.")

    def test_wildcards_are_not_matched(self) -> None:
        """A panel names literal addresses; a wildcard would be an anomaly."""
        assert not leaf_names_host({"subjectAltName": (("DNS", "*.local"),)}, "panel.local")

    def test_ipv4_match(self) -> None:
        assert leaf_names_host({"subjectAltName": (("IP Address", "10.0.0.5"),)}, "10.0.0.5")

    def test_ipv6_compares_parsed_not_textually(self) -> None:
        san = {"subjectAltName": (("IP Address", "0:0:0:0:0:0:0:1"),)}
        assert leaf_names_host(san, "::1")

    def test_ip_and_dns_entries_are_not_interchangeable(self) -> None:
        """Naming the string in a DNS entry must not authorise the address."""
        assert not leaf_names_host({"subjectAltName": (("DNS", "10.0.0.5"),)}, "10.0.0.5")
        assert not leaf_names_host({"subjectAltName": (("IP Address", "10.0.0.5"),)}, "panel.local")

    def test_no_common_name_fallback(self) -> None:
        """A SAN-less certificate names nothing, whatever its subject says."""
        peer = {"subject": ((("commonName", "panel.local"),),)}
        assert not leaf_names_host(peer, "panel.local")

    def test_absent_or_unreadable_san_is_not_a_match(self) -> None:
        assert not leaf_names_host({}, "panel.local")
        assert not leaf_names_host({"subjectAltName": "not-a-sequence"}, "panel.local")

    def test_malformed_entries_are_skipped_not_fatal(self) -> None:
        """A good entry beside a broken one still matches."""
        peer = {
            "subjectAltName": (
                ("DNS",),
                ("IP Address", "not-an-ip"),
                (None, "panel.local"),
                ("DNS", "panel.local"),
            )
        }
        assert leaf_names_host(peer, "panel.local")

    def test_unparseable_ip_entry_does_not_match(self) -> None:
        assert not leaf_names_host({"subjectAltName": (("IP Address", "999.1.1.1"),)}, "10.0.0.5")

    def test_empty_host_matches_nothing(self) -> None:
        assert not leaf_names_host({"subjectAltName": (("DNS", "panel.local"),)}, "")
