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
- **LLM 백엔드 추상화(`llm_backend.py`)** — Pass1b(헤더 계층 판단)가 Ollama `/api/chat`에
  강결합돼 있던 걸 `LLMBackend` 인터페이스로 분리, `OpenAICompatBackend`로 OpenAI/Groq/Gemini 등
  `/chat/completions` 호환 API도 그대로 호출 가능(`--backend {ollama,openai,groq,gemini}`).
  Ollama 경로는 100% 하위 호환(기존 `model`/`host` 인자 그대로 동작).

## 설치

```bash
pip install -e .
```

## 사용법

```bash
hwp2md doctor                              # npx/Ollama 환경 점검만
hwp2md convert input.hwp -o output.md      # 단일 파일 (기본: 로컬 Ollama)
hwp2md convert input_dir -o output_dir     # 폴더 전체 배치 처리
hwp2md convert input_dir -o output_dir --recursive   # 하위 폴더까지

# 클라우드 LLM 백엔드 사용 (로컬 GPU/Ollama 없이)
hwp2md convert input.hwp -o output.md --backend groq --model openai/gpt-oss-20b
# --api-key를 직접 안 주면 GROQ_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY 환경변수(또는 cwd의
# .env, python-dotenv 설치돼 있으면 자동 로드)에서 읽는다.
```

## LLM 백엔드 검증 메모 (2026-08-23)

Groq 무료 티어(`openai/gpt-oss-20b`)로 `OpenAICompatBackend` 실제 호출 검증 완료:
- **Cloudflare 403 (error code: 1010)** — urllib 기본 User-Agent(`Python-urllib/x.y`)를 Groq의
  Cloudflare가 차단한다. 일반적인 User-Agent 헤더로 바꿔서 해결(`llm_backend.py`에 반영됨).
- **TPM(분당 토큰) 제한** — 신규 계정 기본 한도가 8000 TPM인데, 이 파이프라인의 시스템 프롬프트
  (한국어, 약 4500토큰)만으로 최소 요청도 이 한도에 근접/초과한다(실측: 후보 34개 문서 요청 시
  9121 토큰 필요, 8000 한도 초과). 작은 후보 목록(3개)으로 인터페이스 자체는 정상 동작 확인
  (정확한 분류 반환, "대표자 : (인)" 서명란도 not_heading으로 정확히 판단) — 실사용 시 Groq
  무료 티어를 쓰려면 `classify_headings_pass1._MAX_CANDIDATES_PER_CALL`(현재 70, Ollama VRAM
  기준으로 튜닝됨)을 훨씬 작게(10~15 수준) 낮추거나 Dev Tier로 업그레이드해야 함 — 코드 버그
  아님, 계정 등급에 따른 제약.

## 다음 단계 (미착수)

- Groq 실사용을 위한 청크 크기 자동 조정(제공자별 TPM 한도에 맞춰 `_MAX_CANDIDATES_PER_CALL`을
  동적으로 낮추는 옵션)
- 실제 웹 API(FastAPI 등) 레이어 — 지금은 CLI까지만, 이 패키지 구조 위에 얹으면 됨
