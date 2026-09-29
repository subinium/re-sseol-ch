# /// script
# requires-python = ">=3.10"
# dependencies = ["markdown>=3.5", "pillow>=10"]
# ///
"""Split report.md into cards and build viewer.html, post.html, and PNGs.

Cards are separated by a line containing only `---`. Ledger tags
(`<!-- F3 -->`) and other HTML comments are stripped from every output.

Outputs:
    cards/NN.md      plain card text, ready to paste into a community post
    viewer.html      single-file post page (images inlined)
    gallery.html     (--gallery) swipe through part screenshots one at a time
    social/NN_k.png  (--social) 4:5 slides (1080x1350) cut at blank rows, for X/Threads
    post.html        all cards stacked like one long community post
    cards/NN.png     (--png) one image per card via headless Chrome
    post_NN.png      (--long, needs --png) cards stitched into tall images

Usage:
    uv run build_cards.py <report_dir> [--png] [--long] [--width 430]
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import markdown
from PIL import Image, ImageChops

SKILL_DIR = Path(__file__).resolve().parent.parent
CSS = (SKILL_DIR / "assets" / "card.css").read_text(encoding="utf-8")
VIEWER_TEMPLATE = (SKILL_DIR / "assets" / "viewer.html").read_text(encoding="utf-8")
GALLERY_TEMPLATE = (SKILL_DIR / "assets" / "gallery.html").read_text(encoding="utf-8")
GALLERY_WEBP_QUALITY = 85
CARD_CSS_WIDTH = 430  # phone viewport, so part PNGs read like real screenshots
SOCIAL_SIZE = (1080, 1350)  # 4:5 portrait, shown uncropped in most social feeds
SOCIAL_CSS_WIDTH = 560  # narrower render so text stays large on a phone feed
SOCIAL_EXTRA_CSS = ".part { font-size: 16px; }"
# Mask render: figures painted solid so blank-row detection never cuts through them.
SOCIAL_MASK_CSS = SOCIAL_EXTRA_CSS + " .part img { filter: brightness(0) !important; }"
SOCIAL_PARAGRAPH_GAP = 40  # px (2x render); prefer cutting at gaps at least this tall
SOCIAL_MIN_FILL = 0.55  # never cut a slide shorter than this share of its height
SOCIAL_SHORT_PART_LINES = 6  # parts this short share a slide with the next part
SOCIAL_TOP_PAD = 24  # px added above continuation slides (at output scale), close to the first slide's top
BLANK_TOLERANCE = 6
FONT_CSS = (
    "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/"
    "variable/pretendardvariable-dynamic-subset.min.css"
)
# Artifact pages may only load stylesheets from Google Fonts.
ARTIFACT_FONT_CSS = "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap"
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMG_TAG = re.compile(r'<p>\s*<img alt="([^"]*)" src="([^"]+)"\s*/?>\s*</p>')
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
SCREENSHOT_HEIGHT_CSS = 4200
DEVICE_SCALE = 3  # 430 CSS px x 3 = 1290 px, the width of a phone screenshot
PAGE_PAD_CSS = 16
PAGE_TOP_CSS = 12  # phone screenshots start right under the status bar: keep the top tight
LONG_MAX_HEIGHT_PX = 16000
LONG_GAP_PX = 24


def split_cards(raw: str) -> list[str]:
    parts, cur, in_code = [], [], False
    for line in raw.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and line.strip() == "---":
            parts.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    parts.append("\n".join(cur))
    return [p.strip("\n") for p in parts if p.strip()]


def clean_md(text: str) -> str:
    """Remove ledger tags/comments and trailing spaces they leave behind."""
    text = COMMENT.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def render_card_html(md_text: str, report_dir: Path, inline: bool) -> str:
    body = markdown.markdown(md_text, extensions=["nl2br", "tables", "sane_lists"])

    def fig(m: re.Match[str]) -> str:
        alt, src = html.unescape(m.group(1)), m.group(2)
        path = report_dir / src
        if inline and path.exists():
            src = data_uri(path)
        # Generated summary graphics (g_*.png) carry their own title: no caption, no height cap.
        name = Path(m.group(2)).name
        is_graphic = name.startswith("g_")
        # Quote crops (q_*.png) are paper text: shown at reading size like a blockquote.
        cls = ' class="graphic"' if is_graphic else ' class="quote"' if name.startswith("q_") else ""
        cap = f"<figcaption>{html.escape(alt)}</figcaption>" if alt and not is_graphic else ""
        return f'<figure><img{cls} alt="{html.escape(alt)}" src="{src}">{cap}</figure>'

    body = IMG_TAG.sub(fig, body)
    body = re.sub(r"<a href=", '<a target="_blank" rel="noopener" href=', body)
    return body


def card_block(body: str, idx: int) -> str:
    """Wrap one part. No labels, counters, or footers: the post reads like a plain community post."""
    return f'<section class="part" data-index="{idx}">{body}</section>'


def standalone(cards_html: str, title: str, width: int, extra_css: str = "") -> str:
    """A fixed-width white page used for PNG screenshots and post.html."""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<link rel="stylesheet" href="{FONT_CSS}"><style>{CSS}
body {{ padding: {PAGE_TOP_CSS}px {PAGE_PAD_CSS}px {PAGE_PAD_CSS}px; }}
.wrap {{ width: {width - 2 * PAGE_PAD_CSS}px; }}
.wrap .part + .part {{ margin-top: 44px; }}{extra_css}</style></head>
<body><div class="wrap">{cards_html}</div></body></html>"""


