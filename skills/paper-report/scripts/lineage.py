# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Build the citation lineage and author context for a report folder.

Sources (all free, no LLM): Semantic Scholar Graph API (references with
citation contexts/intents/influence, top citing papers, author stats) and
OpenAlex (institutions, author top works). Either may be rate limited; the
script records what failed so the writer can fall back to references.txt.

Writes <dir>/lineage.json and <dir>/lineage.md.

Env (optional): S2_API_KEY, OPENALEX_API_KEY.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import sys
import urllib.error
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from _common import env, http_json, http_post_json, log, q, read_json, write_json

S2 = "https://api.semanticscholar.org/graph/v1"
OA = "https://api.openalex.org"
REF_FIELDS = "contexts,intents,isInfluential,title,year,venue,citationCount,externalIds,authors,publicationDate"
CIT_FIELDS = "contexts,intents,isInfluential,title,year,venue,citationCount,externalIds,authors"
AUTHOR_FIELDS = "name,affiliations,homepage,paperCount,citationCount,hIndex"
MAX_CITATIONS_SCANNED = 1000
TOP_CITATIONS = 25
MAX_AUTHORS_PROFILED = 10
CONTEXT_CHARS = 320
OA_BATCH = 50


def s2_headers() -> dict[str, str]:
    key = env("S2_API_KEY")
    return {"x-api-key": key} if key else {}


def oa_params() -> str:
    key = env("OPENALEX_API_KEY")
    return f"&api_key={q(key)}" if key else ""


def s2_paper_id(meta: dict) -> str | None:
    if meta.get("arxiv_id"):
        return f"arXiv:{meta['arxiv_id']}"
    if meta.get("doi"):
        return f"DOI:{meta['doi']}"
    if meta.get("title"):
        hit = http_json(f"{S2}/paper/search/match?query={q(meta['title'])}&fields=title", headers=s2_headers())
        data = hit.get("data") or []
        return data[0]["paperId"] if data else None
    return None


def s2_collect(meta: dict, errors: list[str]) -> dict[str, Any]:
    """Fetch paper, references (with contexts), citations, and authors from S2.

    Each step is isolated so a rate limit on one step keeps earlier results.
    """
    out: dict[str, Any] = {}
    h = s2_headers()
    try:
        pid = s2_paper_id(meta)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        errors.append(f"S2 lookup: {error}")
        return out
    if not pid:
        errors.append("S2: paper not found")
        return out

    def step(name: str, fn: Any) -> None:
        try:
            fn()
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
            errors.append(f"S2 {name}: {error}")
            log(f"  S2 {name} failed: {error}")

    def paper() -> None:
        out["paper"] = http_json(
            f"{S2}/paper/{q(pid)}?fields=title,year,venue,publicationVenue,publicationDate,"
            "citationCount,influentialCitationCount,referenceCount,externalIds,url,"
            "authors.authorId,authors.name",
            headers=h,
        )

    def references() -> None:
        refs = http_json(f"{S2}/paper/{q(pid)}/references?fields={REF_FIELDS}&limit=1000", headers=h)
        out["references"] = refs.get("data") or []

    def citations() -> None:
        cits: list[dict] = []
        offset = 0
        while offset < MAX_CITATIONS_SCANNED:
            page = http_json(f"{S2}/paper/{q(pid)}/citations?fields={CIT_FIELDS}&limit=100&offset={offset}", headers=h)
            cits += page.get("data") or []
            out["citations"], out["citations_scanned"] = cits, len(cits)
            if page.get("next") is None:
                break
            offset = page["next"]

    def authors() -> None:
        ids = [a["authorId"] for a in (out.get("paper") or {}).get("authors", []) if a.get("authorId")]
        if ids:
            res = http_post_json(f"{S2}/author/batch?fields={AUTHOR_FIELDS}", {"ids": _pick_authors(ids)}, h)
            out["authors"] = [a for a in res if a]

    for name, fn in (("paper", paper), ("references", references), ("citations", citations), ("authors", authors)):
        step(name, fn)
    return out


def _pick_authors(ids: list[str]) -> list[str]:
    """First authors plus the last (usually corresponding/PI) author."""
    if len(ids) <= MAX_AUTHORS_PROFILED:
        return ids
    return ids[: MAX_AUTHORS_PROFILED - 1] + [ids[-1]]


