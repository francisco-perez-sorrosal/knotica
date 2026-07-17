"""Golden-set review UI -- thin localhost wrapper over ``knotica.core.golden_review``.

Prefer the dashboard Golden pane (``knotica mcp --http`` → Golden tab). This
script remains for offline/stdlib-only review of one topic's staging file.

Run from the repo root::

    uv run python scripts/review_golden.py --topic agentic-systems
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from knotica.core.golden_review import (
    FLOOR,
    REVIEWED_NAME,
    TARGET_HIGH,
    load_golden_review,
    save_golden_review,
)
from knotica.store import LocalFSStore

# Re-export names the historical script surface documented.
STAGING_NAME = "golden.staging.jsonl"


class ReviewState:
    """Immutable-per-launch view of everything the UI needs."""

    def __init__(self, vault: Path, topic: str, vault_name: str | None = None) -> None:
        self.vault = vault
        self.topic = topic
        self.vault_name = vault_name or vault.name
        self.store = LocalFSStore(vault)
        self.reviewed_path = vault / topic / ".knotica" / "datasets" / REVIEWED_NAME

    def load(self) -> dict:
        return load_golden_review(
            self.store, self.vault, self.topic, vault_name=self.vault_name
        )

    def save(self, accepted: list[dict]) -> dict:
        return save_golden_review(self.store, self.vault, self.topic, accepted)


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
        except Exception as exc:  # noqa: BLE001 - surface any save failure to the UI
            self._respond_json(400, {"error": str(exc)})
            return
        self._respond_json(200, result)

    def log_message(self, fmt: str, *args: object) -> None:
        pass

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


# Standalone HTML kept for offline use; the dashboard Golden pane is canonical.
PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Golden-set review</title>
<style>
  :root {{
    --bg: #fdf6e3; --surface: #fbf3df; --surface-2: #eee8d5; --border: #ddd6c1;
    --text: #3b4b51; --muted: #6b7a80; --heading: #073642;
    --accent: #268bd2; --accent-strong: #1a6fb0;
    --good: #4f7a00; --good-fill: #859900; --bad: #c0392b; --bad-fill: #dc322f;
    --warn: #b58900; --hack: #cb4b16; --violet: #6c71c4; --cyan: #2aa198;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif;
    --fs-small: 13px;
    --shadow: 0 1px 3px rgba(7,54,66,.07), 0 6px 24px rgba(7,54,66,.05);
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:var(--sans);
         font-size:15px; line-height:1.6; }}
  a {{ color: var(--accent); }}
  header {{ position:sticky; top:0; z-index:5; background:var(--surface);
           border-bottom:1px solid var(--border); box-shadow:var(--shadow);
           padding:14px 28px; display:flex; gap:18px; align-items:center; flex-wrap:wrap; }}
  .eyebrow {{ font-family:var(--mono); font-size:var(--fs-small); letter-spacing:.12em;
             text-transform:uppercase; color:var(--accent-strong); margin:0; }}
  .eyebrow b {{ color:var(--heading); }}
  .kpi {{ display:flex; align-items:baseline; gap:8px; padding:4px 14px;
         border:1px solid var(--border); border-radius:999px; background:var(--surface-2); }}
  .kpi .v {{ font-weight:700; font-variant-numeric:tabular-nums; color:var(--heading); }}
  .kpi .k {{ font-size:var(--fs-small); color:var(--muted); text-transform:uppercase; }}
  .kpi.low .v {{ color:var(--warn); }} .kpi.good .v {{ color:var(--good); }}
  .kpi.high .v {{ color:var(--accent-strong); }}
  .dirty {{ color:var(--warn); font-weight:600; font-size:var(--fs-small); visibility:hidden; }}
  .dirty.on {{ visibility:visible; }}
  button.primary {{ background:var(--accent); color:#fff; border:0; padding:9px 20px;
                   border-radius:999px; font:inherit; font-weight:600; cursor:pointer; }}
  button.primary:disabled {{ opacity:.5; cursor:default; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 20px 28px 90px; }}
  .callout {{ border:1px solid var(--border); border-radius:12px; background:var(--surface-2);
             padding:14px 20px; margin:16px 0; font-size:14px; }}
  .callout.lesson {{ border-left:4px solid var(--violet); }}
  .callout.saved {{ border-left:4px solid var(--good-fill); background:#f3f6e0; display:none; }}
  .card {{ background:var(--surface); border:1px solid var(--border);
          border-left:4px solid var(--accent); border-radius:12px; padding:18px 22px;
          margin:18px 0; box-shadow:var(--shadow); }}
  .card.discarded {{ opacity:.42; border-left-color:var(--bad-fill); filter:grayscale(.4); }}
  .card .top {{ display:flex; justify-content:space-between; gap:10px; align-items:baseline; }}
  .idx {{ font-family:var(--mono); font-size:12px; letter-spacing:.12em;
         text-transform:uppercase; color:var(--accent-strong); }}
  .flag {{ font-family:var(--mono); font-size:12px; font-weight:700; padding:2px 10px;
          border-radius:999px; background:#fbeae7; color:var(--hack); border:1px solid #e6b8ae; }}
  label {{ display:block; font-family:var(--mono); font-size:12px; font-weight:600;
          color:var(--muted); margin:14px 0 4px; text-transform:uppercase; letter-spacing:.08em; }}
  textarea, input[type=text] {{ width:100%; border:1px solid var(--border); border-radius:8px;
          padding:9px 12px; font:inherit; font-size:14px; background:#fffdf5; color:var(--text); }}
  textarea.q {{ min-height:40px; }} textarea.a {{ min-height:104px; }}
  .badges, .chips {{ display:flex; gap:7px; flex-wrap:wrap; margin-top:6px; }}
  .cite, .chip {{ display:inline-block; font-family:var(--mono); font-size:12px;
          padding:2px 11px; border-radius:999px; border:1px solid var(--border);
          background:var(--surface-2); text-decoration:none; color:var(--muted); }}
  .cite.ok, a.chip.ok {{ color:var(--good); border-color:#b9c46a; background:#f3f6e0; }}
  .cite.bad, .chip.bad {{ color:var(--bad); border-color:#e6b8ae; background:#fbeae7; }}
  .quote {{ background:var(--surface-2); border:1px solid var(--border); border-left:4px solid var(--cyan);
           border-radius:10px; padding:10px 16px; margin:8px 0; }}
  .quote blockquote {{ margin:0; font-size:13.5px; white-space:pre-wrap; }}
  .quote .meta {{ font-family:var(--mono); font-size:12px; color:var(--muted); margin-top:6px; }}
  .row {{ display:flex; justify-content:flex-end; margin-top:14px; }}
  button.toggle {{ font:inherit; font-size:var(--fs-small); background:var(--surface);
          border:1px solid var(--border); border-radius:999px; padding:6px 16px; cursor:pointer; color:var(--muted); }}
</style>
</head>
<body>
<header>
  <p class="eyebrow">Golden-set review · <b id="topic"></b></p>
  <span id="counts" class="kpi good"><span class="v"></span><span class="k"></span></span>
  <span id="dirty" class="dirty">unsaved changes</span>
  <span style="flex:1"></span>
  <button id="save" class="primary">Save reviewed set</button>
</header>
<main>
<div class="callout lesson">
  Keep <b>{FLOOR}–{TARGET_HIGH}</b> strong candidates. Prefer the knotica dashboard
  <b>Golden</b> pane when available. Discard weak or duplicate (orange) cards.
</div>
<div id="done" class="callout saved"></div>
<div id="cards"></div>
</main>
<script>
"use strict";
let model = null, dirty = false;
function setDirty(on) {{ dirty = on; document.getElementById("dirty").classList.toggle("on", on); }}
function keptRows() {{ return model.candidates.filter(c => c._kept); }}
function refreshHeader() {{
  const n = keptRows().length, el = document.getElementById("counts");
  el.querySelector(".v").textContent = n + " / " + model.candidates.length;
  el.querySelector(".k").textContent = "kept";
  el.className = "kpi " + (n < model.floor ? "low" : n <= model.target_high ? "good" : "high");
}}
function citeBadges(card, citations) {{
  const wrap = card.querySelector(".badges"); wrap.innerHTML = "";
  for (const key of citations) {{
    const ok = model.source_keys.includes(key);
    const badge = document.createElement(ok ? "a" : "span");
    badge.className = "cite " + (ok ? "ok" : "bad");
    badge.textContent = (ok ? "✓ " : "✗ ") + key;
    if (ok) badge.href = model.citation_links[key];
    wrap.appendChild(badge);
  }}
}}
function renderSupport(card, cand) {{
  const wrap = card.querySelector(".quotes");
  const entries = cand.support || [];
  if (!entries.length) {{ wrap.style.display = "none"; return; }}
  for (const s of entries) {{
    const box = document.createElement("div"); box.className = "quote";
    const quote = document.createElement("blockquote"); quote.textContent = s.quote; box.appendChild(quote);
    wrap.appendChild(box);
  }}
}}
function dupFlag(card, question) {{
  card.querySelector(".flag").style.display =
    model.qa_questions.includes(question.trim().toLowerCase()) ? "" : "none";
}}
function render() {{
  const cards = document.getElementById("cards"); cards.innerHTML = "";
  model.candidates.forEach((cand, i) => {{
    const card = document.createElement("div");
    card.className = "card" + (cand._kept ? "" : " discarded");
    card.innerHTML = "<div class='top'><span class='idx'>candidate " + (i+1) +
      "</span><span class='flag'>duplicate</span></div>" +
      "<label>Question</label><textarea class='q'></textarea>" +
      "<label>Reference answer</label><textarea class='a'></textarea>" +
      "<label>Citations</label><input type='text' class='c'><div class='badges'></div>" +
      "<label>Pages</label><div class='chips'></div><div class='quotes'></div>" +
      "<div class='row'><button class='toggle'></button></div>";
    const q = card.querySelector(".q"), a = card.querySelector(".a");
    const c = card.querySelector(".c"), toggle = card.querySelector(".toggle");
    q.value = cand.question; a.value = cand.reference_answer; c.value = cand.citations.join(", ");
    toggle.textContent = cand._kept ? "Discard" : "Restore";
    for (const page of cand.pages_used) {{
      const info = model.pages[page] || {{ exists: false }};
      const chip = document.createElement(info.exists ? "a" : "span");
      chip.className = "chip " + (info.exists ? "ok" : "bad");
      chip.textContent = (info.exists ? "✓ " : "✗ ") + page;
      if (info.exists) chip.href = info.obsidian_uri;
      card.querySelector(".chips").appendChild(chip);
    }}
    citeBadges(card, cand.citations); dupFlag(card, cand.question); renderSupport(card, cand);
    q.oninput = () => {{ cand.question = q.value; dupFlag(card, q.value); setDirty(true); }};
    a.oninput = () => {{ cand.reference_answer = a.value; setDirty(true); }};
    c.oninput = () => {{
      cand.citations = c.value.split(",").map(s => s.trim()).filter(Boolean);
      citeBadges(card, cand.citations); setDirty(true);
    }};
    toggle.onclick = () => {{
      cand._kept = !cand._kept; card.classList.toggle("discarded", !cand._kept);
      toggle.textContent = cand._kept ? "Discard" : "Restore"; refreshHeader(); setDirty(true);
    }};
    cards.appendChild(card);
  }});
  refreshHeader();
}}
async function save() {{
  const button = document.getElementById("save"); button.disabled = true;
  try {{
    const accepted = keptRows().map(({{ _kept, ...row }}) => row);
    const response = await fetch("/api/save", {{
      method: "POST", headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ accepted }}),
    }});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || response.status);
    setDirty(false);
    const done = document.getElementById("done");
    done.style.display = "block";
    done.textContent = "Saved " + result.count + " candidates to " + result.written;
  }} catch (error) {{ alert("save failed: " + error.message); }}
  finally {{ button.disabled = false; }}
}}
async function boot() {{
  model = await (await fetch("/api/state")).json();
  model.candidates.forEach(c => {{ c._kept = true; }});
  document.getElementById("topic").textContent = model.topic;
  document.getElementById("save").onclick = save;
  if (model.resumed) {{
    const done = document.getElementById("done");
    done.style.display = "block";
    done.textContent = "Resumed from " + model.loaded_from;
  }}
  render();
}}
boot();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
