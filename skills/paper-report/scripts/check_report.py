# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Lint report.md for community tone and trace every fact to the ledger.

Parts are separated by a line containing only `---`.

ERROR (exit 1): profanity, honorific endings, lines over 70 chars, unknown
ledger tags, missing image files, numbers found in neither the paper text,
the ledger, nor lineage.md.
WARN: plain-declarative endings, lines over 40 chars, AI-ish phrases, emoji,
dashes, monotone endings, walls of text, '개-' slang, too many ㅋㅋ or !,
untagged parts, part length/count out of range, too few images, too few
detectable community markers, spine nodes (from outline.md) used in too few parts,
figure-capture or permission chatter, declared fun, missing outline.md,
subheadings inside parts, explanations stuffed into parentheses, "킬포" labels,
weak flow at part boundaries (no connective and no
shared key word between the end of one part and the start of the next).

Usage:
    uv run check_report.py <report_dir> [--report report.md] [--strict]
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MAX_LINE_CHARS = 40
HARD_MAX_LINE_CHARS = 70
MAX_LINES_WITHOUT_BLANK = 12
MONOTONE_RUN = 5
CARD_MIN_LINES, CARD_MAX_LINES = 6, 22
CARD_MIN_COUNT, CARD_MAX_COUNT = 10, 26
MAX_KEK = 2
MIN_IMAGES = 8
MAX_OWN_GRAPHICS = 1  # self-made g_*.png summary images; the rest should quote the paper
OWN_GRAPHIC = re.compile(r"\]\(figures/g_[^)]+\)")
MIN_IMAGE_CARD_RATIO = 0.6
COMMUNITY_MARKERS = {
    "3줄 요약": re.compile(r"3줄\s*요약"),
    "쉽게/정확히 짝": re.compile(r"쉽게 말하면[\s\S]{0,400}정확히는"),
    "내용 추가하면": re.compile(r"내용 추가하면"),
}
MIN_COMMUNITY_MARKERS = 3
# Flow between parts: the next part should pick up the previous one, either with a
# connective at its start or by reusing a key word from the previous part's ending.
FLOW_CONNECTIVES = (
    "근데",
    "그래서",
    "그러다",
    "그럼",
    "그러니까",
    "그리고",
    "이번엔",
    "이제",
    "다시",
    "앞에서",
    "아까",
    "또",
    "한편",
    "반대로",
    "사실",
    "여기서",
    "그 뒤",
    "그렇게",
    "결국",
    "처음",
    "맨 앞",
    "이렇게",
    "여기부터",
    "그 전에",
    "같은 해",
    "그때",
    "그중",
    "이 ",
    "그 ",
    "저 ",
)
# A first line that picks up a spine number or points back with a demonstrative
# ("논문은 이 역설을", "④는 그 결과를") connects without a stock connective.
ANAPHOR = re.compile(r"^[①②③④⑤⑥⑦⑧⑨]|(^|\s)(이|그|이런|그런|앞의|앞에서) \S")
FLOW_WINDOW = 5  # prose lines taken from the end of one part and the start of the next
FLOW_STOPWORDS = {
    "그리고",
    "근데",
    "그래서",
    "이게",
    "그게",
    "이건",
    "거임",
    "있음",
    "없음",
    "했음",
    "됐음",
    "것임",
    "정도",
    "때문",
    "이제",
    "진짜",
    "그냥",
    "하나",
    "보면",
    "보셈",
    "논문",
    "이번",
    "여기",
}
JOSA = re.compile(
    r"(으로|에서|에게|까지|부터|처럼|보다|이랑|하고|이라|이고|라고|은|는|이|가|을|를|의|에|로|와|과|도|만|임|음|함|됨)$"
)
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]+|\d+(?:\.\d+)?[A-Za-z%²]*|[가-힣]{2,}|[①②③④⑤]")
SPINE_MARK = re.compile(r"[①②③④]")
MIN_SPINE_PARTS = 4  # spine nodes should recur across this many parts
SPINE_NODE = re.compile(r"^\s*-\s*마디:\s*([^|]+?)\s*\|", re.MULTILINE)
# Readers care about the figure, not how it was captured. Flag capture/licensing chatter.
FIGURE_META_ALT = re.compile(r"회전|일부만|일부를 (?:잘|확대|보여)|크롭|캡처|스크린샷|잘라|확대")
FIGURE_META_BODY = re.compile(r"캡처|스크린샷|크롭|빨간 글씨|허락|그림[은도] .*가져옴|출처를 달아")
MAX_EXCLAIM = 3
MIN_NUMBER_TO_TRACE = 10  # single digits are too ambiguous to trace
SKIP_CARD_TITLES = ("출처", "참고", "레퍼런스", "references")
SHORT_OK_TITLES = ("요약",)

