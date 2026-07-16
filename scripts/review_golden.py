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

    def __init__(self, vault: Path, topic: str) -> None:
        self.vault = vault
        self.topic = topic
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
        source_keys = sorted(p.stem for p in self.sources_dir.glob("*.md"))
        return {
            "topic": self.topic,
            "vault_name": self.vault.name,
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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()
    state = ReviewState(_resolve_vault(args.vault), args.topic)
    state.load()  # fail fast on a missing/broken staging file before serving
    serve(state, args.port, open_browser=not args.no_browser)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Golden-set review</title>
<style>
  :root { --ok:#1a7f37; --bad:#b42318; --warn:#9a6700; --line:#d0d7de;
          --muted:#57606a; --bg:#f6f8fa; }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 ui-sans-serif,system-ui,sans-serif;
         background:var(--bg); color:#1f2328; }
  header { position:sticky; top:0; z-index:2; background:#fff;
           border-bottom:1px solid var(--line); padding:10px 20px;
           display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; }
  .band { padding:2px 10px; border-radius:999px; font-weight:600; }
  .band.low { background:#fff1e5; color:var(--warn); }
  .band.good { background:#dafbe1; color:var(--ok); }
  .band.high { background:#ddf4ff; color:#0969da; }
  .dirty { color:var(--warn); font-weight:600; visibility:hidden; }
  .dirty.on { visibility:visible; }
  button.primary { background:#1f883d; color:#fff; border:0; padding:8px 16px;
                   border-radius:6px; font-weight:600; cursor:pointer; }
  button.primary:disabled { background:#94d3a2; cursor:default; }
  #guide { max-width:900px; margin:14px auto 0; padding:0 20px; color:var(--muted);
           font-size:14px; }
  #cards { max-width:900px; margin:0 auto; padding:8px 20px 80px; }
  .card { background:#fff; border:1px solid var(--line); border-radius:8px;
          padding:14px 16px; margin:14px 0; }
  .card.discarded { opacity:.45; }
  .card .top { display:flex; justify-content:space-between; gap:10px;
               align-items:baseline; margin-bottom:6px; }
  .idx { color:var(--muted); font-size:13px; }
  .flag { font-size:12px; font-weight:700; padding:1px 8px; border-radius:999px; }
  .flag.dup { background:#ffebe9; color:var(--bad); }
  label { display:block; font-size:12px; font-weight:600; color:var(--muted);
          margin:10px 0 2px; text-transform:uppercase; letter-spacing:.04em; }
  textarea, input[type=text] { width:100%; border:1px solid var(--line);
          border-radius:6px; padding:8px 10px; font:inherit; background:#fff; }
  textarea:focus, input:focus { outline:2px solid #0969da33; }
  textarea.q { min-height:38px; } textarea.a { min-height:96px; }
  .badges { margin-top:4px; display:flex; gap:6px; flex-wrap:wrap; }
  .cite { font:12px ui-monospace,monospace; padding:1px 8px; border-radius:999px; }
  .cite.ok { background:#dafbe1; color:var(--ok); }
  .cite.bad { background:#ffebe9; color:var(--bad); }
  .quotes { margin-top:4px; }
  .quote { border-left:3px solid var(--line); margin:8px 0; padding:2px 12px; }
  .quote blockquote { margin:0; font-size:13px; color:#333; white-space:pre-wrap; }
  .quote .meta { font-size:12px; color:var(--muted); margin-top:2px; }
  .quote .meta a { color:#0969da; text-decoration:none; }
  .quote .meta a:hover { text-decoration:underline; }
  .unlocated { color:var(--warn); font-weight:600; }
  .chips { display:flex; gap:6px; flex-wrap:wrap; margin-top:2px; }
  .chip { font:12px ui-monospace,monospace; background:var(--bg); padding:1px 8px;
          border-radius:999px; color:var(--muted); text-decoration:none; }
  a.chip.ok { background:#dafbe1; color:var(--ok); }
  a.chip.ok:hover { outline:1px solid var(--ok); }
  .chip.bad { background:#ffebe9; color:var(--bad); }
  .row { display:flex; justify-content:flex-end; margin-top:10px; }
  button.toggle { background:#fff; border:1px solid var(--line); border-radius:6px;
                  padding:5px 12px; cursor:pointer; }
  #done { display:none; max-width:900px; margin:10px auto; padding:12px 20px;
          background:#dafbe1; border:1px solid var(--ok); border-radius:8px; }
</style>
</head>
<body>
<header>
  <h1>Golden-set review</h1>
  <span id="counts" class="band good"></span>
  <span id="dirty" class="dirty">unsaved changes</span>
  <span style="flex:1"></span>
  <button id="save" class="primary">Save reviewed set</button>
</header>
<div id="guide">
  Keep 20&ndash;30 strong candidates. For each card: is the <b>question</b> specific and
  answerable from the wiki? Does the <b>reference answer</b> say exactly what a correct
  answer must contain (tighten it &mdash; it is the judge's ground truth)? Do the
  <b>citations</b> resolve (green) and genuinely support the answer? Discard weak or
  redundant candidates; a red <b>duplicate</b> flag means the question already exists in
  the flywheel trainset and must be discarded or reworded.
</div>
<div id="done"></div>
<div id="cards"></div>
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
  el.textContent = n + " kept of " + model.candidates.length;
  el.className = "band " + (n < model.floor ? "low" : n <= model.target_high ? "good" : "high");
  el.title = n < model.floor
    ? "below the " + model.floor + "-candidate stability floor"
    : "target band is " + model.floor + "\\u2013" + model.target_high;
}

function citeBadges(card, citations) {
  const wrap = card.querySelector(".badges");
  wrap.innerHTML = "";
  for (const key of citations) {
    const ok = model.source_keys.includes(key);
    const badge = document.createElement(ok ? "a" : "span");
    badge.className = "cite " + (ok ? "ok" : "bad");
    badge.textContent = (ok ? "\\u2713 " : "\\u2717 ") + key;
    if (ok) {
      badge.href = model.citation_links[key];
      badge.title = "open the stored source in Obsidian";
      badge.style.textDecoration = "none";
    } else {
      badge.title = "no such stored source";
    }
    wrap.appendChild(badge);
  }
}

function advUri(pageInfo, line) {
  return "obsidian://adv-uri?vault=" + encodeURIComponent(model.vault_name) +
    "&filepath=" + encodeURIComponent(pageInfo.relative) + "&line=" + line;
}

function renderSupport(card, cand) {
  const wrap = card.querySelector(".quotes");
  const entries = cand.support || [];
  if (!entries.length) { wrap.style.display = "none"; return; }
  for (const s of entries) {
    const box = document.createElement("div");
    box.className = "quote";
    const quote = document.createElement("blockquote");
    quote.textContent = s.quote;
    box.appendChild(quote);
    const meta = document.createElement("div");
    meta.className = "meta";
    const pageInfo = model.pages[s.page];
    if (s.verified && pageInfo && pageInfo.exists) {
      const label = document.createElement("span");
      label.textContent = s.page + ", lines " + s.line_start + "\\u2013" + s.line_end + " \\u00b7 ";
      const jump = document.createElement("a");
      jump.href = advUri(pageInfo, s.line_start);
      jump.textContent = "\\u2197 jump to line";
      jump.title = "positions the cursor on the line (requires the Advanced URI plugin)";
      const open = document.createElement("a");
      open.href = pageInfo.obsidian_uri;
      open.textContent = "open page";
      open.title = "open in Obsidian (no plugin needed)";
      meta.append(label, jump, document.createTextNode(" \\u00b7 "), open);
    } else {
      const flag = document.createElement("span");
      flag.className = "unlocated";
      flag.textContent = "\\u26a0 quote not located verbatim in " + (s.page || "the page");
      flag.title = "the model returned this quote but it was not found in the page text";
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
      '<div class="top"><span class="idx">candidate ' + (i + 1) + "</span>" +
      '<span class="flag dup">duplicate of a qa.jsonl question</span></div>' +
      '<label>Question</label><textarea class="q"></textarea>' +
      '<label>Reference answer (the judge\\u2019s ground truth)</label>' +
      '<textarea class="a"></textarea>' +
      '<label>Citations (comma-separated stored-source keys)</label>' +
      '<input type="text" class="c"><div class="badges"></div>' +
      '<label>Pages used (click to verify in Obsidian)</label><div class="chips"></div>' +
      '<label>Supporting quotes (generation provenance)</label><div class="quotes"></div>' +
      '<div class="row"><button class="toggle"></button></div>';
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
        chip.textContent = "\\u2713 " + page;
      } else {
        chip = document.createElement("span");
        chip.className = "chip bad";
        chip.title = "no such page in the vault";
        chip.textContent = "\\u2717 " + page;
      }
      card.querySelector(".chips").appendChild(chip);
    }
    citeBadges(card, cand.citations); dupFlag(card, cand.question);
    renderSupport(card, cand);
    if (!(cand.support || []).length) {
      card.querySelectorAll("label")[3].style.display = "none";
    }
    q.addEventListener("input", () => { cand.question = q.value; dupFlag(card, q.value); setDirty(true); });
    a.addEventListener("input", () => { cand.reference_answer = a.value; setDirty(true); });
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
    done.textContent = "Saved " + result.count + " candidates to " + result.written +
      " \\u2014 tell Claude the review is done and it will run the freeze.";
  } catch (error) {
    alert("save failed: " + error.message);
  } finally {
    button.disabled = false;
  }
}

async function boot() {
  model = await (await fetch("/api/state")).json();
  model.candidates.forEach(c => { c._kept = true; });
  document.getElementById("save").addEventListener("click", save);
  window.addEventListener("beforeunload", (event) => {
    if (dirty) event.preventDefault();
  });
  if (model.resumed) {
    const done = document.getElementById("done");
    done.style.display = "block";
    done.textContent = "Resumed from a previously saved review (" + model.loaded_from + ").";
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
