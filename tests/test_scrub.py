"""Behavioral contract tests for ``knotica.core.scrub`` — conservative secret scrubbing.

The contract under test:

1. **Real credential formats are redacted** before anything reaches git
   history: issuer-prefixed keys (AWS ``AKIA``/``ASIA``, GitHub ``ghp_``/
   ``github_pat_``, ``sk-`` API keys), complete PEM private-key blocks, and
   secret-named assignments carrying a machine-generated value.
2. **Every redaction is loud**: the scrubbed text carries a visible
   ``[REDACTED:...]`` marker and a span report locating the redaction in the
   *original* text — never carrying the secret itself.
3. **Legitimate research content is never touched** (the false-positive
   corpus): base64 runs, hex digests, git SHAs, DOIs, arXiv IDs, model
   identifiers, quoted PEM armor lines, and token-like strings without an
   issuer prefix or assignment context. A false positive silently corrupts a
   wiki page — worse than a false negative, which the span report at least
   makes visible.
"""

import pytest

from knotica.core.scrub import scrub

# ---------------------------------------------------------------------------
# Positives: real credential shapes must be redacted
# ---------------------------------------------------------------------------

_PEM_RSA_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAfakekeymaterial0123456789abcdefFAKE\n"
    "-----END RSA PRIVATE KEY-----"
)
_PEM_UNQUALIFIED_BLOCK = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHfakeFAKEfake\n"
    "-----END PRIVATE KEY-----"
)

# (test id, full text, exact substring that must be redacted, 1-based line of the secret)
SECRET_CASES = [
    pytest.param(
        "leaked AWS long-lived access key ID: AKIAIOSFODNN7EXAMPLE in prose",
        "AKIAIOSFODNN7EXAMPLE",
        1,
        id="aws-akia-key-id",
    ),
    pytest.param(
        "temporary credential ASIAJ2K4M6P8R0T2V4X6 pasted into a note",
        "ASIAJ2K4M6P8R0T2V4X6",
        1,
        id="aws-asia-key-id",
    ),
    pytest.param(
        "token: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 (classic PAT)",
        "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        1,
        id="github-classic-token",
    ),
    pytest.param(
        "fine-grained github_pat_11ABCDEFG0abcdefghijklmnopqrstuv here",
        "github_pat_11ABCDEFG0abcdefghijklmnopqrstuv",
        1,
        id="github-fine-grained-token",
    ),
    pytest.param(
        "openai key sk-proj-Ab3dEf6hIj9kLm2nOp5qRs8t left in a snippet",
        "sk-proj-Ab3dEf6hIj9kLm2nOp5qRs8t",
        1,
        id="sk-api-key",
    ),
    pytest.param(
        "anthropic key sk-ant-api03-Xy7Zw2Qv9Rt4Um8Kn3Jd6Fh pasted",
        "sk-ant-api03-Xy7Zw2Qv9Rt4Um8Kn3Jd6Fh",
        1,
        id="sk-ant-api-key",
    ),
    pytest.param(
        f"Config appendix:\n{_PEM_RSA_BLOCK}\nEnd of appendix.",
        _PEM_RSA_BLOCK,
        2,
        id="pem-rsa-private-key-block",
    ),
    pytest.param(
        f"Generated key material:\n\n{_PEM_UNQUALIFIED_BLOCK}\n",
        _PEM_UNQUALIFIED_BLOCK,
        3,
        id="pem-unqualified-private-key-block",
    ),
    pytest.param(
        'setup notes\napi_key = "q7Xw2Kp9Rt4Lm8Zn3Vb6"\nmore notes',
        "q7Xw2Kp9Rt4Lm8Zn3Vb6",
        2,
        id="assigned-high-entropy-value-equals-style",
    ),
    pytest.param(
        "access_token: Zr8Kq2Nx7Vw3Jp9Tb5Yd4Mc",
        "Zr8Kq2Nx7Vw3Jp9Tb5Yd4Mc",
        1,
        id="assigned-high-entropy-value-colon-style",
    ),
]


@pytest.mark.parametrize(("text", "secret", "line"), SECRET_CASES)
def test_real_credential_formats_are_redacted_with_a_span_report(
    text: str, secret: str, line: int
) -> None:
    scrubbed, spans = scrub(text)

    assert secret not in scrubbed, "the secret must not survive into committable content"
    assert "[REDACTED:" in scrubbed, "a redaction must be loud — a visible marker in the text"
    assert len(spans) == 1
    span = spans[0]
    assert text[span.start : span.end] == secret, (
        "the span must locate the secret in the ORIGINAL text"
    )
    assert span.line == line
    assert span.pattern, "the span must name which pattern fired"
    assert f"[REDACTED:{span.pattern}]" in scrubbed


