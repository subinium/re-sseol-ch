# 변경 기록

이 프로젝트의 주요 변경을 적는다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를, 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따른다.

버전을 올릴 때는 `.claude-plugin/plugin.json`의 `version`과 이 파일 맨 위 항목을 같이 바꾼다. CI가 둘이 같은지 확인한다.

## [Unreleased]

## [0.1.0] - 2026-09-29

첫 공개 버전.

### 추가

- `paper-report` 스킬: arXiv ID·URL, DOI, PDF, 제목을 받아 음슴체 커뮤니티 정보글로 해설한다.
- [Agent Skills](https://agentskills.io) 규격을 따라 Claude Code, Codex, Gemini CLI, Cursor 등에서 쓸 수 있다. `npx skills add`, Claude Code 플러그인, 폴더 복사로 설치한다.
- 9단계 절차: 원문 정독, 그림 준비, 계보·사람 조사, 팩트 원장, 기획, 집필, 검증(팩트체크, 흐름 검토, 윤문, 채점), 빌드, 보고.
- 논문 유형 7가지(해결형, 발견형, 측정·데이터형, 이론형, 반박형, 정리형, 제안·관점형)와 유형별 척추·전문성 재료.
- 팩트 원장(F 본문, L 계보, P 사람, W 웹)과 원고의 원장 ID 태그.
- 독립 팩트체크, 처음 읽는 독자 관점의 흐름·설명 검토, 7항목 채점표.
- 스크립트
  - `fetch_paper.py`: 원문 PDF, 메타데이터, 페이지 표시 본문 수집
  - `extract_figures.py`: 그림·표 자동 크롭, 좌표 크롭, 원문 문단 인용 크롭(`quote`)
  - `lineage.py`: Semantic Scholar·OpenAlex 기반 참고문헌 순위, 인용 문맥, 후속작, 저자
  - `make_graphic.py`: 계보 타임라인·척추 지도 이미지
  - `check_report.py`: 말투, 파트 연결, 숫자 추적, 그림, 3줄 요약, 음슴체로 바꿔도 남는 AI 티(틀로 찍은 나열, 정리 멘트, 공식 마무리) 검사. 한국어 AI 문체 규칙 일부는 [im-not-ai](https://github.com/epoko77-ai/im-not-ai)(MIT)에서 가져옴
  - `build_cards.py`: 폰 캡처 같은 파트 이미지, 흰 배경 게시글, 짤 모드, X·스레드용 4:5 이미지, claude.ai Artifact용 파일
- 예시: 『Attention Is All You Need』 해설의 기획서, 원고, 원장.
- Claude Code 플러그인·마켓플레이스 매니페스트, CI(lint, 포맷, Agent Skills 규격 검증, 스크립트 실행, 매니페스트·버전 확인, 예시 원고 검사).

[Unreleased]: https://github.com/subinium/re-sseol-ch/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/subinium/re-sseol-ch/releases/tag/v0.1.0
