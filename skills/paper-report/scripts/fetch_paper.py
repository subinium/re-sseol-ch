# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24"]
# ///
"""Fetch a paper (arXiv ID/URL, DOI, PDF URL, or local PDF) into a report folder.

Outputs in <out>/<slug>/: paper.pdf, meta.json, text.txt (page-marked),
references.txt (reference section, best effort).

Usage:
    uv run fetch_paper.py 1706.03762
    uv run fetch_paper.py https://arxiv.org/abs/2310.06825
    uv run fetch_paper.py 10.1038/s41586-021-03819-2
    uv run fetch_paper.py ~/Downloads/paper.pdf --slug my-paper
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import re
import shutil
import sys
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).parent))
from _common import http_get, http_json, log, q, write_json

ARXIV_NEW = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
ARXIV_OLD = re.compile(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?")
DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)")
ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
REF_HEADING = re.compile(
    r"^\s*(\d+\.?\s*)?(References|REFERENCES|Bibliography|BIBLIOGRAPHY|Literature Cited)\s*$",
    re.MULTILINE,
)
SLUG_MAX_WORDS = 6
TEXT_FLAGS = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES


def classify(raw: str) -> tuple[str, str]:
    """Classify input as ('pdf', path) | ('arxiv', id) | ('doi', doi) | ('url', url)."""
    path = Path(raw).expanduser()
    if path.exists() and path.suffix.lower() == ".pdf":
        return "pdf", str(path.resolve())
    if "arxiv.org" in raw or not raw.startswith("http"):
        m = ARXIV_NEW.search(raw) if "doi.org" not in raw else None
        if m and not raw.startswith("10."):
            return "arxiv", m.group(1)
        m = ARXIV_OLD.search(raw)
        if m and "arxiv" in raw.lower():
            return "arxiv", m.group(1)
    if raw.lower().startswith("10.48550/arxiv."):
        return "arxiv", raw.split("arxiv.", 1)[-1].split("arXiv.", 1)[-1]
    m = DOI_RE.search(raw)
    if m:
        doi = m.group(1).rstrip(".,)")
        if doi.lower().startswith("10.48550/arxiv."):
            return "arxiv", doi.split(".", 2)[-1]
        return "doi", doi
    if raw.startswith("http"):
        return "url", raw
    return "title", raw


def arxiv_meta(arxiv_id: str) -> dict:
    """Fetch arXiv metadata, falling back to the abs page when the API throttles."""
    try:
        return arxiv_api_meta(arxiv_id)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        log(f"  arXiv API failed ({error}); falling back to abs page meta tags")
        return arxiv_abs_meta(arxiv_id)


def arxiv_abs_meta(arxiv_id: str) -> dict:
    """Parse Highwire citation_* meta tags from https://arxiv.org/abs/<id>."""
    page = http_get(f"https://arxiv.org/abs/{arxiv_id}", retries=2).decode("utf-8", "ignore")

    def metas(name: str) -> list[str]:
        pat = rf'<meta\s+name="{name}"\s+content="([^"]*)"'
        return [html.unescape(v).strip() for v in re.findall(pat, page)]

    comment = re.search(r'<td class="tablecell comments[^"]*">(.*?)</td>', page, re.DOTALL)
    authors = []
    for name in metas("citation_author"):
        last, _, first = name.partition(",")
        authors.append({"name": f"{first.strip()} {last.strip()}".strip(), "affiliations": []})
    date = (metas("citation_date") or [None])[0]
    return {
        "source_type": "arxiv",
        "arxiv_id": arxiv_id,
        "arxiv_version": None,
        "doi": (metas("citation_doi") or [None])[0],
        "title": (metas("citation_title") or [None])[0],
        "authors": authors,
        "abstract": (metas("citation_abstract") or [None])[0],
        "published": date.replace("/", "-") if date else None,
        "updated": None,
        "comment": re.sub(r"<[^>]+>", "", comment.group(1)).strip() if comment else None,
        "journal_ref": None,
        "primary_category": None,
        "categories": [],
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "html_url": f"https://arxiv.org/html/{arxiv_id}",
        "meta_source": "abs_page_fallback",
    }


def arxiv_api_meta(arxiv_id: str) -> dict:
    """Fetch arXiv Atom metadata for an ID from the export API."""
    xml = http_get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}", retries=3)
    root = ET.fromstring(xml)
    entry = root.find("a:entry", ATOM)
    if entry is None or entry.find("a:title", ATOM) is None:
        raise SystemExit(f"arXiv ID not found: {arxiv_id}")

    def text(tag: str) -> str | None:
        el = entry.find(tag, ATOM)
        return " ".join(el.text.split()) if el is not None and el.text else None

    authors = []
    for a in entry.findall("a:author", ATOM):
        authors.append(
            {
                "name": a.findtext("a:name", default="", namespaces=ATOM).strip(),
                "affiliations": [x.text.strip() for x in a.findall("x:affiliation", ATOM) if x.text],
            }
        )
    entry_id = text("a:id") or ""
    version = ARXIV_NEW.search(entry_id)
    primary = entry.find("x:primary_category", ATOM)
    return {
        "source_type": "arxiv",
        "arxiv_id": arxiv_id,
        "arxiv_version": version.group(2) if version and version.group(2) else None,
        "doi": text("x:doi"),
        "title": text("a:title"),
        "authors": authors,
        "abstract": text("a:summary"),
        "published": text("a:published"),
        "updated": text("a:updated"),
        "comment": text("x:comment"),
        "journal_ref": text("x:journal_ref"),
        "primary_category": primary.get("term") if primary is not None else None,
        "categories": [c.get("term") for c in entry.findall("a:category", ATOM)],
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "html_url": f"https://arxiv.org/html/{arxiv_id}",
    }