def test_only_the_assigned_value_is_redacted_never_the_variable_name() -> None:
    scrubbed, spans = scrub('api_key = "q7Xw2Kp9Rt4Lm8Zn3Vb6"')

    assert "api_key" in scrubbed, "the variable name is legitimate content and must survive"
    assert "q7Xw2Kp9Rt4Lm8Zn3Vb6" not in scrubbed
    assert len(spans) == 1


# ---------------------------------------------------------------------------
# Negatives: the false-positive corpus — legitimate paper/wiki content
# ---------------------------------------------------------------------------

FALSE_POSITIVE_CORPUS = [
    pytest.param(
        "The figure is embedded as SGVsbG8gd29ybGQgdGhpcyBpcyBhIGZpZ3VyZQ== in the export.",
        id="arbitrary-base64-run",
    ),
    pytest.param(
        "Artifact digest: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        id="sha256-hex-digest",
    ),
    pytest.param(
        "Reproduced from commit 0a961c1f2b3d4e5a6c7b8d9e0f1a2b3c4d5e6f7a of the upstream repo.",
        id="full-git-sha",
    ),
    pytest.param(
        "See https://doi.org/10.48550/arXiv.2304.03442 for the original study.",
        id="doi-in-citation",
    ),
    pytest.param(
        "Wang et al., arXiv:2304.03442v2, introduce workflow memory.",
        id="arxiv-identifier",
    ),
    pytest.param(
        "We evaluated gpt-4o-2024-08-06 and claude-fable-5 on the held-out set.",
        id="model-identifiers",
    ),
    pytest.param(
        "The armor line `-----BEGIN RSA PRIVATE KEY-----` marks the start of key material.",
        id="quoted-pem-begin-line-without-end",
    ),
    pytest.param(
        "Checkpoint Xk9mQ2vR7pLw3JnT8bZs4Yc reached convergence at step 40k.",
        id="high-entropy-token-without-assignment-context",
    ),
    pytest.param(
        "the risk-Weighted0Portfolio2Model34Xy metric from the appendix",
        id="hyphenated-prose-embedding-sk-prefix",
    ),
    pytest.param(
        'password = "changemechangeme"',
        id="assigned-placeholder-without-digit-or-mixed-case",
    ),
    pytest.param(
        'api_key = "aaaa1111bbbb2222"',
        id="assigned-low-entropy-value",
    ),
    pytest.param(
        'password = "hunter2"',
        id="assigned-short-value",
    ),
    pytest.param(
        "The AKIAIOSFODNN7EXAMPL prefix is one character too short to be a key ID.",
        id="aws-shaped-but-wrong-length",
    ),
    pytest.param(
        "Plain wiki prose about agent workflow memory, with [[wikilinks]] and *emphasis*.",
        id="plain-prose",
    ),
]


@pytest.mark.parametrize("text", FALSE_POSITIVE_CORPUS)
def test_legitimate_research_content_passes_through_untouched(text: str) -> None:
    scrubbed, spans = scrub(text)

    assert scrubbed == text, "a false positive silently corrupts a wiki page"
    assert spans == []


# ---------------------------------------------------------------------------
# Invariants across the whole scrub result
# ---------------------------------------------------------------------------

_MULTI_SECRET_DOCUMENT = (
    "Incident report draft.\n"
    "Leaked key one: AKIAIOSFODNN7EXAMPLE\n"
    "Leaked key two: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789\n"
    'config had api_key = "q7Xw2Kp9Rt4Lm8Zn3Vb6" committed\n'
)


def test_every_secret_in_a_document_is_redacted_with_ordered_spans() -> None:
    scrubbed, spans = scrub(_MULTI_SECRET_DOCUMENT)

    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed
    assert "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" not in scrubbed
    assert "q7Xw2Kp9Rt4Lm8Zn3Vb6" not in scrubbed
    assert len(spans) == 3
    assert [span.line for span in spans] == [2, 3, 4]
    assert [span.start for span in spans] == sorted(span.start for span in spans)
    starts_and_ends = [(span.start, span.end) for span in spans]
    assert all(
        earlier[1] <= later[0] for earlier, later in zip(starts_and_ends, starts_and_ends[1:])
    )


def test_the_span_report_never_carries_the_secret_itself() -> None:
    _, spans = scrub("leaked: AKIAIOSFODNN7EXAMPLE")

    assert "AKIAIOSFODNN7EXAMPLE" not in repr(spans), (
        "span reports travel into results and logs — they must never leak the secret"
    )


def test_scrubbing_is_idempotent() -> None:
    once, first_spans = scrub(_MULTI_SECRET_DOCUMENT)
    twice, second_spans = scrub(once)

    assert first_spans, "precondition: the document must actually trigger redactions"
    assert twice == once, "redaction markers must not themselves be flagged as secrets"
    assert second_spans == []


def test_clean_text_passes_through_with_an_empty_span_report() -> None:
    text = "# Agent Workflow Memory\n\nInduced workflows improve success rates.\n"

    scrubbed, spans = scrub(text)

    assert scrubbed == text
    assert spans == []
