"""Behavioral spec for the lenient JSONL reader.

``core.jsonl.read_jsonl_dicts`` is the shared reader for append-only vault logs
(the datasets inventory's dataset files, the ingest activity journal). Its whole
contract is *what it survives*: these files are appended to by live processes, so
a truncated last line or a stray scalar row must cost the caller that one row and
nothing more.

- **Object rows survive; everything else is dropped.** Blank lines, undecodable
  lines, and rows that parse to a non-object (scalar, list, ``null``) are skipped
  silently rather than raising.
- **Order is preserved** -- the rows come back in file order, so a journal read
  is still chronological after bad lines are dropped.
- **Empty in, empty out** -- no file content and no parseable rows both yield an
  empty list, never ``None``.
"""

from knotica.core.jsonl import read_jsonl_dicts


def test_reads_object_rows_in_file_order() -> None:
    text = '{"stage": "fetch"}\n{"stage": "parse"}\n'

    assert read_jsonl_dicts(text) == [{"stage": "fetch"}, {"stage": "parse"}]


def test_skips_blank_and_whitespace_only_lines() -> None:
    text = '\n{"stage": "fetch"}\n   \n{"stage": "parse"}\n'

    assert read_jsonl_dicts(text) == [{"stage": "fetch"}, {"stage": "parse"}]


def test_skips_an_undecodable_line_and_keeps_the_rest() -> None:
    text = '{"stage": "fetch"}\n{"stage": "trunc\n{"stage": "parse"}\n'

    assert read_jsonl_dicts(text) == [{"stage": "fetch"}, {"stage": "parse"}]


def test_skips_rows_that_parse_to_a_non_object() -> None:
    text = '{"stage": "fetch"}\n42\n["a"]\nnull\n{"stage": "parse"}\n'

    assert read_jsonl_dicts(text) == [{"stage": "fetch"}, {"stage": "parse"}]


def test_returns_an_empty_list_for_empty_text() -> None:
    assert read_jsonl_dicts("") == []


def test_returns_an_empty_list_when_no_line_is_an_object() -> None:
    assert read_jsonl_dicts("42\nnot json\n") == []
