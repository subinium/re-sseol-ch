# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24", "pillow>=10"]
# ///
"""Extract figures/tables from a paper into <dir>/figures and render pages.

Default run:
  1. arXiv HTML figures (original images + captions) when meta has arxiv_id.
  2. PDF caption-anchored crops for every "Figure N:" / "Table N:" caption.
  3. Page renders in <dir>/pages for visual QA and manual crops.

Manual crop (fractions of the page, 0..1, origin top-left):
    uv run extract_figures.py DIR crop --page 3 --bbox 0.08,0.10,0.92,0.48 --name fig1_fix
Crop from another paper (e.g. a prior work) straight into the main report:
    uv run extract_figures.py PRIOR_DIR crop --page 6 --bbox ... --name prior_2014_x --dest MAIN_DIR
Quote crop (the passage from the line with --start to the line with --end, same column):
    uv run extract_figures.py DIR quote --page 13 --start "An alternative is" --end "escrow a" --name q_registry
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).parent))
from _common import http_get, log, read_json, write_json

CAPTION_RE = re.compile(
    r"^\s*(Figure|Fig\.?|FIGURE|FIG\.?|Table|TABLE|Extended Data Fig\.?)\s*"
    r"(S?[A-Z]?\d+)\s*[:.|]",
)
FIG_DPI = 220
PAGE_DPI = 100
MAX_PAGES_RENDERED = 40
PAD_PT = 4.0
BODY_PARAGRAPH_MIN_CHARS = 220
MIN_GRAPHIC_SIDE_PT = 8.0
BODY_MIN_WIDTH_FRAC = 0.45
BODY_MIN_AVG_LINE_CHARS = 40
BODY_MAX_DIGIT_RATIO = 0.25
MARGIN_PT = 30.0
GROW_GAP_PT = 14.0
HEADER_FRAC = 0.09
RUNNING_HEADER_MAX_CHARS = 60
QUOTE_DARK_LEVEL = 160  # grey level below which a pixel counts as ink
QUOTE_SLIVER_FRAC = 0.5  # an edge ink band shorter than this share of a line is a neighbour's fragment
TRIM_THRESHOLD = 12
TRIM_BORDER_PX = 12


# ---------------------------------------------------------------- arXiv HTML
class _FigureParser(HTMLParser):
    """Collect <figure> elements with their <img> sources and <figcaption> text."""

    def __init__(self) -> None:
        super().__init__()
        self.figures: list[dict] = []
        self._stack: list[dict] = []
        self._in_caption = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "figure":
            self._stack.append({"imgs": [], "caption": "", "cls": a.get("class") or "", "id": a.get("id") or ""})
        elif tag == "img" and self._stack and a.get("src"):
            self._stack[-1]["imgs"].append(a["src"])
        elif tag == "figcaption" and self._stack:
            self._in_caption += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "figcaption" and self._in_caption:
            self._in_caption -= 1
        elif tag == "figure" and self._stack:
            fig = self._stack.pop()
            if self._stack:  # nested subfigure: bubble images up to the parent
                self._stack[-1]["imgs"].extend(fig["imgs"])
                if not self._stack[-1]["caption"] and fig["caption"]:
                    self._stack[-1].setdefault("sub_captions", []).append(fig["caption"])
            else:
                self.figures.append(fig)

    def handle_data(self, data: str) -> None:
        if self._in_caption and self._stack:
            self._stack[-1]["caption"] += data


def arxiv_html_figures(arxiv_id: str, fig_dir: Path) -> list[dict]:
    """Download figure images from the arXiv HTML rendering of a paper."""
    base = f"https://arxiv.org/html/{arxiv_id}"
    try:
        page = http_get(base, retries=2).decode("utf-8", "ignore")
    except Exception as error:  # noqa: BLE001 - HTML is optional
        log(f"  arXiv HTML unavailable ({error}); using PDF crops only")
        return []
    if "ltx_figure" not in page and "<figure" not in page:
        log("  arXiv HTML has no figures")
        return []
    # Relative image paths resolve against the versioned document folder.
    # arXiv img src looks like "<id>v<N>/Figures/x.png", relative to /html/.
    m = re.search(r'<base href="([^"]+)"', page)
    base_href = urllib.parse.urljoin(base, m.group(1)) if m else base
    parser = _FigureParser()
    parser.feed(page)
    out: list[dict] = []
    for idx, fig in enumerate(parser.figures, start=1):
        caption = " ".join(html.unescape(fig["caption"]).split())
        label_m = CAPTION_RE.match(caption)
        label = f"{label_m.group(1)} {label_m.group(2)}" if label_m else f"html{idx}"
        kind = "table" if "table" in fig["cls"].lower() or label.lower().startswith("tab") else "figure"
        files = []
        for j, src in enumerate(dict.fromkeys(fig["imgs"]), start=1):
            url = urllib.parse.urljoin(base_href, src)
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                ext = ".png"
            name = f"html_{_safe(label)}_{j}{ext}"
            try:
                (fig_dir / name).write_bytes(http_get(url, retries=2))
                files.append(name)
            except Exception as error:  # noqa: BLE001
                log(f"  failed image {url}: {error}")
        if files or kind == "table":
            out.append(
                {
                    "label": label,
                    "kind": kind,
                    "caption": caption,
                    "files": files,
                    "method": "arxiv_html",
                    "note": "table rendered as HTML (no image); crop from PDF" if kind == "table" and not files else "",
                }
            )
    log(f"  arXiv HTML: {len(out)} figures/tables")
    return out


# ---------------------------------------------------------------- PDF crops
def _safe(label: str) -> str:
    return (
        re.sub(
            r"[^A-Za-z0-9]+", "", label.replace("Figure", "fig").replace("Table", "tab").replace("Fig", "fig")
        ).lower()
        or "x"
    )


def _graphics(page: fitz.Page) -> list[fitz.Rect]:
    """Bounding boxes of raster images and vector drawings on a page."""
    rects: list[fitz.Rect] = []
    page_area = abs(page.rect)
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"])
        if r.width > MIN_GRAPHIC_SIDE_PT and r.height > MIN_GRAPHIC_SIDE_PT:
            rects.append(r)
    for d in page.get_drawings():
        r = fitz.Rect(d["rect"])
        if abs(r) > 0.9 * page_area:  # page background
            continue
        if r.width < 1 and r.height < 1:
            continue
        rects.append(r)
    return rects


def _column_range(cap: fitz.Rect, page: fitz.Rect) -> tuple[float, float]:
    """Horizontal range for the figure: one column or full width."""
    if cap.width < 0.55 * page.width:
        mid = page.width / 2
        if cap.x1 <= mid + 10:
            return page.x0 + MARGIN_PT / 2, mid
        if cap.x0 >= mid - 10:
            return mid, page.x1 - MARGIN_PT / 2
    return page.x0 + MARGIN_PT / 2, page.x1 - MARGIN_PT / 2


def _overlaps_x(r: fitz.Rect, x0: float, x1: float) -> bool:
    return r.x1 > x0 + 2 and r.x0 < x1 - 2


def _is_body(block: dict, col_w: float) -> bool:
    """True for prose paragraphs (long lines, few digits), false for tables/labels."""
    lines = ["".join(s["text"] for s in ln["spans"]) for ln in block["lines"]]
    text = "".join(lines)
    if len(text) < BODY_PARAGRAPH_MIN_CHARS:
        return False
    width = block["bbox"][2] - block["bbox"][0]
    avg_line = len(text) / max(len(lines), 1)
    digit_ratio = sum(ch.isdigit() for ch in text) / len(text)
    return (
        width >= BODY_MIN_WIDTH_FRAC * col_w
        and avg_line >= BODY_MIN_AVG_LINE_CHARS
        and digit_ratio < BODY_MAX_DIGIT_RATIO
    )


def _is_running_header(block: dict, header_y: float) -> bool:
    """Short text in the top strip of the page (journal name, running title)."""
    text = "".join(s["text"] for ln in block["lines"] for s in ln["spans"])
    return block["bbox"][3] <= header_y and len(text.strip()) < RUNNING_HEADER_MAX_CHARS


def _grow(region: fitz.Rect, pool: list[fitz.Rect], gap: float) -> fitz.Rect:
    """Repeatedly absorb rects that overlap or sit within `gap` points vertically."""
    changed = True
    remaining = list(pool)
    while changed:
        changed = False
        for r in list(remaining):
            v_gap = max(r.y0 - region.y1, region.y0 - r.y1, 0)
            h_ok = r.x1 > region.x0 - gap and r.x0 < region.x1 + gap
            if v_gap <= gap and h_ok:
                region |= r
                remaining.remove(r)
                changed = True
    return region


def _region(
    page: fitz.Page, cap: fitz.Rect, kind: str, blocks: list[dict], captions: list[fitz.Rect]
) -> tuple[fitz.Rect | None, str]:
    """Estimate the figure/table region anchored at a caption.

    Figures sit above their caption, tables usually below it. The band between
    the caption and the nearest prose paragraph (or other caption) is searched
    for graphics; the region then grows through adjacent short text (axis
    labels, subfigure titles, table cells) but never across a prose paragraph.
    """
    x0, x1 = _column_range(cap, page.rect)
    col_w = x1 - x0
    header_y = page.rect.y0 + HEADER_FRAC * page.rect.height
    text_blocks = [b for b in blocks if b.get("type") == 0 and not _is_running_header(b, header_y)]
    all_graphics = [g for g in _graphics(page) if not (g.y1 <= header_y and g.height < 3)]
    graphics = [g for g in all_graphics if _overlaps_x(g, x0, x1)]
    others = [c for c in captions if c != cap and _overlaps_x(c, x0, x1)]
    above = kind == "figure"

    def in_col(b: dict) -> bool:
        return _overlaps_x(fitz.Rect(b["bbox"]), x0, x1)

    if above:
        stop = max([c.y1 for c in others if c.y1 <= cap.y0] + [page.rect.y0 + MARGIN_PT / 2])
        body = [
            fitz.Rect(b["bbox"]).y1
            for b in text_blocks
            if in_col(b) and _is_body(b, col_w) and fitz.Rect(b["bbox"]).y1 <= cap.y0 + 1
        ]
        top, bottom = max([stop] + body), cap.y0
    else:
        stop = min([c.y0 for c in others if c.y0 >= cap.y1] + [page.rect.y1 - MARGIN_PT / 2])
        body = [
            fitz.Rect(b["bbox"]).y0
            for b in text_blocks
            if in_col(b) and _is_body(b, col_w) and fitz.Rect(b["bbox"]).y0 >= cap.y1 - 1
        ]
        top, bottom = cap.y1, min([stop] + body)

    band = fitz.Rect(x0, top - 1, x1, bottom + 1)
    band_graphics = [g for g in graphics if band.contains(g) or (g & band).get_area() > 0.8 * g.get_area()]
    band_text = [
        fitz.Rect(b["bbox"])
        for b in text_blocks
        if in_col(b)
        and not _is_body(b, col_w)
        and fitz.Rect(b["bbox"]) != cap
        and band.intersects(fitz.Rect(b["bbox"]))
    ]

    if band_graphics:
        seed = fitz.Rect(band_graphics[0])
        for g in band_graphics[1:]:
            seed |= g
        method = "graphics"
    elif band_text:
        # Text-only table/figure: start from the block nearest to the caption.
        nearest = min(band_text, key=lambda r: abs((r.y1 if above else r.y0) - (cap.y0 if above else cap.y1)))
        seed, method = fitz.Rect(nearest), "text_grow"
    elif kind == "table":
        return _region(page, cap, "figure", blocks, captions)[0], "table_caption_below"
    else:
        seed, method = fitz.Rect(x0, top, x1, bottom), "band_fallback"

    region = _grow(seed, band_text + band_graphics, GROW_GAP_PT) & band
    # A single-column caption can sit under a full-width figure (Nature style):
    # when the crop touches the column edge, grow across the page width.
    if region.x1 >= x1 - 3 or region.x0 <= x0 + 3:
        full = fitz.Rect(page.rect.x0 + MARGIN_PT / 2, band.y0, page.rect.x1 - MARGIN_PT / 2, band.y1)
        wide_pool = [g for g in all_graphics if full.contains(g) and g.y1 > region.y0 and g.y0 < region.y1]
        wide_text = [
            fitz.Rect(b["bbox"])
            for b in text_blocks
            if not _is_body(b, col_w) and full.contains(fitz.Rect(b["bbox"])) and fitz.Rect(b["bbox"]) != cap
        ]
        region = _grow(region, wide_pool + wide_text, GROW_GAP_PT) & full
    region = fitz.Rect(region.x0 - PAD_PT, region.y0 - PAD_PT, region.x1 + PAD_PT, region.y1 + PAD_PT)
    region &= page.rect
    if region.is_empty or region.height < 20 or region.width < 20:
        return None, "empty"
    return region, method


def _trim(path: Path) -> None:
    """Trim uniform background margins from a PNG, keeping a small border."""
    img = Image.open(path).convert("RGB")
    bg = Image.new("RGB", img.size, img.getpixel((0, 0)))
    diff = ImageChops.difference(img, bg).convert("L").point(lambda v: 255 if v > TRIM_THRESHOLD else 0)
    box = diff.getbbox()
    if not box:
        return
    b = TRIM_BORDER_PX
    box = (max(box[0] - b, 0), max(box[1] - b, 0), min(box[2] + b, img.width), min(box[3] + b, img.height))
    img.crop(box).save(path)


def pdf_crops(pdf: Path, fig_dir: Path, dpi: int, with_caption: bool) -> list[dict]:
    """Crop every captioned figure/table from the PDF."""
    doc = fitz.open(pdf)
    out: list[dict] = []
    seen: set[str] = set()
    for pno, page in enumerate(doc, start=1):
        blocks = page.get_text("dict")["blocks"]
        caps: list[tuple[fitz.Rect, str, str]] = []
        for b in blocks:
            if b.get("type") != 0:
                continue
            text = " ".join(s["text"] for ln in b["lines"] for s in ln["spans"]).strip()
            m = CAPTION_RE.match(text)
            if m:
                caps.append((fitz.Rect(b["bbox"]), f"{m.group(1).rstrip('.')} {m.group(2)}", text))
        cap_rects = [c[0] for c in caps]
        for rect, label, text in caps:
            norm = label.replace("Fig ", "Figure ").replace("FIGURE", "Figure").replace("TABLE", "Table")
            kind = "table" if norm.lower().startswith("tab") else "figure"
            key = norm.lower()
            dup = key in seen
            seen.add(key)
            region, method = _region(page, rect, kind, blocks, cap_rects)
            if region is None:
                out.append(
                    {
                        "label": norm,
                        "kind": kind,
                        "page": pno,
                        "caption": text,
                        "files": [],
                        "method": "failed",
                        "duplicate_label": dup,
                    }
                )
                continue
            if with_caption:
                region |= rect
            name = f"{_safe(norm)}_p{pno}{'_dup' if dup else ''}.png"
            page.get_pixmap(clip=region, dpi=dpi).save(fig_dir / name)
            _trim(fig_dir / name)
            out.append(
                {
                    "label": norm,
                    "kind": kind,
                    "page": pno,
                    "caption": text,
                    "files": [name],
                    "method": method,
                    "duplicate_label": dup,
                    "bbox_pt": [round(v, 1) for v in region],
                    "bbox_frac": [
                        round(region.x0 / page.rect.width, 3),
                        round(region.y0 / page.rect.height, 3),
                        round(region.x1 / page.rect.width, 3),
                        round(region.y1 / page.rect.height, 3),
                    ],
                }
            )
    doc.close()
    log(f"  PDF: {len(out)} captions found")
    return out


def render_pages(pdf: Path, page_dir: Path, dpi: int, max_pages: int) -> int:
    """Render page PNGs for visual QA."""
    doc = fitz.open(pdf)
    n = min(len(doc), max_pages)
    for i in range(n):
        doc[i].get_pixmap(dpi=dpi).save(page_dir / f"p{i + 1:03d}.png")
    doc.close()
    return n


def manual_crop(
    report_dir: Path, page_no: int, bbox: str, name: str, dpi: int, rotate: int = 0, dest: Path | None = None
) -> None:
    """Crop a region given as page fractions (x0,y0,x1,y1), optionally rotate it
    clockwise (for sideways figures), and register it in the manifest."""
    vals = [float(v) for v in bbox.split(",")]
    if len(vals) != 4 or not all(0 <= v <= 1 for v in vals):
        raise SystemExit("--bbox must be 4 fractions in [0,1]: x0,y0,x1,y1")
    doc = fitz.open(report_dir / "paper.pdf")
    if not 1 <= page_no <= len(doc):
        raise SystemExit(f"--page must be between 1 and {len(doc)}")
    page = doc[page_no - 1]
    w, h = page.rect.width, page.rect.height
    clip = fitz.Rect(vals[0] * w, vals[1] * h, vals[2] * w, vals[3] * h)
    fig_dir = (dest or report_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{name}.png"
    page.get_pixmap(clip=clip, dpi=dpi).save(fig_dir / fname)
    doc.close()
    if rotate:
        Image.open(fig_dir / fname).rotate(-rotate, expand=True, fillcolor="white").save(fig_dir / fname)
    _trim(fig_dir / fname)
    manifest_path = fig_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {"items": []}
    manifest["items"].append(
        {
            "label": name,
            "kind": "manual",
            "page": page_no,
            "caption": "",
            "files": [fname],
            "method": "manual",
            "bbox_frac": vals,
            "rotate_cw": rotate,
        }
    )
    write_json(manifest_path, manifest)
    print(fig_dir / fname)


def _find(page: fitz.Page, phrase: str, flag: str) -> list[fitz.Rect]:
    hits = page.search_for(phrase)
    if not hits:
        raise SystemExit(
            f"{flag} {phrase!r} not found on the page. Use 2-6 words that sit on one "
            "printed line (no hyphenated line breaks); check text.txt for the exact wording."
        )
    return hits


def _drop_edge_slivers(path: Path) -> None:
    """Remove fragments of neighbouring lines (descenders, hyphens) that glyphs
    push past their line boxes into the top or bottom edge of a quote crop."""
    img = Image.open(path).convert("L")
    w, h = img.size
    px = img.load()
    dark = [any(px[x, y] < QUOTE_DARK_LEVEL for x in range(0, w, 2)) for y in range(h)]
    runs: list[list[int]] = []  # [start, end) of consecutive dark rows
    for y, d in enumerate(dark):
        if d and (not runs or runs[-1][1] != y):
            runs.append([y, y + 1])
        elif d:
            runs[-1][1] = y + 1
    if len(runs) < 3:
        return
    line_h = sorted(e - s for s, e in runs)[len(runs) // 2]
    top, bottom = 0, h
    if runs[0][1] - runs[0][0] < QUOTE_SLIVER_FRAC * line_h:
        top = (runs[0][1] + runs[1][0]) // 2
    if runs[-1][1] - runs[-1][0] < QUOTE_SLIVER_FRAC * line_h:
        bottom = (runs[-2][1] + runs[-1][0]) // 2
    if (top, bottom) != (0, h):
        Image.open(path).crop((0, top, w, bottom)).save(path)
        _trim(path)


def quote_crop(
    report_dir: Path,
    page_no: int,
    start: str,
    end: str,
    name: str,
    dpi: int,
    full_width: bool = False,
    dest: Path | None = None,
) -> None:
    """Crop the passage from the line holding `start` to the line holding `end`.

    Both phrases must be on the same page and in the same column. Whole printed
    lines are kept so the crop never cuts a sentence mid-glyph.
    """
    doc = fitz.open(report_dir / "paper.pdf")
    if not 1 <= page_no <= len(doc):
        raise SystemExit(f"--page must be between 1 and {len(doc)}")
    page = doc[page_no - 1]
    s = _find(page, start, "--start")[0]
    x0, x1 = (page.rect.x0 + MARGIN_PT / 2, page.rect.x1 - MARGIN_PT / 2) if full_width else _column_range(s, page.rect)
    ends = [r for r in _find(page, end, "--end") if r.y1 >= s.y0 and _overlaps_x(r, x0, x1)]
    if not ends:
        raise SystemExit(
            "--end is not below --start in the same column. Split the quote into "
            "one crop per column (or use --full-width for single-column text)."
        )
    e = ends[0]
    region = fitz.Rect(s)
    others: list[fitz.Rect] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            r = fitz.Rect(ln["bbox"])
            if not _overlaps_x(r, x0, x1):
                continue
            if r.y1 > s.y0 + 1 and r.y0 < e.y1 - 1:
                region |= r
            else:
                others.append(r)
    # Pad vertically only into the gap before the neighbouring lines, so no
    # slivers of the previous/next sentence leak into the quote.
    above = max((r.y1 for r in others if r.y0 < region.y0), default=region.y0 - PAD_PT)
    below = min((r.y0 for r in others if r.y1 > region.y1), default=region.y1 + PAD_PT)
    top = max(region.y0 - PAD_PT, min(above, region.y0))
    bottom = min(region.y1 + PAD_PT, max(below, region.y1))
    region = fitz.Rect(max(region.x0, x0) - PAD_PT, top, min(region.x1, x1) + PAD_PT, bottom) & page.rect
    fig_dir = (dest or report_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{name}.png"
    page.get_pixmap(clip=region, dpi=dpi).save(fig_dir / fname)
    doc.close()
    _trim(fig_dir / fname)
    _drop_edge_slivers(fig_dir / fname)
    manifest_path = fig_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {"items": []}
    manifest["items"].append(
        {
            "label": name,
            "kind": "quote",
            "page": page_no,
            "caption": "",
            "files": [fname],
            "method": "quote",
            "start": start,
            "end": end,
        }
    )
    write_json(manifest_path, manifest)
    print(fig_dir / fname)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", help="report folder created by fetch_paper.py")
    parser.add_argument("cmd", nargs="?", default="auto", choices=["auto", "crop", "quote"])
    parser.add_argument("--start", help="quote: phrase on the first line of the passage")
    parser.add_argument("--end", help="quote: phrase on the last line of the passage")
    parser.add_argument(
        "--full-width",
        action="store_true",
        help="quote: passage spans the full page width (abstract, single-column papers)",
    )
    parser.add_argument("--page", type=int, help="crop: 1-based page number")
    parser.add_argument("--bbox", help="crop: x0,y0,x1,y1 fractions of the page")
    parser.add_argument("--name", help="crop: output name without extension")
    parser.add_argument("--dest", help="crop: report folder whose figures/ receives the crop (default: DIR)")
    parser.add_argument(
        "--rotate",
        type=int,
        default=0,
        choices=[0, 90, 180, 270],
        help="crop: rotate clockwise by this many degrees (sideways figures)",
    )
    parser.add_argument("--dpi", type=int, default=FIG_DPI)
    parser.add_argument("--pdf-only", action="store_true", help="skip arXiv HTML figures")
    parser.add_argument("--with-caption", action="store_true", help="include caption in PDF crops")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES_RENDERED)
    args = parser.parse_args()

    report_dir = Path(args.dir).expanduser().resolve()
    if args.cmd == "crop":
        if not (args.page and args.bbox and args.name):
            raise SystemExit("crop needs --page, --bbox, --name")
        dest = Path(args.dest).expanduser().resolve() if args.dest else None
        manual_crop(report_dir, args.page, args.bbox, args.name, args.dpi, args.rotate, dest)
        return
    if args.cmd == "quote":
        if not (args.page and args.start and args.end and args.name):
            raise SystemExit("quote needs --page, --start, --end, --name")
        dest = Path(args.dest).expanduser().resolve() if args.dest else None
        quote_crop(report_dir, args.page, args.start, args.end, args.name, args.dpi, args.full_width, dest)
        return

    meta = read_json(report_dir / "meta.json")
    fig_dir = report_dir / "figures"
    page_dir = report_dir / "pages"
    fig_dir.mkdir(exist_ok=True)
    page_dir.mkdir(exist_ok=True)

    items: list[dict] = []
    if meta.get("arxiv_id") and not args.pdf_only:
        items += arxiv_html_figures(meta["arxiv_id"], fig_dir)
    items += pdf_crops(report_dir / "paper.pdf", fig_dir, args.dpi, args.with_caption)
    n_pages = render_pages(report_dir / "paper.pdf", page_dir, PAGE_DPI, args.max_pages)
    write_json(fig_dir / "manifest.json", {"items": items, "pages_rendered": n_pages, "page_dpi": PAGE_DPI})

    print(f"{'label':<14} {'kind':<7} {'page':>4} {'method':<20} files")
    for it in items:
        print(
            f"{it['label']:<14} {it['kind']:<7} {it.get('page', '-')!s:>4} "
            f"{it['method']:<20} {', '.join(it['files']) or '-'}"
        )
    print(f"\npages rendered: {n_pages} -> {page_dir}")
    print("NEXT: open the crops you plan to use with Read and verify them visually.")


if __name__ == "__main__":
    main()
