"""The sibling ``MANIFEST.json`` that content-addresses a frozen golden set.

The proof that a golden set is the one that was frozen: a sha256 over the file's
exact bytes plus the ``split: held_out`` declaration that marks it the eval set
rather than the trainset. Parsing/verifying (read side) and rendering (freeze side)
live together because they are the two directions of one format -- a field added to
the render must be added to the parse in the same edit.
"""

import json

from knotica.core.records import body_sha256
from knotica.evals.golden.contract import GoldenSetIntegrityError

from dataclasses import dataclass

#: The ``split`` value a conforming golden-set manifest must declare -- the marker
#: that this dataset is the held-out eval set, not the trainset.
GOLDEN_SPLIT = "held_out"


@dataclass(frozen=True, kw_only=True)
class GoldenManifest:
    """The sibling ``MANIFEST.json`` that content-addresses a frozen golden set.

    ``sha256`` is the digest of ``golden.jsonl``'s exact UTF-8 bytes; ``split`` is
    ``"held_out"`` for a conforming set; ``version``, ``source``, and ``size``
    record the freeze provenance. Parsed and verified on the read side; written on
    the freeze side.
    """

    sha256: str
    version: str
    source: str
    split: str
    size: int


def _parse_manifest(text: str, *, topic: str) -> GoldenManifest:
    """Parse a golden-set manifest, raising a typed integrity error on malformed input."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise GoldenSetIntegrityError(topic, "its MANIFEST.json is not valid JSON") from error
    if not isinstance(data, dict):
        raise GoldenSetIntegrityError(topic, "its MANIFEST.json is not a JSON object")
    return GoldenManifest(
        sha256=_manifest_str(data, "sha256", topic=topic),
        version=_manifest_str(data, "version", topic=topic),
        source=_manifest_str(data, "source", topic=topic),
        split=_manifest_str(data, "split", topic=topic),
        size=_manifest_int(data, "size", topic=topic),
    )


def _verify_manifest(manifest: GoldenManifest, golden_text: str, *, topic: str) -> None:
    """Check the manifest's declared split and its sha256 against the golden bytes."""
    if manifest.split != GOLDEN_SPLIT:
        raise GoldenSetIntegrityError(
            topic,
            f"its MANIFEST.json declares split {manifest.split!r}, not {GOLDEN_SPLIT!r}",
        )
    if manifest.sha256 != body_sha256(golden_text):
        raise GoldenSetIntegrityError(
            topic,
            "its golden.jsonl does not match the sha256 recorded in MANIFEST.json "
            "(the frozen set was modified after freezing)",
        )


def _render_manifest(manifest: GoldenManifest) -> str:
    """Serialize a :class:`GoldenManifest` to its ``MANIFEST.json`` text (trailing newline)."""
    payload = {
        "sha256": manifest.sha256,
        "version": manifest.version,
        "source": manifest.source,
        "split": manifest.split,
        "size": manifest.size,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _manifest_field(data: dict[str, object], key: str, *, topic: str) -> object:
    """Return a required manifest field, raising a typed integrity error when absent."""
    if key not in data:
        raise GoldenSetIntegrityError(topic, f"its MANIFEST.json is missing the {key!r} field")
    return data[key]


def _manifest_str(data: dict[str, object], key: str, *, topic: str) -> str:
    """Return a required string manifest field, typed-error on the wrong type."""
    value = _manifest_field(data, key, topic=topic)
    if not isinstance(value, str):
        raise GoldenSetIntegrityError(topic, f"its MANIFEST.json field {key!r} must be a string")
    return value


def _manifest_int(data: dict[str, object], key: str, *, topic: str) -> int:
    """Return a required integer manifest field, typed-error on the wrong type."""
    value = _manifest_field(data, key, topic=topic)
    if not isinstance(value, int) or isinstance(value, bool):
        raise GoldenSetIntegrityError(topic, f"its MANIFEST.json field {key!r} must be an integer")
    return value