def oa_find_work(meta: dict, s2_ids: dict) -> dict | None:
    """Locate the OpenAlex work: DOI, then MAG ID (from S2), then title search."""
    sel = (
        "&select=id,doi,title,publication_year,publication_date,cited_by_count,"
        "authorships,referenced_works,primary_location,ids"
    )
    keys = []
    if meta.get("doi") or s2_ids.get("DOI"):
        keys.append(f"doi:{q(meta.get('doi') or s2_ids['DOI'])}")
    if s2_ids.get("MAG"):
        keys.append(f"mag:{s2_ids['MAG']}")
    for key in keys:
        try:
            return http_json(f"{OA}/works/{key}?{sel[1:]}{oa_params()}", retries=3)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    if meta.get("title"):
        title = re.sub(r"[^\w\s-]", " ", meta["title"])
        res = http_json(f"{OA}/works?filter=title.search:{q(title)}&per_page=5{sel}{oa_params()}", retries=4)
        want = _norm(meta["title"])
        for w in res.get("results") or []:
            if _norm(w.get("title") or "") == want:
                return w
        results = res.get("results") or []
        return results[0] if results else None
    return None


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def oa_collect(meta: dict, errors: list[str], s2_ids: dict | None = None) -> dict[str, Any]:
    """Fetch institutions, references, top citing works, and author top works from OpenAlex."""
    out: dict[str, Any] = {}
    try:
        work = oa_find_work(meta, s2_ids or {})
        if not work:
            errors.append("OpenAlex: work not found")
            return out
        out["work"] = {
            k: work.get(k) for k in ("id", "doi", "title", "publication_year", "publication_date", "cited_by_count")
        }
        out["authorships"] = [
            {
                "name": a["author"].get("display_name"),
                "openalex_id": a["author"].get("id"),
                "position": a.get("author_position"),
                "is_corresponding": a.get("is_corresponding"),
                "raw_affiliation": a.get("raw_affiliation_strings"),
                "institutions": [
                    {"name": i.get("display_name"), "country": i.get("country_code"), "type": i.get("type")}
                    for i in a.get("institutions", [])
                ],
            }
            for a in work.get("authorships", [])
        ]
        refs = work.get("referenced_works") or []
        ref_items = []
        for i in range(0, len(refs), OA_BATCH):
            chunk = "|".join(r.rsplit("/", 1)[-1] for r in refs[i : i + OA_BATCH])
            res = http_json(
                f"{OA}/works?filter=openalex_id:{chunk}&per_page={OA_BATCH}"
                f"&select=id,doi,title,publication_year,cited_by_count,authorships{oa_params()}"
            )
            ref_items += res.get("results") or []
        out["references"] = [_oa_brief(w) for w in ref_items]
        wid = work["id"].rsplit("/", 1)[-1]
        cit = http_json(
            f"{OA}/works?filter=cites:{wid}&sort=cited_by_count:desc&per_page={TOP_CITATIONS}"
            f"&select=id,doi,title,publication_year,cited_by_count,authorships{oa_params()}"
        )
        out["citations_top"] = [_oa_brief(w) for w in cit.get("results") or []]
        authors = out["authorships"]
        picked = (
            authors if len(authors) <= MAX_AUTHORS_PROFILED else authors[: MAX_AUTHORS_PROFILED - 1] + [authors[-1]]
        )
        for a in picked:
            aid = (a.get("openalex_id") or "").rsplit("/", 1)[-1]
            if not aid:
                continue
            top = http_json(
                f"{OA}/works?filter=author.id:{aid}&sort=cited_by_count:desc&per_page=5"
                f"&select=title,publication_year,cited_by_count{oa_params()}"
            )
            a["top_works"] = top.get("results") or []
            prof = http_json(
                f"{OA}/authors/{aid}?select=display_name,works_count,cited_by_count,"
                f"summary_stats,last_known_institutions{oa_params()}"
            )
            a["profile"] = {
                "works_count": prof.get("works_count"),
                "cited_by_count": prof.get("cited_by_count"),
                "h_index": (prof.get("summary_stats") or {}).get("h_index"),
                "last_known_institutions": [i.get("display_name") for i in prof.get("last_known_institutions") or []],
            }
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
        errors.append(f"OpenAlex: {error}")
        log(f"  OpenAlex failed: {error}")
    return out


def _clean(text: str | None) -> str:
    """Collapse whitespace and literal escape sequences in titles."""
    return " ".join((text or "?").replace("\\n", " ").split())


def _oa_brief(w: dict) -> dict:
    auths = w.get("authorships") or []
    first = auths[0]["author"].get("display_name") if auths else None
    insts = sorted({i.get("display_name") for a in auths for i in a.get("institutions", []) if i.get("display_name")})
    return {
        "title": w.get("title"),
        "year": w.get("publication_year"),
        "doi": w.get("doi"),
        "cited_by_count": w.get("cited_by_count"),
        "first_author": first,
        "n_authors": len(auths),
        "institutions": insts[:6],
        "openalex_id": w.get("id"),
    }