def to_artifact(viewer: str, title: str) -> str:
    """Adapt the viewer for claude.ai Artifacts: the host supplies the document
    skeleton, and only Google Fonts stylesheets are allowed."""
    page = viewer.replace(FONT_CSS, ARTIFACT_FONT_CSS)
    # The gallery name should be a short name, not the community headline.
    page = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>", page, count=1)
    for tag in (
        "<!doctype html>\n",
        '<html lang="ko">\n',
        "<head>\n",
        '<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n',
        "</head>\n",
        "<body>\n",
        "</body>\n",
        "</html>\n",
    ):
        page = page.replace(tag, "", 1)
    return page


def part_title(md_text: str) -> str:
    """First heading of a part, or its first non-empty line."""
    m = re.search(r"^#{1,2}\s+(.+)$", md_text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return next((line.strip() for line in md_text.splitlines() if line.strip()), "")


def gallery_page(pngs: list[Path], alts: list[str], title: str) -> str:
    """Build the swipe gallery with screenshots inlined as WebP data URIs."""
    shots = []
    for png, alt in zip(pngs, alts, strict=True):
        buf = io.BytesIO()
        Image.open(png).convert("RGB").save(buf, "WEBP", quality=GALLERY_WEBP_QUALITY, method=6)
        shots.append({"src": "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode(), "alt": alt})
    # Escape "</" so a part title can never close the inline <script>.
    return GALLERY_TEMPLATE.replace("{{TITLE}}", html.escape(title)).replace(
        "{{SHOTS}}", json.dumps(shots, ensure_ascii=False).replace("</", "<\\/")
    )


def _blank_rows(img: Image.Image) -> list[bool]:
    """True for rows that are (almost) pure background."""
    gray = img.convert("L")
    w, h = gray.size
    data = gray.load()
    step = max(1, w // 240)  # sample columns; fast enough for tall images
    bg = data[0, 0]
    return [all(abs(data[x, y] - bg) <= BLANK_TOLERANCE for x in range(0, w, step)) for y in range(h)]


def _cut_row(blank: list[bool], lo: int, hi: int) -> int | None:
    """Pick a cut inside [lo, hi]: middle of the last paragraph-sized gap, else of any gap."""
    runs, start = [], None
    for r in range(lo, hi + 1):
        if blank[r] and start is None:
            start = r
        elif not blank[r] and start is not None:
            runs.append((start, r - 1))
            start = None
    if start is not None:
        runs.append((start, hi))
    if not runs:
        return None
    wide = [run for run in runs if run[1] - run[0] + 1 >= SOCIAL_PARAGRAPH_GAP]
    a, b = (wide or runs)[-1]
    return (a + b) // 2


def social_groups(cards_md: list[str]) -> list[list[int]]:
    """Group part indices for social slides: a short part (e.g. the title block)
    shares a slide with the next part instead of leaving a mostly blank slide."""
    groups: list[list[int]] = []
    i = 0
    while i < len(cards_md):
        group = [i]
        while sum(1 for ln in cards_md[group[-1]].splitlines() if ln.strip()) <= SOCIAL_SHORT_PART_LINES and group[
            -1
        ] + 1 < len(cards_md):
            group.append(group[-1] + 1)
        groups.append(group)
        i = group[-1] + 1
    return groups


def social_slices(png: Path, mask: Path, out_dir: Path, stem: str) -> list[Path]:
    """Cut one part screenshot into 4:5 slides at blank rows of its mask render, padded to full size."""
    img = Image.open(png).convert("RGB")
    out_w, out_h = SOCIAL_SIZE
    scale = out_w / img.width
    slice_h = int(out_h / scale)  # slice height in source pixels
    blank = _blank_rows(Image.open(mask))
    if len(blank) < img.height:  # renders should match; pad defensively
        blank += [True] * (img.height - len(blank))
    cuts, y = [], 0
    while img.height - y > slice_h:
        cut = _cut_row(blank, y + int(slice_h * SOCIAL_MIN_FILL), y + slice_h)
        if cut is None:  # one block taller than a slide: cut after it and shrink later
            cut = next((r for r in range(y + slice_h, img.height) if blank[r]), img.height)
        cuts.append((y, cut))
        y = cut
        while y < img.height and blank[y]:
            y += 1
    if y < img.height:
        cuts.append((y, img.height))
    paths = []
    for k, (top, bottom) in enumerate(cuts, start=1):
        seg = img.crop((0, top, img.width, bottom))
        seg = seg.resize((out_w, max(1, int(seg.height * scale))), Image.LANCZOS)
        pad = SOCIAL_TOP_PAD if k > 1 else 0
        if seg.height + pad > out_h:  # oversized block: fit inside the slide
            ratio = (out_h - pad) / seg.height
            seg = seg.resize((max(1, int(seg.width * ratio)), out_h - pad), Image.LANCZOS)
        canvas = Image.new("RGB", SOCIAL_SIZE, "white")
        canvas.paste(seg, ((out_w - seg.width) // 2, pad))
        path = out_dir / f"{stem}_{k}.png"
        canvas.save(path, optimize=True)
        paths.append(path)
    return paths


def find_chrome() -> str | None:
    custom = os.environ.get("CHROME_PATH", "").strip()
    for cand in ([custom] if custom else []) + CHROME_CANDIDATES:
        if cand and (Path(cand).exists() or shutil.which(cand)):
            return cand
    return None


def screenshot(chrome: str, html_path: Path, png_path: Path, width: int) -> bool:
    """Render an HTML file to PNG with headless Chrome and trim the page background."""
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        f"--force-device-scale-factor={DEVICE_SCALE}",
        f"--window-size={width},{SCREENSHOT_HEIGHT_CSS}",
        "--virtual-time-budget=4000",
        f"--screenshot={png_path}",
        html_path.as_uri(),
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:  # Chrome refuses to run as root (e.g. Docker) without this
        cmd.insert(1, "--no-sandbox")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
    if result.returncode != 0 or not png_path.exists():
        print(f"  chrome failed for {html_path.name}: {result.stderr[-300:]}", file=sys.stderr)
        return False
    img = Image.open(png_path).convert("RGB")
    bg = Image.new("RGB", img.size, img.getpixel((1, img.height - 2)))
    box = ImageChops.difference(img, bg).getbbox()
    if box:
        pad = PAGE_PAD_CSS * DEVICE_SCALE
        bottom = min(box[3] + pad, img.height)
        if box[3] >= img.height - 2:
            print(
                f"  WARN {png_path.name}: card taller than {SCREENSHOT_HEIGHT_CSS}px, image cut off. Split the card.",
                file=sys.stderr,
            )
        img.crop((0, 0, img.width, bottom)).save(png_path, optimize=True)
    return True


def stitch(pngs: list[Path], out_dir: Path) -> list[Path]:
    """Stack card PNGs into tall images no higher than LONG_MAX_HEIGHT_PX each."""
    images = [Image.open(p).convert("RGB") for p in pngs]
    if not images:
        return []
    bg = images[0].getpixel((1, 1))
    groups: list[list[Image.Image]] = [[]]
    height = 0
    for im in images:
        if groups[-1] and height + im.height > LONG_MAX_HEIGHT_PX:
            groups.append([])
            height = 0
        groups[-1].append(im)
        height += im.height + LONG_GAP_PX
    outputs = []
    for gi, group in enumerate(groups, start=1):
        w = max(i.width for i in group)
        h = sum(i.height for i in group) + LONG_GAP_PX * (len(group) - 1)
        canvas = Image.new("RGB", (w, h), bg)
        y = 0
        for im in group:
            canvas.paste(im, (0, y))
            y += im.height + LONG_GAP_PX
        path = out_dir / f"post_{gi:02d}.png"
        canvas.save(path, optimize=True)
        outputs.append(path)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", help="report folder")
    parser.add_argument("--report", default="report.md")
    parser.add_argument("--png", action="store_true", help="export cards/NN.png via headless Chrome")
    parser.add_argument("--long", action="store_true", help="also stitch post_NN.png (needs --png)")
    parser.add_argument(
        "--social", action="store_true", help="write social/NN_k.png 4:5 slides for X/Threads (implies --png)"
    )
    parser.add_argument(
        "--gallery", action="store_true", help="write gallery.html to swipe through part screenshots (implies --png)"
    )
    parser.add_argument("--width", type=int, default=CARD_CSS_WIDTH, help="card image width in CSS px")
    parser.add_argument(
        "--artifact", action="store_true", help="also write viewer_artifact.html for publishing as a claude.ai Artifact"
    )
    parser.add_argument("--artifact-title", help="short page name for the artifact (default: '<paper title> 해설')")
    args = parser.parse_args()

    report_dir = Path(args.dir).expanduser().resolve()
    raw = (report_dir / args.report).read_text(encoding="utf-8")
    meta_path = report_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    cards_md = [clean_md(c) for c in split_cards(raw)]
    total = len(cards_md)
    title_m = re.search(r"^#\s+(.+)$", cards_md[0], re.MULTILINE) if cards_md else None
    report_title = title_m.group(1).strip() if title_m else (meta.get("title") or "paper report")
    paper_title = meta.get("title") or ""

    card_dir = report_dir / "cards"
    if card_dir.exists():
        for old in card_dir.glob("[0-9][0-9].*"):
            old.unlink()
    card_dir.mkdir(exist_ok=True)
    blocks_inline, blocks_file = [], []
    for i, md_text in enumerate(cards_md):
        (card_dir / f"{i + 1:02d}.md").write_text(md_text, encoding="utf-8")
        blocks_inline.append(card_block(render_card_html(md_text, report_dir, True), i))
        blocks_file.append(card_block(render_card_html(md_text, report_dir, False), i))

    viewer = (
        VIEWER_TEMPLATE.replace("{{TITLE}}", html.escape(report_title))
        .replace("{{FONT_CSS}}", FONT_CSS)
        .replace("{{CSS}}", CSS)
        .replace("{{CARDS}}", "\n".join(blocks_inline))
    )
    (report_dir / "viewer.html").write_text(viewer, encoding="utf-8")
    if args.artifact:
        name = args.artifact_title or (f"{paper_title} 해설" if paper_title else report_title)
        (report_dir / "viewer_artifact.html").write_text(to_artifact(viewer, name), encoding="utf-8")
        print(f"artifact: {report_dir / 'viewer_artifact.html'}")
    (report_dir / "post.html").write_text(
        standalone("\n".join(blocks_inline), report_title, args.width), encoding="utf-8"
    )
    print(f"cards: {total} -> {card_dir}")
    print(f"viewer: {report_dir / 'viewer.html'}")
    print(f"post:   {report_dir / 'post.html'}")

    if args.gallery or args.social:
        args.png = True
    if not args.png:
        return
    chrome = find_chrome()
    if not chrome:
        raise SystemExit("Chrome/Chromium not found. Set CHROME_PATH or skip --png.")
    build = report_dir / ".build"
    build.mkdir(exist_ok=True)
    pngs: list[Path] = []
    shot_idx: list[int] = []
    for i, block in enumerate(blocks_file):
        page = build / f"card_{i + 1:02d}.html"
        # Relative image paths resolve against the report dir via <base>.
        doc = standalone(block, report_title, args.width).replace(
            "<head>", f'<head><base href="{report_dir.as_uri()}/">', 1
        )
        page.write_text(doc, encoding="utf-8")
        png = card_dir / f"{i + 1:02d}.png"
        if screenshot(chrome, page, png, args.width):
            pngs.append(png)
            shot_idx.append(i)
    print(f"png: {len(pngs)}/{total} cards -> {card_dir}")
    if args.long:
        for p in stitch(pngs, report_dir):
            print(f"long: {p}")
    if args.social:
        social_dir = report_dir / "social"
        if social_dir.exists():
            for old in social_dir.glob("*.png"):
                old.unlink()
        social_dir.mkdir(exist_ok=True)
        lines = ["# 공유용 이미지", ""]
        for group in social_groups(cards_md):
            i = group[0]
            block = "\n".join(blocks_file[j] for j in group)
            page = build / f"social_{i + 1:02d}.html"
            doc = standalone(block, report_title, SOCIAL_CSS_WIDTH, SOCIAL_EXTRA_CSS).replace(
                "<head>", f'<head><base href="{report_dir.as_uri()}/">', 1
            )
            page.write_text(doc, encoding="utf-8")
            shot = build / f"social_{i + 1:02d}.png"
            mask_page = build / f"social_{i + 1:02d}_mask.html"
            mask_page.write_text(doc.replace(SOCIAL_EXTRA_CSS, SOCIAL_MASK_CSS, 1), encoding="utf-8")
            mask = build / f"social_{i + 1:02d}_mask.png"
            if not (
                screenshot(chrome, page, shot, SOCIAL_CSS_WIDTH)
                and screenshot(chrome, mask_page, mask, SOCIAL_CSS_WIDTH)
            ):
                continue
            files = social_slices(shot, mask, social_dir, f"{i + 1:02d}")
            lines.append(f"- {', '.join(p.name for p in files)}: {part_title(cards_md[i])}")
        (social_dir / "captions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"social: {sum(1 for _ in social_dir.glob('*.png'))} slides -> {social_dir}")
    if args.gallery:
        alts = [part_title(cards_md[i]) for i in shot_idx]
        page = gallery_page(pngs, alts, report_title)
        (report_dir / "gallery.html").write_text(page, encoding="utf-8")
        print(f"gallery: {report_dir / 'gallery.html'}")
        if args.artifact:
            name = args.artifact_title or (f"{paper_title} 해설" if paper_title else report_title)
            (report_dir / "gallery_artifact.html").write_text(to_artifact(page, name), encoding="utf-8")
            print(f"gallery artifact: {report_dir / 'gallery_artifact.html'}")


if __name__ == "__main__":
    main()