def crossref_meta(doi: str) -> dict:
    """Fetch Crossref metadata for a DOI."""
    msg = http_json(f"https://api.crossref.org/works/{q(doi)}")["message"]
    authors = [
        {
            "name": " ".join(filter(None, [a.get("given"), a.get("family")])),
            "affiliations": [x.get("name") for x in a.get("affiliation", []) if x.get("name")],
        }
        for a in msg.get("author", [])
    ]
    date_parts = (msg.get("published") or msg.get("issued") or {}).get("date-parts", [[None]])
    abstract = re.sub(r"<[^>]+>", "", msg.get("abstract", "") or "").strip() or None
    return {
        "source_type": "doi",
        "arxiv_id": None,
        "doi": doi,
        "title": (msg.get("title") or [None])[0],
        "authors": authors,
        "abstract": abstract,
        "published": "-".join(str(p) for p in date_parts[0] if p),
        "venue": (msg.get("container-title") or [None])[0],
        "publisher": msg.get("publisher"),
        "reference_count": msg.get("reference-count"),
        "abs_url": f"https://doi.org/{doi}",
        "pdf_url": None,
    }


def openalex_pdf_url(doi: str) -> str | None:
    """Find an open-access PDF URL for a DOI via OpenAlex."""
    try:
        work = http_json(f"https://api.openalex.org/works/doi:{q(doi)}", retries=2)
    except Exception as error:  # noqa: BLE001 - best effort lookup
        log(f"  OpenAlex OA lookup failed: {error}")
        return None
    for loc in [work.get("best_oa_location")] + (work.get("locations") or []):
        if loc and loc.get("pdf_url"):
            return loc["pdf_url"]
    arxiv = (work.get("ids") or {}).get("arxiv")
    return arxiv.replace("/abs/", "/pdf/") if arxiv else None


def download(url: str, dest: Path) -> None:
    """Download a URL to a file and check it is a PDF."""
    data = http_get(url, timeout=120)
    if not data.startswith(b"%PDF"):
        raise SystemExit(f"Not a PDF at {url} (paywall or HTML page). Ask the user for the PDF file.")
    dest.write_bytes(data)


def slugify(text: str) -> str:
    """Make a filesystem-safe slug; non-ASCII titles fall back to a short hash."""
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:SLUG_MAX_WORDS]
    if words:
        return "-".join(words)
    return "paper-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def extract_text(pdf: Path, out_dir: Path) -> dict:
    """Write page-marked text and a best-effort references section."""
    doc = fitz.open(pdf)
    parts = []
    for i, page in enumerate(doc, start=1):
        parts.append(f"\n=== [p.{i}] ===\n")
        # Expand ligatures (ﬁ -> fi) so quotes and numbers match plain-text searches.
        parts.append(page.get_text("text", flags=TEXT_FLAGS))
    full = "".join(parts)
    (out_dir / "text.txt").write_text(full, encoding="utf-8")
    matches = list(REF_HEADING.finditer(full))
    ref_found = bool(matches)
    if matches:
        (out_dir / "references.txt").write_text(full[matches[-1].start() :], encoding="utf-8")
    first_page = doc[0].get_text("text") if len(doc) else ""
    info = {"page_count": len(doc), "references_section_found": ref_found, "first_page_excerpt": first_page[:1500]}
    doc.close()
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="arXiv ID/URL, DOI, PDF URL, or local PDF path")
    parser.add_argument("--out", default="reports", help="parent output dir")
    parser.add_argument("--slug", help="folder name override")
    parser.add_argument("--pdf", help="local PDF to use with a DOI/arXiv source")
    args = parser.parse_args()

    kind, value = classify(args.source.strip())
    log(f"input: {kind} -> {value}")
    if kind == "title":
        raise SystemExit("Title-only input: search for the paper (WebSearch) and rerun with its arXiv ID, DOI, or PDF.")

    meta: dict
    if kind == "arxiv":
        meta = arxiv_meta(value)
    elif kind == "doi":
        meta = crossref_meta(value)
        meta["pdf_url"] = openalex_pdf_url(value)
    elif kind == "url":
        meta = {"source_type": "url", "title": None, "authors": [], "pdf_url": value, "abs_url": value}
    else:
        meta = {"source_type": "pdf", "title": None, "authors": [], "pdf_url": None, "local_pdf": value}

    slug = args.slug or (
        f"arxiv-{meta['arxiv_id'].replace('/', '-')}"
        if meta.get("arxiv_id")
        else slugify(meta.get("title") or Path(value).stem)
    )
    out_dir = Path(args.out).expanduser().resolve() / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / "paper.pdf"

    local = args.pdf or meta.get("local_pdf")
    if local and not Path(local).expanduser().is_file():
        raise SystemExit(f"PDF not found: {local}")
    if local:
        shutil.copyfile(Path(local).expanduser(), pdf)
    elif meta.get("pdf_url"):
        log(f"downloading {meta['pdf_url']}")
        download(meta["pdf_url"], pdf)
    else:
        write_json(out_dir / "meta.json", meta)
        raise SystemExit(
            f"No open PDF found. Ask the user for the PDF, then rerun with --pdf PATH. "
            f"Metadata saved to {out_dir / 'meta.json'}"
        )

    text_info = extract_text(pdf, out_dir)
    if not meta.get("title"):
        doc = fitz.open(pdf)
        meta["title"] = (doc.metadata or {}).get("title") or None
        doc.close()
    meta.update(text_info)
    meta["fetched_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    write_json(out_dir / "meta.json", meta)
    print(out_dir)
    log(f"done: {text_info['page_count']} pages, title={meta.get('title')!r}")


if __name__ == "__main__":
    main()
