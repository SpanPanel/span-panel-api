"""SSL context construction for the panel's private CA.

SPAN panels serve a minimal self-signed CA that omits the Authority Key
Identifier (AKI) X.509v3 extension. Python 3.13 turned on
``VERIFY_X509_STRICT`` by default, and that flag rejects such certificates
with "Missing Authority Key Identifier" — which broke MQTTS against
perfectly healthy panels. These tests pin the behaviour so the regression
cannot come back silently.

They also cover the two halves that a caller has to compose when a strict
handshake has already failed: a relaxed context, and the SAN matcher that then
decides the name on its own. ``probe_leaf_name`` is that composition, so its
outcomes are asserted here too, against a real server, rather than against a
mocked socket that could agree with a wrong implementation.
"""

from __future__ import annotations

import socket
import ssl

import pytest

from span_panel_api._ssl import LeafNameMismatch, build_panel_ssl_context, leaf_names_host, probe_leaf_name

from tls_fixtures import closed_port, mint_ca, mint_chain, tls_server

PROBE_TIMEOUT_S = 5.0


class TestBuildSslContext:
    def test_loads_ca_without_authority_key_identifier(self) -> None:
        """The panel's AKI-less CA must load — this is the actual regression."""
        ctx = build_panel_ssl_context(mint_ca(with_aki=False))

        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
        assert ctx.get_ca_certs(), "panel CA should be installed as a trust anchor"

    def test_handshake_succeeds_against_panel_style_cert(self) -> None:
        """End-to-end proof: a TLS handshake completes against an AKI-less chain."""
        chain = mint_chain(with_aki=False)
        ctx = build_panel_ssl_context(chain.ca_pem)

        with tls_server(chain) as (host, port):
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
        chain = mint_chain(with_aki=False)
        strict = build_panel_ssl_context(chain.ca_pem)
        strict.verify_flags |= ssl.VERIFY_X509_STRICT

        with tls_server(chain) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with pytest.raises(ssl.SSLCertVerificationError, match="Authority Key Identifier"):
                    strict.wrap_socket(raw, server_hostname="localhost")

    def test_strict_flag_is_cleared(self) -> None:
        ctx = build_panel_ssl_context(mint_ca(with_aki=False))
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_hostname_and_peer_verification_stay_enabled(self) -> None:
        """Clearing the strict flag must not weaken the checks that matter."""
        ctx = build_panel_ssl_context(mint_ca(with_aki=True))

        assert ctx.check_hostname is True
        assert ctx.verify_mode is ssl.CERT_REQUIRED

    def test_system_ca_bundle_is_not_trusted(self) -> None:
        """Only the panel CA is a trust anchor — no system roots."""
        ctx = build_panel_ssl_context(mint_ca(with_aki=False))
        assert len(ctx.get_ca_certs()) == 1

    def test_conventional_ca_still_loads(self) -> None:
        ctx = build_panel_ssl_context(mint_ca(with_aki=True))
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
        chain = mint_chain(with_aki=False)
        ctx = build_panel_ssl_context(chain.ca_pem)

        with tls_server(chain) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with pytest.raises(ssl.SSLCertVerificationError):
                    ctx.wrap_socket(raw, server_hostname="127.0.0.1")

    def test_relaxed_context_completes_and_yields_the_certificate(self) -> None:
        """The same connection succeeds, and hands back the leaf to judge."""
        chain = mint_chain(with_aki=False)
        ctx = build_panel_ssl_context(chain.ca_pem, check_hostname=False)

        with tls_server(chain) as (host, port):
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
        chain = mint_chain(with_aki=False)
        impostor = mint_chain(with_aki=False)
        ctx = build_panel_ssl_context(chain.ca_pem, check_hostname=False)

        with tls_server(impostor) as (host, port):
            with socket.create_connection((host, port), timeout=5) as raw:
                with pytest.raises(ssl.SSLCertVerificationError):
                    ctx.wrap_socket(raw, server_hostname="localhost")

    def test_relaxed_context_keeps_peer_verification_required(self) -> None:
        ctx = build_panel_ssl_context(mint_ca(with_aki=False), check_hostname=False)

        assert ctx.check_hostname is False
        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)

    def test_default_is_unchanged(self) -> None:
        """Every existing caller keeps hostname checking without asking for it."""
        ctx = build_panel_ssl_context(mint_ca(with_aki=False))
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