PROFANITY = re.compile(
    r"ㅈㄴ|ㅅㅂ|ㅆㅂ|ㅂㅅ|ㅁㅊ|ㄱㅅㄲ|ㅄ|존나|졸라|좆|씨발|씨바|시발|씹|병신|븅신|미친놈|미친년|"
    r"개새|새끼|지랄|닥쳐|꺼져|엠창|느금|틀딱|한남충|김치녀|된장녀|급식충|맘충|틀니"
)
SLANG_GAE = re.compile(r"(?<![가-힣])개(좋|쩔|쩐|웃|꿀|이득|많|빡|비싸|어렵|쉽|사기|오반|추|노잼|잼)")
HONORIFIC = re.compile(r"(니다|습니까|십니까|[어아해]요|에요|예요|세요|네요|군요|지요|죠|까요)[.!?~]*$")
DECLARATIVE = re.compile(
    r"((했|였|었|았|겠|됐|왔|갔|졌|났|웠|렸|켰|봤|줬|뒀)다|(이|한|된|는|있|없|같|많|크|작|높|낮|싶)다)[.!]*$"
)
AI_PHRASES = [
    "흥미롭게도",
    "주목할 만한",
    "주목할만한",
    "혁신적",
    "획기적",
    "게임 체인저",
    "게임체인저",
    "라고 할 수 있",
    "결론적으로 말하자면",
    "살펴보겠",
    "알아보겠",
    "기대됩니다",
    "시사하는 바",
    "의미를 가집니다",
    "중요한 의미",
    "본 논문은",
    "본 연구는",
    "다양한 분야에",
    "패러다임",
    "한 획을",
    "새로운 지평",
    "눈여겨볼",
    "깊이 있는",
    "숨은 백미",
    "의 비밀",
    "진짜 이유",
    "의 모든 것",
    "알아보자",
    "파헤쳐",
]
# Declaring fun ("웃김") instead of showing the expectation-vs-outcome gap.
FUN_DECLARATION = re.compile(r"(웃김|웃긴 일|재밌음|재미있음)$")
# Explanations stuffed into parentheses ("퍼플렉서티(모델이 헷갈리는 정도라 낮을수록 좋음)").
# Term names and dates in parentheses are fine: require Hangul, 2+ spaces, no digits.
PAREN_GLOSS = re.compile(r"\(([^()]*[가-힣][^()]*)\)")
EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff]")
DASHES = re.compile("[—–]")
TAG = re.compile(r"<!--\s*((?:[FLPW]\d+[\s,]*)+)\s*-->")
LEDGER_ID = re.compile(r"^\s*[-*]\s*([FLPW]\d+)\s*\|", re.MULTILINE)
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
LINK = re.compile(r"\[[^\]]*\]\([^)]+\)|https?://\S+")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass
class Issue:
    level: str
    card: int
    line: int
    msg: str
    text: str = ""


@dataclass
class Card:
    index: int
    title: str
    start_line: int
    lines: list[tuple[int, str]] = field(default_factory=list)