def score_reference(r: dict) -> float:
    """Heuristic importance: influence, methodology intent, #contexts, log citations."""
    intents = set(r.get("intents") or [])
    cited = r.get("citedPaper") or {}
    return (
        3.0 * bool(r.get("isInfluential"))
        + 2.0 * ("methodology" in intents)
        + 1.0 * ("result" in intents)
        + 0.7 * len(r.get("contexts") or [])
        + 0.5 * math.log10(1 + (cited.get("citationCount") or 0))
    )


def _paper_line(p: dict) -> str:
    authors = p.get("authors") or []
    first = authors[0]["name"] if authors else "?"
    etal = " 외" if len(authors) > 1 else ""
    ext = p.get("externalIds") or {}
    link = f"arXiv:{ext['ArXiv']}" if ext.get("ArXiv") else f"doi:{ext['DOI']}" if ext.get("DOI") else ""
    return (
        f"**{p.get('year') or '?'}** {p.get('title') or '?'} ({first}{etal}; "
        f"{p.get('venue') or '-'}; 피인용 {p.get('citationCount', '?')}) {link}"
    ).strip()


def render_md(meta: dict, s2: dict, oa: dict, errors: list[str], today: str) -> str:
    lines = [f"# 계보 자료: {meta.get('title')}", f"조회일: {today}", ""]
    if errors:
        lines += [
            "## 조회 실패",
            *[f"- {e}" for e in errors],
            "- 빈 섹션은 `references.txt`와 본문 인용 위치를 직접 읽어 채울 것",
            "",
        ]
    p = s2.get("paper") or {}
    w = oa.get("work") or {}
    lines += [
        "## 이 논문",
        (
            f"- S2 피인용 {p.get('citationCount', '?')} (영향력 있는 인용 "
            f"{p.get('influentialCitationCount', '?')}), OpenAlex 피인용 {w.get('cited_by_count', '?')}"
        ),
        f"- 발표처(S2): {p.get('venue') or '-'} / "
        f"발표일: {p.get('publicationDate') or w.get('publication_date') or '-'}",
        f"- 인용 수는 출처마다 다름. 레포트에는 하나만 골라 '{today[:7]} 기준, 출처'로 씀",
        "",
    ]

    lines += [
        "## 저자와 소속",
        "OpenAlex 소속은 논문 기준, S2 소속은 현재 프로필 기준임. 논문 저자란(p.1)이 최우선 근거임.",
        "",
    ]
    for a in oa.get("authorships") or []:
        inst = "; ".join(f"{i['name']} ({i.get('country') or '-'})" for i in a["institutions"]) or "-"
        tag = " [교신]" if a.get("is_corresponding") else ""
        lines.append(f"- {a['position']}: **{a['name']}**{tag} — {inst}")
        prof = a.get("profile")
        if prof:
            lines.append(
                f"  - 현재 알려진 소속: {', '.join(prof['last_known_institutions']) or '-'}; "
                f"h-index {prof.get('h_index')}; 논문 {prof.get('works_count')}편"
            )
        for t in (a.get("top_works") or [])[:3]:
            lines.append(
                f"  - 대표작: {t.get('publication_year')} {_clean(t.get('title'))} (피인용 {t.get('cited_by_count')})"
            )
    for a in s2.get("authors") or []:
        lines.append(
            f"- (S2) {a.get('name')}: h-index {a.get('hIndex')}, 논문 {a.get('paperCount')}편, "
            f"소속 {', '.join(a.get('affiliations') or []) or '-'}, 홈페이지 {a.get('homepage') or '-'}"
        )
    lines += [
        "",
        "> 동명이인 주의. 대표작이 분야와 안 맞으면 그 저자 항목은 버리고 웹으로 확인.",
        "> OpenAlex 연도는 재출판본 때문에 틀릴 때가 있음 (예: 2017년 논문이 2025로 표시). "
        "연도는 arXiv/학회 페이지로 확인.",
        "",
    ]

    refs = sorted(s2.get("references") or [], key=score_reference, reverse=True)
    lines += [
        "## 참고문헌 순위 (S2, 인용 문맥 포함)",
        "점수 = 영향력 3 + 방법론 의도 2 + 결과 비교 1 + 문맥 수 x0.7 + log10(피인용) x0.5. 서사 순서는 별개임.",
        (
            "주의: 문맥 문장 속 [번호]는 S2가 파싱한 버전 기준이라 지금 PDF의 참고문헌 번호와 다를 수 있음. "
            "원장에는 text.txt에서 같은 문장을 찾아 페이지 번호로 적을 것."
        ),
        "",
    ]
    for i, r in enumerate(refs[:30], start=1):
        cited = r.get("citedPaper") or {}
        flags = []
        if r.get("isInfluential"):
            flags.append("영향력")
        flags += r.get("intents") or []
        lines.append(f"{i}. [{score_reference(r):.1f}] {_paper_line(cited)} `{', '.join(flags) or '-'}`")
        for c in (r.get("contexts") or [])[:3]:
            lines.append(f'   - "{c[:CONTEXT_CHARS]}"')
    if not refs and oa.get("references"):
        lines += ["(S2 실패. OpenAlex 참고문헌을 피인용 순으로 표시. 인용 문맥 없음)", ""]
        for r in sorted(oa["references"], key=lambda x: x.get("cited_by_count") or 0, reverse=True)[:30]:
            lines.append(
                f"- **{r['year']}** {_clean(r['title'])} ({r['first_author']} 외; 피인용 {r['cited_by_count']}; "
                f"{', '.join(r['institutions'][:3])}) {r.get('doi') or ''}"
            )
    lines.append("")

    lines += ["## 후속작 (이 논문을 인용한 논문)"]
    if oa.get("citations_top"):
        lines += [
            "### 피인용 순 상위 (OpenAlex)",
            (
                "경고: OpenAlex에는 메타데이터가 잘못 병합된 레코드가 섞여 있음 (제목/저자/연도가 엉뚱한 논문). "
                "쓰기 전에 제목을 arXiv나 학회 페이지에서 반드시 확인할 것."
            ),
            "",
        ]
        for r in oa["citations_top"]:
            lines.append(
                f"- **{r['year']}** {_clean(r['title'])} ({r['first_author']} 외; 피인용 {r['cited_by_count']}; "
                f"{', '.join(r['institutions'][:3])}) {r.get('doi') or ''}"
            )
        lines.append("")
    cits = s2.get("citations") or []
    total = (s2.get("paper") or {}).get("citationCount") or 0
    if cits:
        coverage = len(cits) / total if total else 1.0
        heavy = [c for c in cits if c.get("isInfluential") or "methodology" in (c.get("intents") or [])]
        heavy.sort(key=lambda c: (c.get("citingPaper") or {}).get("citationCount") or 0, reverse=True)
        lines += [f"### 방법론으로 인용한 논문 (S2, {len(cits)}/{total}편 표본)"]
        if coverage < 0.9:
            lines.append(
                "표본이 전체의 일부(S2는 최근 인용부터 줌)라 대표성 없음. "
                "대표 후속작은 위 OpenAlex 목록이나 웹으로 확인."
            )
        for c in heavy[:TOP_CITATIONS]:
            flags = (["영향력"] if c.get("isInfluential") else []) + (c.get("intents") or [])
            lines.append(f"- {_paper_line(c.get('citingPaper') or {})} `{', '.join(flags) or '-'}`")
            for ctx in (c.get("contexts") or [])[:1]:
                lines.append(f'  - "{ctx[:CONTEXT_CHARS]}"')
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", help="report folder created by fetch_paper.py")
    parser.add_argument("--skip-s2", action="store_true", help="reuse S2 data from the last run")
    parser.add_argument("--skip-openalex", action="store_true", help="reuse OpenAlex data from the last run")
    args = parser.parse_args()
    report_dir = Path(args.dir).expanduser().resolve()
    meta = read_json(report_dir / "meta.json")
    today = dt.datetime.now().astimezone().date().isoformat()
    errors: list[str] = []

    # Skipped sources keep results from a previous run, so a rate-limited
    # source can be retried alone with --skip-s2 / --skip-openalex.
    prev_path = report_dir / "lineage.json"
    prev = read_json(prev_path) if prev_path.exists() else {}
    if args.skip_s2:
        s2 = prev.get("semantic_scholar") or {}
    else:
        log("Semantic Scholar ...")
        s2 = s2_collect(meta, errors)
    if args.skip_openalex:
        oa = prev.get("openalex") or {}
    else:
        log("OpenAlex ...")
        oa = oa_collect(meta, errors, (s2.get("paper") or {}).get("externalIds") or {})

    write_json(
        report_dir / "lineage.json", {"retrieved": today, "errors": errors, "semantic_scholar": s2, "openalex": oa}
    )
    (report_dir / "lineage.md").write_text(render_md(meta, s2, oa, errors, today), encoding="utf-8")
    print(report_dir / "lineage.md")
    log(
        f"done. S2 refs={len(s2.get('references') or [])} cits={len(s2.get('citations') or [])} "
        f"OA refs={len(oa.get('references') or [])} errors={len(errors)}"
    )


if __name__ == "__main__":
    main()