class TestProbeLeafName:
    """The composition: dial a peer under the pin, and judge the name separately.

    Every case here runs a real handshake against a real socket. A mocked one
    would happily agree with a wrong implementation, and the whole value of this
    function is that it reaches the point of holding a *validated* certificate --
    which is precisely the state a mock cannot reproduce honestly.
    """

    def test_a_leaf_that_omits_the_dialled_address_is_a_mismatch(self) -> None:
        """The DHCP move: a good certificate, reached at an address it never names."""
        chain = mint_chain(names=("panel.local", "10.0.0.5"))

        with tls_server(chain) as (host, port):
            probe = probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch == LeafNameMismatch(host="127.0.0.1", leaf_names=("panel.local", "10.0.0.5"))
        assert "does not name 127.0.0.1" in probe.detail

    def test_reported_names_keep_certificate_order_and_both_kinds(self) -> None:
        """A user reads these back and types one in, so they are verbatim and in order."""
        chain = mint_chain(names=("10.0.0.5", "panel.local", "panel.example.test"))

        with tls_server(chain) as (host, port):
            probe = probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch is not None
        assert probe.mismatch.leaf_names == ("10.0.0.5", "panel.local", "panel.example.test")

    def test_a_leaf_that_names_the_dialled_address_is_not_a_mismatch(self) -> None:
        """Cannot follow a failed strict handshake, and is a shrug rather than a verdict."""
        chain = mint_chain(names=("127.0.0.1",))

        with tls_server(chain) as (host, port):
            probe = probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch is None
        assert "does name 127.0.0.1" in probe.detail

    def test_an_expired_leaf_is_not_a_mismatch(self) -> None:
        """The other half of what a pinned handshake conflates, and the panel fixes it.

        The certificate here names the dialled address perfectly well. Only its
        validity window has passed, so the pin rejects it before there is any
        name to read -- and reporting a mismatch on that basis would send a user
        to change an address that is correct.
        """
        chain = mint_chain(names=("127.0.0.1",), expired=True)

        with tls_server(chain) as (host, port):
            probe = probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch is None
        assert "rejected too" in probe.detail
        assert "expired" in probe.detail

    def test_an_untrusted_chain_is_not_a_mismatch(self) -> None:
        """Relaxing the name does not relax trust, so an impostor is still rejected.

        Load-bearing: if this reported a name mismatch, the mismatch signal would
        be a way for something the pin does not trust to publish its own names to
        a user as addresses to move to.
        """
        chain = mint_chain()
        impostor = mint_chain(names=("panel.local",))

        with tls_server(impostor) as (host, port):
            probe = probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch is None
        assert "rejected too" in probe.detail

    def test_nothing_listening_is_not_a_mismatch(self) -> None:
        """A panel mid-reboot. Missing evidence is not evidence."""
        chain = mint_chain()

        with closed_port() as (host, port):
            probe = probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch is None
        assert "could not reach it" in probe.detail

    def test_an_unusable_host_is_not_a_mismatch(self) -> None:
        """An empty host is a configuration fault, and still not evidence of one."""
        chain = mint_chain()

        probe = probe_leaf_name(chain.ca_pem, "", 8883, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch is None
        assert "could not reach it" in probe.detail

    def test_a_leaf_naming_nothing_reports_an_empty_tuple(self) -> None:
        """A certificate with no SAN names no address, which is a panel problem."""
        chain = mint_chain(names=())

        with tls_server(chain) as (host, port):
            probe = probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert probe.mismatch is not None
        assert probe.mismatch.leaf_names == ()

    def test_the_anchor_is_never_replaced_by_what_the_peer_served(self) -> None:
        """The discipline the whole path is built on, asserted at its lowest level.

        A probe that saw a perfectly valid certificate from some other CA must
        leave the caller's anchor exactly as it found it -- the function returns
        a verdict, and there is no way for a certificate to travel back out of it.
        """
        chain = mint_chain()
        impostor = mint_chain(names=("127.0.0.1",))
        pinned_before = chain.ca_pem

        with tls_server(impostor) as (host, port):
            probe_leaf_name(chain.ca_pem, host, port, timeout=PROBE_TIMEOUT_S)

        assert chain.ca_pem == pinned_before
        assert impostor.ca_pem not in chain.ca_pem
