# hwp-hierarchical-md (service/CLI 버전, private)

공개 저장소 [hwp-hierarchical-md-skill](https://github.com/adover134/korean_official_document_parser_skill)의
파이프라인 코드를 pip 설치 가능한 패키지 + CLI로 재구성한 버전. 설계/아키텍처 설명은 공개 저장소의
README/SKILL.md를 그대로 참고 — 여기는 "서비스로 쓸 수 있게 만드는" 확장만 다룬다.

## 공개 스킬 저장소와의 차이

- `run_pipeline.py`가 단일 파일만 처리했던 것을, `hwp2md convert`가 **폴더 입력(배치 처리)**도
  받도록 확장 (`cli.py`).
- kordoc(npx)·Ollama가 준비 안 된 상태에서 처리 도중 알아보기 힘든 subprocess 에러로 죽는 대신,
  시작 전에 `hwp2md doctor`(또는 `convert` 내장 사전 점검)로 분명한 진단 메시지를 낸다.
- `pip install -e .`로 설치 가능한 패키지 구조(`pyproject.toml`, `src/` 레이아웃, `hwp2md` 콘솔
  스크립트 진입점)로 재구성 — 나중에 웹 API/서비스 레이어를 얹을 때 `hwp_hierarchical_md.run_pipeline`
  의 `run_stage1`/`run_pass1`/`run_pass2`를 그대로 import해서 쓸 수 있다.

## 설치

```bash
pip install -e .
```

## 사용법

```bash
hwp2md doctor                              # npx/Ollama 환경 점검만
hwp2md convert input.hwp -o output.md      # 단일 파일
hwp2md convert input_dir -o output_dir     # 폴더 전체 배치 처리
hwp2md convert input_dir -o output_dir --recursive   # 하위 폴더까지
```

## 다음 단계 (미착수)

- OpenAI 호환 LLM 백엔드 인터페이스 (Ollama 외 Groq/Gemini/OpenAI 등 클라우드 API로도 헤더 판단
  가능하게 — `classify_headings_pass1.py`의 `classify()`가 현재 Ollama `/api/chat`에 강결합돼
  있음)
- 실제 웹 API(FastAPI 등) 레이어 — 지금은 CLI까지만, 이 패키지 구조 위에 얹으면 됨
