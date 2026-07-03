"""Conservative secret scrubbing for content written into the vault.

Every byte a knotica operation writes passes through :func:`scrub` before it
is committed, so secrets never enter git history. Matches are replaced with a
``[REDACTED:<pattern>]`` marker and reported as :class:`RedactedSpan` records
-- the transaction surfaces them as a secret-scrubbed *warning* on a
successful result, so a redaction is always loud, never silent.

**Design stance: conservative on purpose.** The vault ingests research
content that legitimately contains token-like strings -- commit SHAs, base64
figures, hex digests, model identifiers. A false positive silently corrupts a
page, which is worse for a knowledge wiki than a false negative is (the span
report makes the trade visible either way). Therefore this module matches only
strings that are overwhelmingly likely to be real credentials:

* **Issuer-prefixed key formats** (AWS, GitHub, ``sk-`` API keys): the vendor
  prefix is globally unique by design, so prose almost never collides with it.
* **PEM private-key blocks**: the armor lines are an unambiguous container.
* **Assignment context + high entropy**: a generic value is redacted only when
  it is *assigned to a secret-named variable* AND looks machine-generated.

Deliberate non-goals -- these are **not** flagged, because each one matches
legitimate paper/wiki content constantly:

* arbitrary base64 or hex runs (digests, hashes, encoded figures),
* long alphanumeric identifiers without an issuer prefix (arXiv IDs, DOIs),
* entropy alone without assignment context.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

#: Minimum length for a generic assigned value to be considered a secret.
#: Real generated credentials are rarely shorter; short values are usually
#: enum-ish config ("true", "us-east-1") that must never be redacted.
MIN_ASSIGNED_VALUE_LENGTH = 16
#: Shannon-entropy floor (bits/char) for a generic assigned value. A random
#: 16+ char key sits near ~3.9-4.7; English-like placeholders ("changeme",
#: "EXAMPLE_KEY_VALUE") sit near ~3.3 or below. 3.5 favors false negatives.
MIN_ASSIGNED_VALUE_ENTROPY_BITS = 3.5

_REDACTION_TEMPLATE = "[REDACTED:{name}]"


@dataclass(frozen=True)
class RedactedSpan:
    """One redaction, located in the *original* (pre-scrub) text.

    Attributes:
        pattern: Name of the secret pattern that matched (stable identifier,
            e.g. ``"aws-access-key-id"``).
        start: Offset of the redacted region in the original text.
        end: End offset (exclusive) in the original text.
        line: 1-based line number of ``start`` in the original text.
    """

    pattern: str
    start: int
    end: int
    line: int


@dataclass(frozen=True)
class _SecretPattern:
    """A named regex, an optional value validator, and the group to redact."""

    name: str
    regex: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    group: str | int = 0


def _looks_machine_generated(value: str) -> bool:
    """Heuristic for the generic-assignment pattern: entropy + charset mix.

    Requires a digit or mixed case (pure-word phrases and shouting-case
    placeholders fail this) AND a Shannon-entropy floor (repetitive or
    English-like strings fail that). Both gates bias toward false negatives.
    """
    has_digit = any(character.isdigit() for character in value)
    has_mixed_case = value.lower() != value and value.upper() != value
    if not (has_digit or has_mixed_case):
        return False
    return _shannon_entropy_bits(value) >= MIN_ASSIGNED_VALUE_ENTROPY_BITS


def _shannon_entropy_bits(value: str) -> float:
    """Shannon entropy of ``value`` in bits per character."""
    if not value:
        return 0.0
    length = len(value)
    frequencies = {character: value.count(character) for character in set(value)}
    return -sum((count / length) * math.log2(count / length) for count in frequencies.values())


#: Ordered registry (order = priority when overlapping matches collide).
#: Each entry documents why it cannot plausibly match legitimate prose.
_SECRET_PATTERNS: tuple[_SecretPattern, ...] = (
    # PEM armor is an explicit, self-labeling container for key material; the
    # BEGIN/END pair (with matching qualifier, e.g. "RSA "/"OPENSSH ") never
    # occurs in prose by accident. Only complete blocks match -- a lone BEGIN
    # line quoted in documentation is not a usable key and is left alone.
    _SecretPattern(
        name="pem-private-key",
        regex=re.compile(
            r"-----BEGIN (?P<qualifier>(?:[A-Z]+ )*)PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?P=qualifier)PRIVATE KEY-----"
        ),
    ),
    # AWS access key IDs carry the fixed issuer prefix AKIA (long-lived) or
    # ASIA (temporary) followed by exactly 16 uppercase base32 chars -- a
    # format AWS chose to be globally recognizable.
    _SecretPattern(
        name="aws-access-key-id",
        regex=re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    # GitHub tokens are issuer-prefixed by design (ghp_ personal, gho_ OAuth,
    # ghu_/ghs_ app, ghr_ refresh; github_pat_ fine-grained) precisely so that
    # scanners can match them with near-zero false positives.
    _SecretPattern(
        name="github-token",
        regex=re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    ),
    # OpenAI/Anthropic-style API keys: the sk- prefix followed by a long
    # opaque token (Anthropic's sk-ant-... included). \b keeps hyphenated
    # prose like "task-..." or "risk-..." from ever reaching the prefix.
    _SecretPattern(
        name="sk-api-key",
        regex=re.compile(r"\bsk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
    ),
    # Generic assignment: a secret-named variable being assigned a long,
    # high-entropy value (validator below). Assignment context is required so
    # a bare token-like string in running text is never touched; only the
    # value is redacted, never the variable name.
    _SecretPattern(
        name="assigned-secret",
        regex=re.compile(
            r"(?i)\b(?:api[_-]?key|api[_-]?secret|api[_-]?token|secret[_-]?key|"
            r"access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key|"
            r"password|passwd)\b"
            r"""\s*[:=]\s*["']?(?P<value>[A-Za-z0-9_\-/+.]{16,})["']?"""
        ),
        validator=_looks_machine_generated,
        group="value",
    ),
)


def scrub(text: str) -> tuple[str, list[RedactedSpan]]:
    """Redact conservative secret patterns from ``text``.

    Returns:
        A ``(scrubbed_text, redacted_spans)`` pair. ``redacted_spans`` locate
        each redaction in the **original** text (never carrying the secret
        itself); an empty list means the text passed through unchanged.
    """
    spans = _collect_spans(text)
    if not spans:
        return text, []
    pieces: list[str] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor : span.start])
        pieces.append(_REDACTION_TEMPLATE.format(name=span.pattern))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces), spans


def _collect_spans(text: str) -> list[RedactedSpan]:
    """Match all patterns, then drop overlaps (earlier/longer/higher-priority wins)."""
    candidates: list[tuple[int, int, int, str]] = []
    for priority, pattern in enumerate(_SECRET_PATTERNS):
        for match in pattern.regex.finditer(text):
            start, end = match.span(pattern.group)
            if pattern.validator and not pattern.validator(text[start:end]):
                continue
            candidates.append((start, -(end - start), priority, pattern.name))
    candidates.sort()
    spans: list[RedactedSpan] = []
    last_end = 0
    for start, negative_length, _priority, name in candidates:
        end = start - negative_length
        if start < last_end:
            continue
        line = text.count("\n", 0, start) + 1
        spans.append(RedactedSpan(pattern=name, start=start, end=end, line=line))
        last_end = end
    return spans
