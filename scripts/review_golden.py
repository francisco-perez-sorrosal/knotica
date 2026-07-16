"""Golden-set review UI -- the human gate between bootstrap staging and freeze.

A local, stdlib-only web app over one topic's ``golden.staging.jsonl``: each
candidate renders as an editable card (question, reference answer, citations)
with live citation-resolution badges checked against the vault's real
``sources/<topic>/`` files, duplicate warnings against the flywheel
``qa.jsonl`` (the freeze-time contamination check made visible at review
time), and keep/discard toggles. Saving writes the accepted candidates to
``golden.staging.reviewed.jsonl`` beside the staging file -- the exact input
the freeze step consumes. The staging file itself is never modified, and the
review can be resumed: when a reviewed file already exists it is loaded in
preference to the raw staging.

Run from the repo root::

    uv run python scripts/review_golden.py --topic agentic-systems

The vault is resolved from the knotica config; pass ``--vault`` to override.
Everything runs on localhost with zero network egress and zero LLM calls.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STAGING_NAME = "golden.staging.jsonl"
REVIEWED_NAME = "golden.staging.reviewed.jsonl"
#: The eval-readiness band surfaced in the header (mirrors EVAL_MIN_GOLDEN).
FLOOR = 20
TARGET_HIGH = 30
CANDIDATE_KEYS = ("question", "reference_answer", "citations", "pages_used")


class ReviewState:
    """Immutable-per-launch view of everything the UI needs."""

    def __init__(self, vault: Path, topic: str, vault_name: str | None = None) -> None:
        self.vault = vault
        self.topic = topic
        #: Obsidian's registered vault name (Advanced URI needs it); defaults to
        #: the directory basename, overridable when the registered name differs.
        self.vault_name = vault_name or vault.name
        datasets = vault / topic / ".knotica" / "datasets"
        self.staging_path = datasets / STAGING_NAME
        self.reviewed_path = datasets / REVIEWED_NAME
        self.sources_dir = vault / "sources" / topic
        self.qa_path = datasets / "qa.jsonl"

    def load(self) -> dict:
        source = self.reviewed_path if self.reviewed_path.exists() else self.staging_path
        if not source.exists():
            raise SystemExit(
                f"no staging file at {self.staging_path} -- run"
                " `knotica eval --bootstrap` for this topic first."
            )
        candidates = _read_jsonl(source)
        self._enrich_support_offsets(candidates)
        source_keys = sorted(p.stem for p in self.sources_dir.glob("*.md"))
        return {
            "topic": self.topic,
            "vault_name": self.vault_name,
            "candidates": candidates,
            "pages": self._page_provenance(candidates),
            "citation_links": {
                key: "obsidian://open?path="
                + urllib.parse.quote(str(self.sources_dir / f"{key}.md"), safe="")
                for key in source_keys
            },
            "source_keys": source_keys,
            "qa_questions": sorted(_qa_questions(self.qa_path)),
            "floor": FLOOR,
            "target_high": TARGET_HIGH,
            "resumed": source == self.reviewed_path,
            "loaded_from": str(source),
            "reviewed_path": str(self.reviewed_path),
        }

    def _page_provenance(self, candidates: list[dict]) -> dict[str, dict]:
        """Existence + Obsidian deep link for every page any candidate cites.

        ``pages_used`` values vary by producer: the bootstrap records bare
        topic-relative slugs (``agent-memory``) while curated examples carry
        vault-relative paths (``agentic-systems/agent-memory``) -- so resolution
        tries the topic directory first, then the vault root. The
        ``obsidian://open?path=`` form takes an absolute file path and lets
        Obsidian resolve which vault owns it -- no dependence on the vault's
        registered display name.
        """
        pages: dict[str, dict] = {}
        for candidate in candidates:
            for page in candidate.get("pages_used", []):
                name = str(page).strip()
                if not name or name in pages:
                    continue
                pages[name] = self._resolve_page(name)
        return pages

    def _resolve_page(self, name: str) -> dict:
        relative = name if name.endswith(".md") else f"{name}.md"
        topic_first = (self.vault / self.topic / relative, self.vault / relative)
        path = next((p for p in topic_first if p.is_file()), topic_first[0])
        return {
            "exists": path.is_file(),
            "relative": str(path.relative_to(self.vault)),
            "obsidian_uri": "obsidian://open?path=" + urllib.parse.quote(str(path), safe=""),
        }

    def _enrich_support_offsets(self, candidates: list[dict]) -> None:
        """Relocate each support quote in the CURRENT page file, char-precise.

        Generation-time line spans go stale the moment a page is edited, and
        Advanced URI's ``offset`` parameter (cursor at a character count from
        file start) is finer than any line jump -- so links are built from a
        fresh server-side relocation at review time. Each verified-or-not entry
        gains a ``current`` sub-object when the quote is found in the file as
        it exists now; entries that no longer match simply get none.
        """
        raw_cache: dict[str, str | None] = {}
        for candidate in candidates:
            for entry in candidate.get("support", []) or []:
                page = str(entry.get("page", "")).strip()
                quote = str(entry.get("quote", ""))
                if not page or not quote:
                    continue
                if page not in raw_cache:
                    info = self._resolve_page(page)
                    path = self.vault / info["relative"]
                    raw_cache[page] = path.read_text(encoding="utf-8") if info["exists"] else None
                raw = raw_cache[page]
                located = _locate_quote(raw, quote) if raw is not None else None
                if located:
                    entry["current"] = located

    def save(self, accepted: list[dict]) -> dict:
        rows = [_normalized_candidate(row) for row in accepted]
        payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        _atomic_write(self.reviewed_path, payload)
        return {"written": str(self.reviewed_path), "count": len(rows)}


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number} is not valid JSON: {exc}") from exc
    return rows


def _qa_questions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(row.get("query", "")).strip().lower()
        for row in _read_jsonl(path)
        if str(row.get("query", "")).strip()
    }


def _normalized_candidate(row: dict) -> dict:
    missing = [key for key in CANDIDATE_KEYS if key not in row]
    if missing:
        raise ValueError(f"candidate is missing {missing}")
    question = str(row["question"]).strip()
    answer = str(row["reference_answer"]).strip()
    if not question or not answer:
        raise ValueError("candidate question and reference_answer must be non-empty")
    citations = [str(key).strip() for key in row["citations"] if str(key).strip()]
    pages = [str(page).strip() for page in row["pages_used"] if str(page).strip()]
    normalized = {
        "question": question,
        "reference_answer": answer,
        "citations": citations,
        "pages_used": pages,
    }
    # Provenance spans ride through review untouched (freeze ignores them).
    support = row.get("support")
    if isinstance(support, list) and support:
        normalized["support"] = support
    return normalized


def _locate_quote(raw: str, quote: str) -> dict | None:
    """Find ``quote`` in ``raw``: exact first, then whitespace-normalized.

    Returns 0-based character offsets (``char_start`` inclusive, ``char_end``
    exclusive) plus 1-based inclusive line numbers, or ``None`` when the quote
    does not appear in the text as it exists now. First occurrence wins.
    """
    start = raw.find(quote)
    end = start + len(quote)
    if start == -1:
        normalized_raw, offset_map = _normalize_with_offsets(raw)
        normalized_quote, _ = _normalize_with_offsets(quote)
        if not normalized_quote:
            return None
        hit = normalized_raw.find(normalized_quote)
        if hit == -1:
            return None
        start = offset_map[hit]
        end = offset_map[hit + len(normalized_quote) - 1] + 1
    return {
        "char_start": start,
        "char_end": end,
        "line_start": raw.count("\n", 0, start) + 1,
        "line_end": raw.count("\n", 0, max(start, end - 1)) + 1,
    }


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to single spaces, mapping each kept char home."""
    chars: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if chars and not in_space:
                chars.append(" ")
                offsets.append(index)
            in_space = True
        else:
            chars.append(char)
            offsets.append(index)
            in_space = False
    if chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class _Handler(BaseHTTPRequestHandler):
    state: ReviewState  # injected by serve()

    def do_GET(self) -> None:  # noqa: N802 - http.server contract
        if self.path == "/":
            self._respond(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._respond_json(200, self.state.load())
        else:
            self._respond_json(404, {"error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802 - http.server contract
        if self.path != "/api/save":
            self._respond_json(404, {"error": "unknown path"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.state.save(list(body["accepted"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._respond_json(400, {"error": str(exc)})
            return
        self._respond_json(200, result)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # keep the terminal quiet; the UI is the surface

    def _respond_json(self, status: int, payload: dict) -> None:
        self._respond(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _resolve_vault(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    try:
        from knotica.core.config import resolve

        return Path(resolve().path)
    except Exception as exc:  # noqa: BLE001 - any failure means: ask for --vault
        raise SystemExit(f"could not resolve the vault from knotica config ({exc}); pass --vault")


def serve(state: ReviewState, port: int, open_browser: bool) -> None:
    handler = type("BoundHandler", (_Handler,), {"state": state})
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/"
        print(f"golden-set review for topic '{state.topic}': {url}")
        print(f"reviewed output will be written to: {state.reviewed_path}")
        print("Ctrl-C to stop once you have saved.")
        if open_browser:
            threading.Timer(0.3, webbrowser.open, args=(url,)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--topic", required=True, help="topic whose staging file to review")
    parser.add_argument("--vault", help="vault root (default: knotica config)")
    parser.add_argument(
        "--vault-name",
        help="Obsidian's registered vault name for deep links (default: folder name)",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()
    state = ReviewState(_resolve_vault(args.vault), args.topic, vault_name=args.vault_name)
    state.load()  # fail fast on a missing/broken staging file before serving
    serve(state, args.port, open_browser=not args.no_browser)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Golden-set review</title>
<style>
  /* Solarized-light theme, ported from the AIE findings design system.
     Self-contained: no webfonts, system stack only. */
  :root {
    --bg: #fdf6e3; --surface: #fbf3df; --surface-2: #eee8d5; --border: #ddd6c1;
    --text: #3b4b51; --muted: #6b7a80; --heading: #073642;
    --accent: #268bd2; --accent-strong: #1a6fb0;
    --good: #4f7a00; --good-fill: #859900; --bad: #c0392b; --bad-fill: #dc322f;
    --warn: #b58900; --hack: #cb4b16; --violet: #6c71c4; --cyan: #2aa198;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif;
    --fs-small: 13px;
    --shadow: 0 1px 3px rgba(7,54,66,.07), 0 6px 24px rgba(7,54,66,.05);
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:var(--sans);
         font-size:15px; line-height:1.6; -webkit-font-smoothing:antialiased; }
  a { color: var(--accent); }

  header { position:sticky; top:0; z-index:5; background:var(--surface);
           border-bottom:1px solid var(--border); box-shadow:var(--shadow);
           padding:14px 28px; display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
  .eyebrow { font-family:var(--mono); font-size:var(--fs-small); letter-spacing:.12em;
             text-transform:uppercase; color:var(--accent-strong); margin:0; }
  .eyebrow b { color:var(--heading); }
  .kpi { display:flex; align-items:baseline; gap:8px; padding:4px 14px;
         border:1px solid var(--border); border-radius:999px; background:var(--surface-2); }
  .kpi .v { font-weight:700; font-variant-numeric:tabular-nums; color:var(--heading); }
  .kpi .k { font-size:var(--fs-small); color:var(--muted); text-transform:uppercase;
            letter-spacing:.05em; }
  .kpi.low .v { color:var(--warn); } .kpi.good .v { color:var(--good); }
  .kpi.high .v { color:var(--accent-strong); }
  .dirty { color:var(--warn); font-weight:600; font-size:var(--fs-small);
           visibility:hidden; }
  .dirty.on { visibility:visible; }
  button.primary { background:var(--accent); color:#fff; border:0; padding:9px 20px;
                   border-radius:999px; font:inherit; font-weight:600; cursor:pointer;
                   transition:background .15s; }
  button.primary:hover { background:var(--accent-strong); }
  button.primary:disabled { opacity:.5; cursor:default; }

  main { max-width: 980px; margin: 0 auto; padding: 20px 28px 90px; }
  .callout { border:1px solid var(--border); border-radius:12px; background:var(--surface-2);
             padding:14px 20px; margin:16px 0; font-size:14px; }
  .callout.lesson { border-left:4px solid var(--violet); }
  .callout.saved { border-left:4px solid var(--good-fill); background:#f3f6e0;
                   display:none; }
  .callout b { color:var(--heading); }

  .card { background:var(--surface); border:1px solid var(--border);
          border-left:4px solid var(--accent); border-radius:12px; padding:18px 22px;
          margin:18px 0; box-shadow:var(--shadow); transition:opacity .2s; }
  .card.discarded { opacity:.42; border-left-color:var(--bad-fill); filter:grayscale(.4); }
  .card .top { display:flex; justify-content:space-between; gap:10px;
               align-items:baseline; margin-bottom:2px; }
  .idx { font-family:var(--mono); font-size:12px; letter-spacing:.12em;
         text-transform:uppercase; color:var(--accent-strong); }
  .flag { font-family:var(--mono); font-size:12px; font-weight:700; padding:2px 10px;
          border-radius:999px; background:#fbeae7; color:var(--hack);
          border:1px solid #e6b8ae; }
  label { display:block; font-family:var(--mono); font-size:12px; font-weight:600;
          color:var(--muted); margin:14px 0 4px; text-transform:uppercase;
          letter-spacing:.08em; }
  textarea, input[type=text] { width:100%; border:1px solid var(--border);
          border-radius:8px; padding:9px 12px; font:inherit; font-size:14px;
          background:#fffdf5; color:var(--text); }
  textarea:focus, input:focus { outline:none; border-color:var(--accent);
          box-shadow:0 0 0 3px rgba(38,139,210,.15); }
  textarea.q { min-height:40px; } textarea.a { min-height:104px; }

  .badges, .chips { display:flex; gap:7px; flex-wrap:wrap; margin-top:6px; }
  .cite, .chip { display:inline-block; font-family:var(--mono); font-size:12px;
          padding:2px 11px; border-radius:999px; border:1px solid var(--border);
          background:var(--surface-2); text-decoration:none; color:var(--muted); }
  .cite.ok, a.chip.ok { color:var(--good); border-color:#b9c46a; background:#f3f6e0; }
  a.cite.ok:hover, a.chip.ok:hover { border-color:var(--good-fill);
          text-decoration:none; box-shadow:0 0 0 3px rgba(133,153,0,.15); }
  .cite.bad, .chip.bad { color:var(--bad); border-color:#e6b8ae; background:#fbeae7; }

  .quotes { margin-top:2px; }
  .quote { background:var(--surface-2); border:1px solid var(--border);
           border-left:4px solid var(--cyan); border-radius:10px;
           padding:10px 16px; margin:8px 0; }
  .quote blockquote { margin:0; font-size:13.5px; color:var(--text);
           white-space:pre-wrap; }
  .quote .meta { font-family:var(--mono); font-size:12px; color:var(--muted);
           margin-top:6px; }
  .quote .meta a { color:var(--accent); text-decoration:none; }
  .quote .meta a:hover { text-decoration:underline; }
  .unlocated { color:var(--warn); font-weight:600; }

  .row { display:flex; justify-content:flex-end; margin-top:14px; }
  button.toggle { font:inherit; font-size:var(--fs-small); background:var(--surface);
          border:1px solid var(--border); border-radius:999px; padding:6px 16px;
          cursor:pointer; color:var(--muted); transition:all .15s; }
  button.toggle:hover { border-color:var(--bad); color:var(--bad); }
  .card.discarded button.toggle:hover { border-color:var(--good-fill);
          color:var(--good); }
  @media (max-width: 640px) { main, header { padding-left:16px; padding-right:16px; } }
</style>
</head>
<body>
<header>
  <p class="eyebrow">Golden-set review \u00b7 <b id="topic"></b></p>
  <span id="counts" class="kpi good"><span class="v"></span><span class="k"></span></span>
  <span id="dirty" class="dirty">unsaved changes</span>
  <span style="flex:1"></span>
  <button id="save" class="primary">Save reviewed set</button>
</header>
<main>
<div class="callout lesson">
  Keep <b>20\u201330</b> strong candidates. For each card: is the <b>question</b> specific
  and answerable from the wiki? Does the <b>reference answer</b> state exactly what a
  correct answer must contain \u2014 it is the judge\u2019s ground truth? Do the
  <b>citations</b> resolve (green, click to open the stored source) and genuinely support
  the answer? Do the <b>supporting quotes</b> really appear in the linked pages? Discard
  weak or redundant candidates; an orange <b>duplicate</b> flag means the question already
  exists in the flywheel trainset \u2014 discard or reword it.
</div>
<div id="done" class="callout saved"></div>
<div id="cards"></div>
</main>
<script>
"use strict";
let model = null, dirty = false;

function setDirty(on) {
  dirty = on;
  document.getElementById("dirty").classList.toggle("on", on);
}

function keptRows() { return model.candidates.filter(c => c._kept); }

function refreshHeader() {
  const n = keptRows().length, el = document.getElementById("counts");
  el.querySelector(".v").textContent = n + " / " + model.candidates.length;
  el.querySelector(".k").textContent = "kept";
  el.className = "kpi " +
    (n < model.floor ? "low" : n <= model.target_high ? "good" : "high");
  el.title = n < model.floor
    ? "below the " + model.floor + "-candidate stability floor"
    : "target band is " + model.floor + "\u2013" + model.target_high;
}

function citeBadges(card, citations) {
  const wrap = card.querySelector(".badges");
  wrap.innerHTML = "";
  for (const key of citations) {
    const ok = model.source_keys.includes(key);
    const badge = document.createElement(ok ? "a" : "span");
    badge.className = "cite " + (ok ? "ok" : "bad");
    badge.textContent = (ok ? "\u2713 " : "\u2717 ") + key;
    if (ok) {
      badge.href = model.citation_links[key];
      badge.title = "open the stored source in Obsidian";
    } else {
      badge.title = "no such stored source";
    }
    wrap.appendChild(badge);
  }
}

function advUri(pageInfo, positionParams) {
  // viewmode=live: cursor placement is invisible in reading view, so force an
  // editing view. Advanced URI positions the cursor; it has no highlight param.
  return "obsidian://adv-uri?vault=" + encodeURIComponent(model.vault_name) +
    "&filepath=" + encodeURIComponent(pageInfo.relative) +
    "&" + positionParams + "&viewmode=live";
}

function renderSupport(card, cand) {
  const wrap = card.querySelector(".quotes");
  const entries = cand.support || [];
  if (!entries.length) {
    wrap.style.display = "none";
    card.querySelector("label.support-label").style.display = "none";
    return;
  }
  for (const s of entries) {
    const box = document.createElement("div");
    box.className = "quote";
    const quote = document.createElement("blockquote");
    quote.textContent = s.quote;
    box.appendChild(quote);
    const meta = document.createElement("div");
    meta.className = "meta";
    const pageInfo = model.pages[s.page];
    const current = s.current;
    if (pageInfo && pageInfo.exists && (current || s.verified)) {
      const label = document.createElement("span");
      const lineStart = current ? current.line_start : s.line_start;
      const lineEnd = current ? current.line_end : s.line_end;
      label.textContent = s.page + ", " +
        (lineStart === lineEnd ? "line " + lineStart
                               : "lines " + lineStart + "\u2013" + lineEnd) +
        (current ? " \u00b7 char " + current.char_start : " (at generation)") +
        " \u00b7 ";
      const jump = document.createElement("a");
      jump.href = current
        ? advUri(pageInfo, "offset=" + current.char_start)
        : advUri(pageInfo, "line=" + s.line_start + "&column=1");
      jump.textContent = "\u2197 jump to quote";
      jump.title = "places the cursor at the quote in live-preview mode " +
        "(Advanced URI plugin; the plugin cannot visually highlight text)";
      const open = document.createElement("a");
      open.href = pageInfo.obsidian_uri;
      open.textContent = "open page";
      open.title = "open in Obsidian (no plugin needed)";
      meta.append(label, jump, document.createTextNode(" \u00b7 "), open);
    } else {
      const flag = document.createElement("span");
      flag.className = "unlocated";
      flag.textContent = "\u26a0 quote not located verbatim in " +
        (s.page || "the page");
      flag.title =
        "the model returned this quote but it was not found in the page text";
      meta.appendChild(flag);
    }
    box.appendChild(meta);
    wrap.appendChild(box);
  }
}

function dupFlag(card, question) {
  const flag = card.querySelector(".flag");
  const dup = model.qa_questions.includes(question.trim().toLowerCase());
  flag.style.display = dup ? "" : "none";
}

function render() {
  const cards = document.getElementById("cards");
  cards.innerHTML = "";
  model.candidates.forEach((cand, i) => {
    const card = document.createElement("div");
    card.className = "card" + (cand._kept ? "" : " discarded");
    card.innerHTML =
      "<div class='top'><span class='idx'>candidate " + (i + 1) + " / " +
      model.candidates.length + "</span>" +
      "<span class='flag'>duplicate of a qa.jsonl question</span></div>" +
      "<label>Question</label><textarea class='q'></textarea>" +
      "<label>Reference answer (the judge\\u2019s ground truth)</label>" +
      "<textarea class='a'></textarea>" +
      "<label>Citations (comma-separated stored-source keys)</label>" +
      "<input type='text' class='c'><div class='badges'></div>" +
      "<label>Pages used (click to verify in Obsidian)</label>" +
      "<div class='chips'></div>" +
      "<label class='support-label'>Supporting quotes (generation provenance)" +
      "</label><div class='quotes'></div>" +
      "<div class='row'><button class='toggle'></button></div>";
    const q = card.querySelector(".q"), a = card.querySelector(".a");
    const c = card.querySelector(".c"), toggle = card.querySelector(".toggle");
    q.value = cand.question; a.value = cand.reference_answer;
    c.value = cand.citations.join(", ");
    toggle.textContent = cand._kept ? "Discard" : "Restore";
    for (const page of cand.pages_used) {
      const info = model.pages[page] || { exists: false };
      let chip;
      if (info.exists) {
        chip = document.createElement("a");
        chip.className = "chip ok";
        chip.href = info.obsidian_uri;
        chip.title = "open in Obsidian to verify the extraction";
        chip.textContent = "\u2713 " + page;
      } else {
        chip = document.createElement("span");
        chip.className = "chip bad";
        chip.title = "no such page in the vault";
        chip.textContent = "\u2717 " + page;
      }
      card.querySelector(".chips").appendChild(chip);
    }
    citeBadges(card, cand.citations); dupFlag(card, cand.question);
    renderSupport(card, cand);
    q.addEventListener("input", () => {
      cand.question = q.value; dupFlag(card, q.value); setDirty(true);
    });
    a.addEventListener("input", () => {
      cand.reference_answer = a.value; setDirty(true);
    });
    c.addEventListener("input", () => {
      cand.citations = c.value.split(",").map(s => s.trim()).filter(Boolean);
      citeBadges(card, cand.citations); setDirty(true);
    });
    toggle.addEventListener("click", () => {
      cand._kept = !cand._kept;
      card.classList.toggle("discarded", !cand._kept);
      toggle.textContent = cand._kept ? "Discard" : "Restore";
      refreshHeader(); setDirty(true);
    });
    cards.appendChild(card);
  });
  refreshHeader();
}

async function save() {
  const button = document.getElementById("save");
  button.disabled = true;
  try {
    const accepted = keptRows().map(({ _kept, ...row }) => row);
    const response = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accepted }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || response.status);
    setDirty(false);
    const done = document.getElementById("done");
    done.style.display = "block";
    done.innerHTML = "";
    const strong = document.createElement("b");
    strong.textContent = "Saved " + result.count + " candidates";
    done.append(strong, document.createTextNode(
      " to " + result.written +
      " \u2014 tell Claude the review is done and it will run the freeze."));
  } catch (error) {
    alert("save failed: " + error.message);
  } finally {
    button.disabled = false;
  }
}

async function boot() {
  model = await (await fetch("/api/state")).json();
  model.candidates.forEach(c => { c._kept = true; });
  document.getElementById("topic").textContent = model.topic;
  document.getElementById("save").addEventListener("click", save);
  window.addEventListener("beforeunload", (event) => {
    if (dirty) event.preventDefault();
  });
  if (model.resumed) {
    const done = document.getElementById("done");
    done.style.display = "block";
    done.textContent =
      "Resumed from a previously saved review (" + model.loaded_from + ").";
  }
  render();
}
boot();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
