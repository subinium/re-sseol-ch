# re-sseol-ch (리썰치)

[![version](https://img.shields.io/badge/version-0.1.0-1d5bd6)](CHANGELOG.md)
[![CI](https://github.com/subinium/re-sseol-ch/actions/workflows/ci.yml/badge.svg)](https://github.com/subinium/re-sseol-ch/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-161616)](LICENSE)

논문을 커뮤니티 정보글처럼 풀어 주는 에이전트 스킬입니다. 이름은 리서치에 썰을 붙였습니다.

arXiv 링크를 주면 음슴체로 짧게 끊어 쓴 해설글이 나옵니다. 설명 사이사이에 논문에서 잘라 온 그림과 원문 문단이 붙습니다. 글에 들어가는 숫자와 날짜는 전부 팩트 원장(`ledger.md`)에 출처를 적도록 되어 있습니다.

[Agent Skills](https://agentskills.io) 규격을 따르기 때문에 Claude Code 말고도 Codex, Gemini CLI, Cursor 같은 에이전트에서 쓸 수 있습니다.

## 설치

여러 에이전트에 한 번에 넣으려면 [skills](https://github.com/vercel-labs/skills) CLI가 편합니다. 설치된 에이전트를 찾아서 각자의 스킬 폴더에 넣어 줍니다.

```bash
npx skills add subinium/re-sseol-ch
```

특정 에이전트에만 넣으려면 `-a codex`처럼 이름을 붙이고, 어느 프로젝트에서나 쓰려면 `-g`를 붙입니다.

Claude Code는 플러그인으로도 설치할 수 있습니다.

```bash
claude plugin marketplace add subinium/re-sseol-ch
claude plugin install paper-report@re-sseol-ch
```

직접 복사해도 됩니다. `skills/paper-report` 폴더를 쓰는 에이전트의 스킬 폴더에 넣으면 됩니다.

| 에이전트 | 전역 스킬 폴더 |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.codex/skills/` |
| Gemini CLI | `~/.gemini/skills/` |
| GitHub Copilot | `~/.copilot/skills/` |
| Cursor | `~/.cursor/skills/` |
| OpenCode | `~/.config/opencode/skills/` |

claude.ai에는 `skills/paper-report` 폴더를 zip으로 묶어 올리면 됩니다.

### 필요한 것

[uv](https://docs.astral.sh/uv/)와 Chrome(또는 Chromium)이 있어야 합니다. 스크립트 의존성은 `uv run`이 알아서 받습니다. Chrome 경로가 특이하면 `CHROME_PATH`로 알려 주세요. 한글은 웹 폰트로 렌더링하는데, 오프라인이거나 Linux라면 `fonts-noto-cjk` 같은 한글 폰트를 깔아 두세요.

Semantic Scholar, OpenAlex API 키(`S2_API_KEY`, `OPENALEX_API_KEY`)는 없어도 돌아갑니다. 다만 키가 없으면 계보 조회가 호출 제한에 자주 걸립니다.

## 쓰는 법

Claude Code에서는 이렇게 부릅니다.

```
/paper-report 1706.03762
/paper-report https://arxiv.org/abs/2609.31562
/paper-report 10.1038/s41586-021-03819-2
/paper-report ~/Downloads/paper.pdf
/paper-report 분야:확산 모델
```

플러그인으로 설치했는데 다른 명령과 이름이 겹치면 `/paper-report:paper-report`로 부르면 됩니다. 다른 에이전트에서는 그 에이전트가 스킬을 부르는 방식을 쓰거나, 그냥 "이 논문 커뮤니티식으로 설명해줘"라고 하면 됩니다.

arXiv ID나 URL, DOI, PDF 경로, 논문 제목을 넣으면 됩니다. `분야:확산 모델`처럼 쓰면 그 분야 대표 논문 4~8편을 골라 연재로 씁니다.

결과물은 `reports/<논문>/`에 모입니다.

- `viewer.html`, `post.html`: 흰 배경 게시글
- `gallery.html`: 파트별 이미지를 한 장씩 넘겨 보는 짤 모드
- `cards/NN.png`: 폰 화면 폭(430px, 3배율)으로 찍은 파트별 캡처. `post_NN.png`는 이걸 이어 붙인 긴 이미지
- `social/NN_k.png`: X·스레드에 올릴 4:5(1080×1350) 이미지. 문단 사이에서 잘라서 그림이 중간에 끊기지 않습니다
- `report.md`, `ledger.md`, `outline.md`: 원고, 팩트 원장, 기획서

## 한 편이 만들어지는 과정

원문을 끝까지 읽고 참고문헌과 후속작, 저자를 조사해서 팩트 원장부터 채웁니다. 기획은 그다음입니다. 논문 목차는 따라가지 않습니다. 독자가 이미 아는 것에서 질문을 하나 던진 뒤, 그 질문에서 이어지는 질문 사슬을 따라 끝까지 끌고 갑니다.

다 쓰면 검사 스크립트를 돌립니다. 원장에 없는 숫자, 앞 파트와 안 이어지는 파트, 같은 모양으로 찍어낸 나열이나 정리 멘트 같은 걸 잡습니다. 그다음 원고를 처음 보는 서브에이전트들이 팩트체크와 흐름 검토를 합니다. 마지막으로 음슴체는 그대로 둔 채 AI 티만 걷어내는 윤문을 한 번 거치고 이미지를 뽑습니다. 서브에이전트를 못 띄우는 에이전트에서는 같은 검토를 한 대화 안에서 합니다.

절차와 기준은 [`SKILL.md`](skills/paper-report/SKILL.md)와 [`references/`](skills/paper-report/references/)에, 완성본 예시는 [`examples/transformer/`](skills/paper-report/examples/transformer/)에 있습니다.

## im-not-ai와 같이 쓰기

리썰치의 윤문 단계는 [im-not-ai](https://github.com/epoko77-ai/im-not-ai)의 한국어 AI 문체 규칙 일부를 가져와 씁니다. "A가 아니라 B" 대구 반복, "핵심은 ~임" 같은 분열문, 설명형 "~는 거임"이 몰린 곳을 찾아 그 줄만 고칩니다.

더 꼼꼼하게 다듬고 싶으면 im-not-ai의 humanize-korean 스킬을 같이 쓰세요. 번역투, 리듬, 접속사까지 보는 전용 윤문 스킬이라 리썰치 원고를 한 번 더 통과시키면 문장이 더 자연스러워질 수 있습니다. 원문이 구어체면 구어체로 두는 규칙이 있어서 음슴체는 대체로 유지됩니다.

```bash
claude plugin marketplace add epoko77-ai/im-not-ai
claude plugin install humanize-korean@im-not-ai
```

원고가 나온 뒤, 이미지를 뽑기 전에 부릅니다.

```
/humanize reports/<논문>/report.md 장르: 블로그
```

두 가지는 직접 챙겨 주세요.

- im-not-ai는 문단마다 긴 문장을 하나씩 섞으라고 권하는데, 한 줄에 한 호흡인 음슴체와는 맞지 않습니다. 줄바꿈은 그대로 두라고 같이 적어 주세요.
- 원고 줄 끝의 원장 태그(`<!-- F3 -->`)가 빠지면 사실 추적이 끊깁니다. 윤문본을 `report.md`에 반영한 뒤 `check_report.py`를 다시 돌리면 태그가 빠진 파트를 경고로 알려 줍니다. 그다음 이미지를 다시 뽑으면 됩니다.

## 한계

- 한 편을 만드는 데 단계가 많아서 시간이 꽤 걸리고 토큰도 많이 씁니다.
- 팩트체크도 결국 모델이 합니다. 공개하기 전에 원장과 원문을 사람이 한 번 대조해 보세요.
- 그림 자동 크롭은 틀릴 수 있습니다. 스킬이 열어서 확인하게 돼 있지만 결과물에서 한 번 더 보세요.
- API 키가 없으면 계보 조회가 호출 제한에 걸려 일부만 채워질 수 있습니다.
- 한국어 해설 전용입니다.

## 개발

CI와 같은 검사를 로컬에서 이렇게 돌립니다.

```bash
uvx ruff check skills/paper-report/scripts
uvx ruff format --check skills/paper-report/scripts
uvx --from skills-ref agentskills validate skills/paper-report
uv run skills/paper-report/scripts/check_report.py skills/paper-report/examples/transformer --allow-missing-figures
```

예시 폴더에는 저작권 때문에 논문 그림을 넣지 않았습니다. 그래서 예시를 검사할 때는 `--allow-missing-figures`를 붙입니다. 그림까지 다시 만드는 방법은 [예시 README](skills/paper-report/examples/transformer/README.md)에 있습니다.

버그나 제안은 이슈로 남겨 주세요. PR을 보낼 때는 위 검사를 먼저 돌려 주시고, 한 PR에는 한 가지 변경만 담아 주세요.

버전은 [유의적 버전](https://semver.org/lang/ko/)을 따릅니다. 올릴 때는 `.claude-plugin/plugin.json`의 `version`과 [`CHANGELOG.md`](CHANGELOG.md) 맨 위 항목을 같이 바꾸고 `vX.Y.Z` 태그를 답니다. plugin.json과 CHANGELOG의 버전이 다르면 CI가 실패합니다.

```
.claude-plugin/           Claude Code 플러그인·마켓플레이스 매니페스트
skills/paper-report/
├── SKILL.md              절차
├── references/           기획, 말투, 그림, 팩트체크, 계보, 사람 맥락, 채점 가이드
├── scripts/              수집, 그림 추출, 계보, 이미지, 검사, 빌드
├── assets/               게시글·짤 모드 스타일과 템플릿
└── examples/transformer/ 예시 기획서, 원고, 원장
```

## 참고

논문 그림과 원문 문장의 저작권은 각 논문에 있습니다. 결과물을 공개할 때는 논문 라이선스를 확인해 주세요. 외부 LLM API는 쓰지 않습니다. 논문 정보는 arXiv, Crossref, Semantic Scholar, OpenAlex의 공개 API에서 받습니다.

한국어 AI 문체 규칙 일부는 [im-not-ai](https://github.com/epoko77-ai/im-not-ai)(MIT)에서 가져왔습니다. 가져온 규칙은 [말투 가이드](skills/paper-report/references/style-guide.md) 4절에 규칙 번호와 함께 적어 두었습니다.

라이선스는 [MIT](LICENSE)입니다.
