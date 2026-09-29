# 예시: Attention Is All You Need

`paper-report`로 만든 해결형 논문 해설이다. 기획서를 채우는 법, 파트의 호흡, 원장 ID를 다는 법을 볼 때 참고한다. 도입 질문과 척추 구도는 이 논문에 맞춘 것이라 다른 논문에 그대로 옮기지 않는다.

| 파일 | 내용 |
|---|---|
| `report.md` | 원고 (26파트) |
| `outline.md` | 기획서. 유형, 도입 질문, 척추, 밑밥과 회수, 파트 목록 |
| `ledger.md` | 팩트 원장 |
| `meta.json` | 원문 메타데이터 |
| `graphics/lineage.json` | 계보 요약 이미지 스펙. 직접 만든 이미지는 이 한 장이고 나머지는 원문 인용이다 |
| `figures/g_lineage.png` | 위 스펙으로 만든 이미지 |

## 그림

논문과 전작·후속작 그림은 저작권 때문에 넣지 않았다. 이 폴더를 검사할 때는 "그림 파일 없음"을 경고로 낮춘다.

```bash
uv run skills/paper-report/scripts/check_report.py skills/paper-report/examples/transformer --allow-missing-figures
```

그림까지 다시 만들려면 레포 루트에서 원문을 받아 자른 뒤, 원고와 같은 폴더에 둔다.

```bash
S=skills/paper-report/scripts
E=skills/paper-report/examples/transformer
uv run $S/fetch_paper.py 1706.03762 --out reports
D=reports/arxiv-1706.03762
uv run $S/extract_figures.py $D          # tab1_p6, tab2_p8, tab3_p9, fig1_p3, fig2_p4
uv run $S/extract_figures.py $D crop --page 14 --bbox 0.19,0.455,0.83,0.77 --name fig4_its --rotate 90
uv run $S/extract_figures.py $D crop --page 13 --bbox 0.19,0.12,0.84,0.385 --name fig3_making --rotate 90
uv run $S/extract_figures.py $D crop --page 4 --bbox 0.34,0.565,0.86,0.628 --name eq1
uv run $S/extract_figures.py $D crop --page 1 --bbox 0.12,0.10,0.88,0.47 --name p1_header
cp $E/report.md $E/ledger.md $E/outline.md $D/ && cp $E/figures/g_lineage.png $D/figures/
uv run $S/check_report.py $D
```

전작·후속작 그림(`prior_*`, `next_*`)은 해당 논문(1409.3215, 1409.0473, 1810.04805, 2010.11929)을 받아 `crop --dest $D`로 자른다. 자동 크롭은 열어서 확인하고, 잘못 잘렸으면 좌표를 조정한다.
