"""SPAN Panel API exceptions."""


class SpanPanelError(Exception):
    """Base exception for SPAN Panel API errors."""


class SpanPanelAuthError(SpanPanelError):
    """Authentication failed."""


class SpanPanelConnectionError(SpanPanelError):
    """Connection to SPAN panel failed."""


class SpanPanelTLSVerificationError(SpanPanelConnectionError):
    """Something answered a bootstrap REST call with a certificate the supplied anchor rejects.

    A subclass of `SpanPanelConnectionError` on purpose: every consumer that
    catches the parent and retries keeps doing exactly what it did, because
    nothing raised this before an `ssl_context` reached the bootstrap calls. The
    subclass exists for the consumer that wants the opposite of a retry — a
    verification failure is not "the panel is not up yet", it is "whatever is up
    does not hold a key the pin signs", and retrying that is waiting to succeed
    against whatever is answering. Catch this before the parent to fail closed.

    Raised only when the failure is demonstrably about verification — an
    `ssl.SSLCertVerificationError` in the cause chain. Every other transport
    failure, TLS handshakes that die for other reasons included, stays a plain
    `SpanPanelConnectionError`, because ambiguous evidence must not look
    terminal.
    """


class SpanPanelTimeoutError(SpanPanelError):
    """Request timed out."""


class SpanPanelValidationError(SpanPanelError):
    """Data validation failed."""


class SpanPanelAPIError(SpanPanelError):
    """General API error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpanPanelServerError(SpanPanelAPIError):
    """The panel answered, and the answer means "not ready yet".

    Any 5xx, and a 200 whose body cannot be a schema. Distinct from
    `SpanPanelAPIError` because a caller can retry this and should not retry a
    4xx, which will not fix itself. A rebooting panel produces these for as long
    as its front end is up and the application behind it is not.
    """


class SpanPanelCAChangedError(SpanPanelError):
    """The panel is presenting a certificate chain from a different CA than the pin.

    Terminal, and deliberately so. Every other connection failure in this library
    is retried, because every other one is something that fixes itself: a panel
    rebooting, a broker restarting, a network dropping. This one does not fix
    itself, and retrying it is the failure mode -- a client that keeps trying is
    a client waiting to succeed against whatever is answering, which is the
    outcome pinning exists to prevent.

    It is also not a conclusion drawn from the failed handshake, because that
    handshake cannot support one: an expired leaf (a panel whose clock reset
    after a power outage) and a hostname mismatch (a panel whose address moved)
    raise the same verification error against a perfectly valid pinned CA, and
    the ``ssl`` module exposes no peer chain when verification fails. This is
    raised only after a separate fetch of the panel's advertised CA returned a
    certificate whose fingerprint differs from the pinned one -- so
    ``observed_fingerprint`` is what the panel says its anchor is now, not what
    it presented on the connection that failed.

    The other two are told apart afterwards and elsewhere, by a *second*
    handshake with hostname checking relaxed (``_ssl.probe_leaf_name``), which
    reaches the point of holding a validated certificate and can therefore read
    its names. That path never produces this error: a leaf that chains to the pin
    has proved the panel is the panel, so the worst it can report is
    ``LeafNameMismatch``, which is not fatal and is retried like any other
    address problem.

    The two remedies are opposite and only the user can choose between them, so
    both fingerprints are carried: re-pin, if the panel's CA was legitimately
    rotated by a firmware upgrade or a factory reset, or investigate, if it was
    not.
    """

    def __init__(self, expected_fingerprint: str, observed_fingerprint: str) -> None:
        self.expected_fingerprint = expected_fingerprint
        self.observed_fingerprint = observed_fingerprint
        super().__init__(
            "The panel is advertising a different CA certificate than the pinned one. "
            f"Pinned SHA-256 {expected_fingerprint}, panel now advertises {observed_fingerprint}. "
            "Refusing to re-anchor: a rotated CA and an intercepted connection look identical "
            "from here. Re-pin only after confirming the new fingerprint out of band."
        )


class SpanPanelStaleDataError(SpanPanelError):
    """Raised when get_snapshot() is called while the client isn't live.

    Distinct from SpanPanelConnectionError: this means the client is running
    but data cannot be trusted right now (broker disconnected, or the Homie
    device has declared $state=disconnected/lost).
    """


class SpanPanelSchemaVersionError(SpanPanelError):
    """The panel reports a data-model-version this library cannot interpret.

    Distinct from SpanPanelAdapterMissingError, because the remedy differs. A
    missing adapter is a known schema with no installed parser — install or
    update the adapter package. This is a schema whose *major cannot even be
    determined*, so no adapter can be named. That is a panel this library has
    never seen, and the honest response is to say so.

    Absence is not this error: a panel that publishes no data-model-version at
    all is speaking the flat schema, which is a real and supported signal.
    """

    def __init__(self, data_model_version: str) -> None:
        self.data_model_version = data_model_version
        super().__init__(
            f"Cannot determine a schema major from data-model-version {data_model_version!r}. "
            "Expected MAJOR.MINOR[.PATCH]. Refusing to guess — parsing this panel with the "
            "wrong schema would produce plausible but incorrect power and energy values. "
            "Please report this value."
        )


class SpanPanelAdapterMissingError(SpanPanelError):
    """No installed adapter covers the schema this panel publishes."""

    def __init__(self, needed: str, reason: str, available: list[str]) -> None:
        self.needed = needed
        self.reason = reason
        self.available = available
        super().__init__(
            f"Panel requires adapter {needed!r} (reason: {reason}); "
            f"installed adapters: {sorted(available)}. "
            "Update the integration or install the missing adapter package."
        )


class SpanPanelAdapterIncompatibleError(SpanPanelError):
    """An adapter for this schema is installed, but this package cannot use it.

    Distinct from SpanPanelAdapterMissingError because the remedy is the
    opposite one. "Missing" means nothing claims this schema, and the answer is
    to install something. This means a package *does* claim it and was rejected,
    so installing more cannot help — the two installed pieces were built against
    different versions of the same contract, and one of them has to move.

    Raised rather than logged because the panel needing this adapter has no
    other parser. Discovery still only logs, so one unusable third-party adapter
    does not take down a panel whose own adapter is fine; this fires only when
    the rejected adapter turns out to be the one actually required.
    """

    def __init__(self, needed: str, reason: str, defect: str) -> None:
        self.needed = needed
        self.reason = reason
        self.defect = defect
        super().__init__(
            f"Panel requires adapter {needed!r} (reason: {reason}), and an installed "
            f"package registers it, but it cannot be used: {defect} "
            "Upgrade span-panel-api and the adapter package together."
        )