def split_cards(raw: str) -> list[Card]:
    cards: list[Card] = [Card(0, "", 1)]
    in_code = False
    for no, line in enumerate(raw.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and line.strip() == "---":
            cards.append(Card(len(cards), "", no + 1))
            continue
        card = cards[-1]
        if not card.title and line.lstrip().startswith("#"):
            card.title = line.lstrip("# ").strip()
        card.lines.append((no, line))
    cards = [c for c in cards if any(t.strip() for _, t in c.lines)]
    for c in cards:  # parts have no subheadings: fall back to the first prose line
        if not c.title:
            c.title = next((prose(t).strip("*") for _, t in c.lines if prose(t)), "")
    return cards


def prose(line: str) -> str:
    """Line content that is subject to tone rules (no comments/links/images)."""
    text = COMMENT.sub("", line)
    text = IMAGE.sub("", text)
    text = LINK.sub("", text)
    return text.strip()


def normalize_num(s: str) -> str:
    return s.replace(",", "")


def key_tokens(lines: list[str]) -> set[str]:
    """Content words with trailing particles stripped, for crude lexical overlap."""
    out = set()
    for line in lines:
        for tok in TOKEN.findall(line):
            tok = JOSA.sub("", tok) if re.match(r"[가-힣]", tok) else tok.lower()
            if (len(tok) >= 2 or tok in "①②③④⑤") and tok not in FLOW_STOPWORDS:
                out.add(tok)
    return out


def flow_issues(cards: list[Card]) -> list[Issue]:
    """Warn at part boundaries where the next part neither connects nor reuses a key word."""
    body = [
        c for c in cards[1:] if not any(k in c.title.lower() for k in SKIP_CARD_TITLES) and "내용 추가" not in c.title
    ]
    issues = []
    for prev, nxt in itertools.pairwise(body):
        tail = [prose(t) for _, t in prev.lines if prose(t) and not t.lstrip().startswith("#")][-FLOW_WINDOW:]
        head = [prose(t) for _, t in nxt.lines if prose(t) and not t.lstrip().startswith("#")][:FLOW_WINDOW]
        if not head:
            continue
        connects = head[0].startswith(FLOW_CONNECTIVES) or bool(ANAPHOR.search(head[0]))
        shared = key_tokens(tail) & key_tokens(head)
        if not connects and not shared:
            issues.append(
                Issue(
                    "WARN",
                    nxt.index,
                    nxt.start_line,
                    f"파트 {prev.index}→{nxt.index} 연결 약함: 첫 줄에 연결어도, 앞 파트 끝과 겹치는 핵심어도 없음",
                    head[0],
                )
            )
    return issues


# AI tells that survive the switch to 음슴체 (style-guide.md section 4).
SLOP_PHRASE = re.compile(
    r"(돌아보면|정리하면|요약하면|한마디로|결국 .*셈|볼 맛|진짜 포인트"
    r"|이게 핵심|핵심은|딱 이거|자는 얘기였음|는 얘기였음)"
)
OUTLOOK_PHRASE = re.compile(r"(관전 포인트|기대되는 대목|앞으로가 기대)")
# Korean AI tells adapted from epoko77-ai/im-not-ai (MIT); rule ids in comments.
CONTRAST = re.compile(r"(가|이) 아니라|는 아님$|것은 아님")  # C-8
MAX_CONTRASTS = 3
EXPLAIN_END = re.compile(r"(는|은|ㄴ|한|된|인|는다는|라는|다는) (거임|뜻임)$|는 거임$|는 뜻임$")  # I-3
MAX_EXPLAIN_ENDS = 4
CLEFT = re.compile(r"^(핵심은|관건은|중요한 건|중요한 것은|필요한 건)")  # D-8
REASON_END = re.compile(r"이유임$")  # D-10
RANGE_UP = re.compile(r"단순한 .{1,15}(를|을) 넘어")  # A-21
DEAD_METAPHOR = re.compile(r"(잠식|청사진|적신호|경고등|신호탄|뿌리내리|짓누르)")  # D-14
MAX_OUTLOOK_PHRASES = 1
OPENER = re.compile(r"^(그래서|근데|그럼)\b")
MAX_OPENER_LINE_SHARE = 0.08  # share of prose lines that may start with 그래서/근데/그럼
MAX_OPENER_PART_SHARE = 0.5  # share of parts whose first line starts with one of them
VERDICT_END = re.compile(r"(고 봄|고 함|다고 씀)$")
MAX_VERDICTS_PER_PART = 3
TEMPLATE_RUN = 3  # consecutive lines cut from the same mould
CIRCLED = re.compile(r"^[①②③④⑤⑥⑦⑧⑨]")


def _mould(line: str) -> str:
    """Rough shape of a line's opening, used to spot lines stamped from one template."""
    if CIRCLED.match(line):
        return "circled"
    return line.split()[0] if line.split() else ""


def slop_issues(cards: list[Card]) -> list[Issue]:
    """Warn on 음슴체 text that still reads machine-made: templated runs, wrap-up and
    teaser phrases, stock outlook endings, and repetitive openers or verdict endings."""
    body = [
        c for c in cards[1:] if not any(k in c.title.lower() for k in SKIP_CARD_TITLES) and "내용 추가" not in c.title
    ]
    issues: list[Issue] = []
    outlook: list[tuple[int, int, str]] = []
    openers = total = contrasts = explains = 0
    seen: dict[str, int] = {}
    for card in body:
        verdicts = 0
        block: list[tuple[int, str]] = []
        rows = [(no, prose(t)) for no, t in card.lines if not t.lstrip().startswith(("#", "!", "**", "|", "-"))]
        for no, text in [*rows, (0, "")]:
            if not text:
                circled = [r for r in block if _mould(r[1]) == "circled"]
                if len(circled) >= TEMPLATE_RUN:
                    issues.append(
                        Issue(
                            "WARN", card.index, circled[0][0], "번호로 찍어낸 나열. 하나만 골라 말할 것", circled[0][1]
                        )
                    )
                consecutive = 1
                for (_, a), (bno, b) in itertools.pairwise(block):
                    consecutive = consecutive + 1 if a.endswith("고") and b.endswith("고") else 1
                    if consecutive == TEMPLATE_RUN:
                        issues.append(Issue("WARN", card.index, bno, "'~고'로 이어 붙인 나열. 한 문장으로 묶을 것", b))
                block = []
                continue
            block.append((no, text))
            total += 1
            openers += bool(OPENER.match(text))
            if CLEFT.search(text) or REASON_END.search(text) or RANGE_UP.search(text):
                issues.append(
                    Issue("WARN", card.index, no, "분열문·도치 결산·범위 상승. 주어와 서술을 바로 이을 것", text)
                )
            if DEAD_METAPHOR.search(text):
                issues.append(Issue("WARN", card.index, no, "사전 은유. 명제로 풀 것", text))
            contrasts += bool(CONTRAST.search(text))
            explains += bool(EXPLAIN_END.search(text))
            if SLOP_PHRASE.search(text):
                issues.append(Issue("WARN", card.index, no, "정리·예고 멘트. 지우고 사실이나 장면으로", text))
            if OUTLOOK_PHRASE.search(text):
                outlook.append((card.index, no, text))
            if VERDICT_END.search(text):
                verdicts += 1
            if len(text) >= 10:
                if text in seen:
                    issues.append(Issue("WARN", card.index, no, f"파트 {seen[text]}과 같은 문장 반복", text))
                seen.setdefault(text, card.index)
        if verdicts > MAX_VERDICTS_PER_PART:
            issues.append(
                Issue("WARN", card.index, card.start_line, f"'~라고 봄/함'이 {verdicts}번. 논문 주장임을 한 번만 걸 것")
            )
    if contrasts > MAX_CONTRASTS:
        issues.append(Issue("WARN", 0, 0, f"'A가 아니라 B' 대구 {contrasts}번. 한 번만 살리고 나머지는 평서로"))
    if explains > MAX_EXPLAIN_ENDS:
        issues.append(Issue("WARN", 0, 0, f"'~는 거임' 설명형 종결 {explains}번. {MAX_EXPLAIN_ENDS}번까지만"))
    if len(outlook) > MAX_OUTLOOK_PHRASES:
        idx, no, text = outlook[1]
        issues.append(Issue("WARN", idx, no, f"전망 문구 {len(outlook)}번. 공식처럼 반복하지 말 것", text))
    if total and openers / total > MAX_OPENER_LINE_SHARE:
        issues.append(
            Issue("WARN", 0, 0, f"'그래서/근데/그럼'으로 시작하는 줄 {openers}/{total}. 장면이나 사실로 시작할 것")
        )
    firsts = [next((prose(t) for _, t in c.lines if prose(t)), "") for c in body]
    opened = sum(bool(OPENER.match(f)) for f in firsts)
    if firsts and opened / len(firsts) > MAX_OPENER_PART_SHARE:
        issues.append(
            Issue("WARN", 0, 0, f"파트 첫 줄 {opened}/{len(firsts)}개가 접속어로 시작. 앞 파트의 장면을 받아 시작할 것")
        )
    return issues


# "RNN을 빼고 어텐션만 남긴 게 트랜스포머임": an action equated with a thing.
ACT_EQUALS_THING = re.compile(r"[가-힣] (게|것이) \S+임$")


def summary_issues(cards: list[Card]) -> list[Issue]:
    """Check the 3-line summary: first line answers the title, no action-equals-thing lines."""
    title = next((prose(t).lstrip("# ") for _, t in cards[0].lines if t.lstrip().startswith("# ")), "") if cards else ""
    issues = []
    for card in cards:
        lines = [(no, prose(t)) for no, t in card.lines if prose(t)]
        idx = next((i for i, (_, t) in enumerate(lines) if re.search(r"3줄\s*요약", t)), None)
        if idx is None or card.index == 0:
            continue
        summary = lines[idx + 1 : idx + 4]
        if summary and title and not (key_tokens([title]) & key_tokens([summary[0][1]])):
            issues.append(
                Issue(
                    "WARN",
                    card.index,
                    summary[0][0],
                    "3줄 요약 첫 줄이 제목(도입 질문)과 이어지지 않음. 첫 줄은 질문에 대한 답으로",
                    summary[0][1],
                )
            )
        for no, text in summary:
            if ACT_EQUALS_THING.search(text):
                issues.append(
                    Issue("WARN", card.index, no, "'~한 게 X임' 구문. 행동이 아니라 대상을 주어로 쓸 것", text)
                )
    return issues


def spine_names(report_dir: Path) -> list[str]:
    """Spine node names from outline.md ("- 마디: <name> | ..."); empty if none."""
    outline = report_dir / "outline.md"
    if not outline.exists():
        return []
    return [n for n in SPINE_NODE.findall(outline.read_text(encoding="utf-8")) if n]


def check(report_dir: Path, report_name: str, allow_missing_figures: bool = False) -> list[Issue]:
    report = report_dir / report_name
    raw = report.read_text(encoding="utf-8")
    ledger_path = report_dir / "ledger.md"
    ledger = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else ""
    sources = [ledger]
    for name in ("text.txt", "lineage.md"):
        p = report_dir / name
        if p.exists():
            sources.append(p.read_text(encoding="utf-8", errors="ignore"))
    # Compare whole numbers, not substrings: "17" must not match inside "2017".
    known_numbers = {normalize_num(n) for n in NUMBER.findall(" ".join(sources))}
    ledger_ids = set(LEDGER_ID.findall(ledger))

    issues: list[Issue] = []
    if not ledger:
        issues.append(Issue("ERROR", 0, 0, "ledger.md 없음. 원장부터 만들 것"))
    if not (report_dir / "outline.md").exists():
        issues.append(Issue("WARN", 0, 0, "outline.md 없음. 기획서(planning.md 8절)를 먼저 쓸 것"))

    cards = split_cards(raw)
    endings: Counter[str] = Counter()
    kek_total = raw.count("ㅋㅋ")
    exclaim_total = sum(prose(line).count("!") for line in raw.splitlines())

    for card in cards:
        is_source = any(k in card.title.lower() for k in SKIP_CARD_TITLES)
        content_lines = 0
        tags_in_card: list[str] = []
        run_char, run_len = "", 0
        block_len = 0
        in_code = False
        for no, line in card.lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            for group in TAG.findall(line):
                tags_in_card += re.findall(r"[FLPW]\d+", group)
            for alt in re.findall(r"!\[([^\]]*)\]", line):
                if FIGURE_META_ALT.search(alt):
                    issues.append(
                        Issue(
                            "WARN", card.index, no, "그림 설명에 캡처 방식 언급. 무엇을 보여주는 그림인지만 쓸 것", alt
                        )
                    )
            for src in IMAGE.findall(line):
                if not src.startswith(("http://", "https://", "data:")) and not (report_dir / src).exists():
                    issues.append(
                        Issue("WARN" if allow_missing_figures else "ERROR", card.index, no, f"그림 파일 없음: {src}")
                    )
            if not stripped:
                block_len = 0
                continue
            text = prose(line)
            if stripped.startswith("#"):
                heading = stripped.lstrip("# ")
                if card.index > 0 and stripped.startswith("##"):
                    issues.append(
                        Issue(
                            "WARN", card.index, no, "소제목은 쓰지 않음. 첫 줄이 앞 파트를 받아 이어가게 할 것", heading
                        )
                    )
                if PROFANITY.search(heading):
                    issues.append(Issue("ERROR", card.index, no, "제목에 비속어", heading))
                if ledger and not is_source:
                    for num in NUMBER.findall(heading):
                        value = normalize_num(num)
                        if float(value) >= MIN_NUMBER_TO_TRACE and value not in known_numbers:
                            issues.append(
                                Issue(
                                    "ERROR",
                                    card.index,
                                    no,
                                    f"제목의 숫자 {num}이(가) 원문/원장/계보 어디에도 없음",
                                    heading,
                                )
                            )
                continue
            block_len += 1
            if block_len == MAX_LINES_WITHOUT_BLANK + 1 and not is_source:
                issues.append(
                    Issue("WARN", card.index, no, f"빈 줄 없이 {MAX_LINES_WITHOUT_BLANK}줄 초과. 장면 전환 빈 줄 넣기")
                )
            if not text:
                continue
            content_lines += 1
            if is_source:
                continue
            quoted = text.startswith((">", '"', "“", "'"))
            if PROFANITY.search(text):
                issues.append(Issue("ERROR", card.index, no, f"비속어: {PROFANITY.search(text).group(0)}", text))
            if SLANG_GAE.search(text):
                issues.append(Issue("WARN", card.index, no, "'개-' 강조 비속어성 표현", text))
            if not quoted and HONORIFIC.search(text):
                issues.append(Issue("ERROR", card.index, no, "존댓말 어미. 음슴체로", text))
            if not quoted and DECLARATIVE.search(text):
                issues.append(Issue("WARN", card.index, no, "평서체(~다) 어미. 음슴체로", text))
            if len(text) > HARD_MAX_LINE_CHARS:
                issues.append(Issue("ERROR", card.index, no, f"{len(text)}자 줄. 호흡 단위로 쪼갤 것", text))
            elif len(text) > MAX_LINE_CHARS:
                issues.append(Issue("WARN", card.index, no, f"{len(text)}자 줄 (권장 {MAX_LINE_CHARS}자 이하)", text))
            for phrase in AI_PHRASES:
                if phrase in text:
                    issues.append(Issue("WARN", card.index, no, f"AI 문체: '{phrase}'", text))
            if FIGURE_META_BODY.search(text):
                issues.append(Issue("WARN", card.index, no, "본문에 캡처/사용 허락 얘기. 독자에게 불필요함", text))
            for inner in PAREN_GLOSS.findall(text):
                names_only = "," in inner and all(seg.strip().count(" ") <= 1 for seg in inner.split(","))
                if inner.count(" ") >= 2 and not re.search(r"\d", inner) and not names_only:
                    issues.append(Issue("WARN", card.index, no, "괄호로 설명을 때움. 문장 흐름 안에서 풀 것", text))
                    break
            if "킬포" in text:
                issues.append(
                    Issue("WARN", card.index, no, "'킬포' 라벨. 바로 앞에 무엇과 대비되는지 밑밥이 있는지 확인", text)
                )
            if FUN_DECLARATION.search(text):
                issues.append(
                    Issue("WARN", card.index, no, "재미를 선언하는 줄. 기대와 결과를 나란히 놓아 보여줄 것", text)
                )
            if EMOJI.search(text):
                issues.append(Issue("WARN", card.index, no, "이모지", text))
            if DASHES.search(text):
                issues.append(Issue("WARN", card.index, no, "줄표(—). 줄바꿈이나 쉼표로", text))
            last = re.sub(r"[\s.!?~,)\]\"'”]+$", "", text)[-1:]
            if last:
                endings[last] += 1
                if last == run_char:
                    run_len += 1
                    if run_len == MONOTONE_RUN:
                        issues.append(
                            Issue("WARN", card.index, no, f"'{last}' 어미 {MONOTONE_RUN}줄 연속. 리듬 섞기", text)
                        )
                else:
                    run_char, run_len = last, 1
            if ledger:
                for num in NUMBER.findall(text):
                    value = normalize_num(num)
                    try:
                        if float(value) < MIN_NUMBER_TO_TRACE:
                            continue
                    except ValueError:
                        continue
                    if value not in known_numbers:
                        issues.append(
                            Issue(
                                "ERROR",
                                card.index,
                                no,
                                f"숫자 {num}이(가) 원문/원장/계보 어디에도 없음. 원장에 근거 추가",
                                text,
                            )
                        )
        for tag in tags_in_card:
            if ledger and tag not in ledger_ids:
                issues.append(Issue("ERROR", card.index, card.start_line, f"원장에 없는 태그 {tag}"))
        if not is_source and content_lines >= 3 and not tags_in_card:
            issues.append(
                Issue("WARN", card.index, card.start_line, f"파트 '{card.title or card.index}'에 원장 태그 없음")
            )
        short_ok = any(k in card.title for k in SHORT_OK_TITLES)
        if (
            card.index > 0
            and not is_source
            and not short_ok
            and not (CARD_MIN_LINES <= content_lines <= CARD_MAX_LINES)
        ):
            issues.append(
                Issue(
                    "WARN",
                    card.index,
                    card.start_line,
                    f"파트 길이 {content_lines}줄 (권장 {CARD_MIN_LINES}~{CARD_MAX_LINES})",
                )
            )

    if not (CARD_MIN_COUNT <= len(cards) <= CARD_MAX_COUNT):
        issues.append(Issue("WARN", 0, 0, f"파트 {len(cards)}개 (권장 {CARD_MIN_COUNT}~{CARD_MAX_COUNT})"))
    if kek_total > MAX_KEK:
        issues.append(Issue("WARN", 0, 0, f"ㅋㅋ {kek_total}회 (최대 {MAX_KEK})"))
    if exclaim_total > MAX_EXCLAIM:
        issues.append(Issue("WARN", 0, 0, f"느낌표 {exclaim_total}회 (최대 {MAX_EXCLAIM})"))

    content_cards = [
        c
        for c in cards[1:]
        if not any(k in c.title.lower() for k in SKIP_CARD_TITLES)
        and not any(k in c.title for k in SHORT_OK_TITLES)
        and "내용 추가" not in c.title
    ]
    with_image = [c for c in content_cards if any(IMAGE.search(t) for _, t in c.lines)]
    n_images = len(IMAGE.findall(raw))
    if n_images < MIN_IMAGES:
        issues.append(
            Issue(
                "WARN", 0, 0, f"그림 {n_images}장 (최소 {MIN_IMAGES}). 원문 그림·표, 인용 크롭(quote), 전작 그림 추가"
            )
        )
    own = OWN_GRAPHIC.findall(raw)
    if len(own) > MAX_OWN_GRAPHICS:
        issues.append(
            Issue(
                "WARN",
                0,
                0,
                f"직접 만든 정리 이미지 {len(own)}장 (최대 {MAX_OWN_GRAPHICS}). "
                "앞부분 지도 1장만 남기고 나머지는 원문 인용 크롭으로",
            )
        )
    if content_cards and len(with_image) / len(content_cards) < MIN_IMAGE_CARD_RATIO:
        bare = ", ".join(str(c.index) for c in content_cards if c not in with_image)
        issues.append(
            Issue(
                "WARN",
                0,
                0,
                f"그림 있는 내용 파트 {len(with_image)}/{len(content_cards)} "
                f"(권장 {int(MIN_IMAGE_CARD_RATIO * 100)}% 이상). 그림 없는 파트: {bare}",
            )
        )
    issues.extend(flow_issues(cards))
    issues.extend(slop_issues(cards))
    summary_parts = [c.index for c in cards if any(re.search(r"3줄\s*요약", t) for _, t in c.lines)]
    if summary_parts and max(summary_parts) == 0:
        issues.append(Issue("WARN", 0, 0, "3줄 요약이 맨 위에만 있음. 맺음 파트 끝(출처 앞)에 둘 것"))
    issues.extend(summary_issues(cards))
    names = spine_names(report_dir)

    def mentions_spine(text: str) -> bool:
        return any(n in text for n in names) if names else bool(SPINE_MARK.search(text))

    spine_parts = [c.index for c in cards if any(mentions_spine(t) for _, t in c.lines)]
    if len(spine_parts) < MIN_SPINE_PARTS:
        label = ", ".join(names) if names else "①②"
        issues.append(
            Issue(
                "WARN",
                0,
                0,
                f"척추 마디({label})가 {len(spine_parts)}개 파트에만 나옴 "
                f"(권장 {MIN_SPINE_PARTS}개 이상). 파트마다 척추 마디 이름을 불러서 연결할 것",
            )
        )
    found = [name for name, pat in COMMUNITY_MARKERS.items() if pat.search(raw)]
    if len(spine_parts) >= MIN_SPINE_PARTS:
        found.append("척추 마디")
    if len(found) < MIN_COMMUNITY_MARKERS:
        missing = [n for n in COMMUNITY_MARKERS if n not in found]
        issues.append(
            Issue(
                "WARN",
                0,
                0,
                f"자동 감지된 커뮤니티 요소 {len(found)}개 (권장 {MIN_COMMUNITY_MARKERS}개 이상). "
                f"빠진 것: {', '.join(missing)}",
            )
        )

    total = sum(endings.values()) or 1
    top = ", ".join(f"{k} {v * 100 // total}%" for k, v in endings.most_common(8))
    issues.append(
        Issue(
            "INFO",
            0,
            0,
            f"파트 {len(cards)}개, 그림 {n_images}장, 그림 있는 내용 파트 {len(with_image)}/{len(content_cards)}",
        )
    )
    issues.append(Issue("INFO", 0, 0, f"자동 감지된 커뮤니티 요소: {', '.join(found) or '없음'}"))
    issues.append(Issue("INFO", 0, 0, f"어미 분포: {top}"))
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", help="report folder")
    parser.add_argument("--report", default="report.md")
    parser.add_argument("--strict", action="store_true", help="treat WARN as failure")
    parser.add_argument(
        "--allow-missing-figures",
        action="store_true",
        help="downgrade missing image files to WARN (examples shipped without third-party figures)",
    )
    args = parser.parse_args()
    report_dir = Path(args.dir).expanduser().resolve()
    issues = check(report_dir, args.report, args.allow_missing_figures)
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    for it in sorted(issues, key=lambda i: (order[i.level], i.card, i.line)):
        loc = f"part {it.card:>2} L{it.line:<4}" if it.line else "global      "
        tail = f"  | {it.text[:60]}" if it.text else ""
        print(f"{it.level:<5} {loc} {it.msg}{tail}")
    n_err = sum(i.level == "ERROR" for i in issues)
    n_warn = sum(i.level == "WARN" for i in issues)
    print(f"\n{n_err} errors, {n_warn} warnings")
    sys.exit(1 if n_err or (args.strict and n_warn) else 0)


if __name__ == "__main__":
    main()
