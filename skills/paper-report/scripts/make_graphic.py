# /// script
# requires-python = ">=3.10"
# dependencies = ["markdown>=3.5", "pillow>=10"]
# ///
"""Render explainer graphics (lineage timeline, comparison table) to PNG.

Community info-posts lean on summary images ("정리짤"). This script draws
them from a small JSON spec in the same plain style as the post, then saves
PNGs into <report_dir>/figures/ so report.md can embed them.

Spec examples:
    {"type": "timeline", "name": "lineage", "title": "번역 모델 계보",
     "items": [{"year": "2014", "label": "seq2seq", "org": "Google",
                "note": "RNN 인코더-디코더", "highlight": false}]}
    {"type": "compare", "name": "rnn_vs_attn", "title": "무엇이 달라졌나",
     "columns": ["", "RNN", "셀프 어텐션"],
     "rows": [["먼 단어 연결", "n단계", "1단계"]], "highlight_col": 2}

Every value in a graphic must also be in ledger.md. The spec's "source" field is
a reader-facing footnote rendered into the image (e.g. "출처: 원 논문 Table 1").

Usage:
    uv run make_graphic.py <report_dir> spec.json [more_specs.json ...]
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_cards import CSS, FONT_CSS, find_chrome, screenshot

WIDTH_CSS = 430  # same as the part PNG width, so the graphic sits flush with the text
EXTRA_CSS = """
body { padding: 8px 0 6px; } .wrap { width: 430px; }
.g-title { font-size: 18px; font-weight: 700; margin: 0 0 18px; letter-spacing: -0.015em; }
.tl { list-style: none; margin: 0; padding: 0; position: relative; }
.tl::before { content: ""; position: absolute; left: 68px; top: 6px; bottom: 6px; width: 2px; background: var(--line); }
.tl li { display: grid; grid-template-columns: 48px 22px 1fr; gap: 0 12px; align-items: start; padding: 8px 0; }
.tl .yr { font-weight: 700; font-variant-numeric: tabular-nums; text-align: right; padding-top: 2px; font-size: 15px; }
.tl .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--bg); border: 2px solid var(--muted);
  margin: 6px 0 0 3px; position: relative; z-index: 1; }
.tl .hl .dot { background: var(--link); border-color: var(--link); }
.tl .lab { font-weight: 700; font-size: 17px; line-height: 1.4; }
.tl .hl .lab { color: var(--link); }
.tl .org { font-size: 13px; color: var(--muted); margin-left: 6px; font-weight: 500; }
.tl .note { font-size: 14.5px; line-height: 1.55; margin-top: 2px; }
.cmp { width: 100%; border-collapse: collapse; font-size: 14px; }
.cmp th, .cmp td { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }
.cmp th { font-size: 13px; color: var(--muted); font-weight: 600; }
.cmp td:first-child { font-weight: 600; white-space: nowrap; }
.cmp .hl { color: var(--link); font-weight: 700; }
.g-src { margin-top: 14px; font-size: 11.5px; color: var(--muted); }
"""


def esc(v: object) -> str:
    return html.escape(str(v if v is not None else ""))


def timeline(spec: dict) -> str:
    rows = []
    for it in spec["items"]:
        cls = ' class="hl"' if it.get("highlight") else ""
        org = f'<span class="org">{esc(it.get("org"))}</span>' if it.get("org") else ""
        note = f'<div class="note">{esc(it.get("note"))}</div>' if it.get("note") else ""
        rows.append(
            f'<li{cls}><div class="yr">{esc(it.get("year"))}</div><div class="dot"></div>'
            f'<div><div class="lab">{esc(it.get("label"))}{org}</div>{note}</div></li>'
        )
    return f'<ol class="tl">{"".join(rows)}</ol>'


def _cell(tag: str, value: object, highlight: bool) -> str:
    cls = ' class="hl"' if highlight else ""
    return f"<{tag}{cls}>{esc(value)}</{tag}>"


def compare(spec: dict) -> str:
    hl = spec.get("highlight_col")
    head = "".join(_cell("th", c, i == hl) for i, c in enumerate(spec["columns"]))
    body = "".join(
        "<tr>" + "".join(_cell("td", c, i == hl) for i, c in enumerate(row)) + "</tr>" for row in spec["rows"]
    )
    return f'<table class="cmp"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


RENDERERS = {"timeline": timeline, "compare": compare}


def render(spec: dict) -> str:
    kind = spec.get("type")
    if kind not in RENDERERS:
        raise SystemExit(f"unknown graphic type {kind!r}; use one of {sorted(RENDERERS)}")
    src = f'<div class="g-src">{esc(spec["source"])}</div>' if spec.get("source") else ""
    inner = f'<div class="g-title">{esc(spec.get("title"))}</div>{RENDERERS[kind](spec)}{src}'
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{FONT_CSS}"><style>{CSS}{EXTRA_CSS}</style></head>'
        f'<body><div class="wrap">{inner}</div></body></html>'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", help="report folder")
    parser.add_argument("specs", nargs="+", help="JSON spec files (one graphic each, or a list)")
    args = parser.parse_args()
    report_dir = Path(args.dir).expanduser().resolve()
    chrome = find_chrome()
    if not chrome:
        raise SystemExit("Chrome/Chromium not found. Set CHROME_PATH.")
    build = report_dir / ".build"
    build.mkdir(exist_ok=True)
    (report_dir / "figures").mkdir(exist_ok=True)
    for spec_path in args.specs:
        data = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        for spec in data if isinstance(data, list) else [data]:
            name = spec.get("name") or Path(spec_path).stem
            page = build / f"graphic_{name}.html"
            page.write_text(render(spec), encoding="utf-8")
            out = report_dir / "figures" / f"g_{name}.png"
            if screenshot(chrome, page, out, WIDTH_CSS):
                print(out)


if __name__ == "__main__":
    main()
