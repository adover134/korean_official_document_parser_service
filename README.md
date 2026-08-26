# hwp-hierarchical-md

계층 구조를 가진 한글(HWP/HWPX) 공문서를, 원문의 섹션 계층(대분류·중분류·첨부)을 보존한 채
Markdown으로 변환하는 pip 설치 가능한 패키지 + CLI + 배포용 API. 한국 공공기관 입찰공고문 42건을
대상으로 검증했다.

HWP→Markdown 변환 라이브러리(예: [`kordoc`](https://www.npmjs.com/package/kordoc))는 표·서식·
읽기 순서는 충실히 보존하지만, "이 줄이 최상위 섹션 제목인지, 그 하위 세부항목인지, 아니면 서명란
같은 비-헤더인지"는 판단하지 못한다. 원문 스타일 메타데이터(폰트 크기, 볼드 여부)만으로는 계층을
안정적으로 재구성할 수 없다 — 같은 "1. 2. 3." 번호 매김이 문서 최상위 섹션에도, 그 안의
체크리스트에도, 별도 첨부(붙임/서식)에도 반복해서 쓰이기 때문이다. 그렇다고 LLM에게 문서 전체를
통째로 다시 쓰게 하면, 문단이 빠지거나 없는 헤더가 생기는 등 콘텐츠 손실 위험이 생긴다. 이 도구는
"판단"(어디가 헤더인가)과 "변환"(원문을 그대로 옮기는 것)을 분리해, LLM은 각 줄이 헤더인지
아닌지만 판단하게 하고 원문 자체는 건드리지 않는 2-pass 파이프라인으로 이 문제를 푼다.

## 특징

- **CLI 폴더 배치 처리** — `hwp2md convert`가 단일 파일뿐 아니라 폴더 입력(재귀 옵션 포함)도
  받는다 (`cli.py`).
- **사전 환경 점검** — kordoc(npx)·Ollama가 준비 안 된 상태에서 처리 도중 알아보기 힘든
  subprocess 에러로 죽는 대신, 시작 전에 `hwp2md doctor`(또는 `convert` 내장 사전 점검)로 분명한
  진단 메시지를 낸다.
- **pip 설치 가능한 패키지 구조** (`pyproject.toml`, `src/` 레이아웃, `hwp2md` 콘솔 스크립트
  진입점).
- **배포용 HTTP API** (`api.py`, FastAPI) — `POST /v1/convert`에 HWP/HWPX 파일을 올리면
  Markdown을 반환한다. 서버 쪽에서 백엔드를 환경변수로 미리 설정해두므로(`HWP2MD_BACKEND` 등)
  클라이언트가 자기 LLM 키를 들고 올 필요가 없다. `Dockerfile`로 바로 컨테이너 배포 가능.
- **LLM 백엔드 추상화** (`llm_backend.py`, Ollama + OpenAI 호환 — Groq/OpenAI/Gemini 등 어떤
  OpenAI 호환 엔드포인트도 백엔드로 쓸 수 있음).

## 표 처리

`normalize_html_tables.py`가 kordoc의 raw HTML `<table>`을 GFM pipe-table로 정규화한다.
rowspan/colspan 병합 셀 처리, 밑줄 태그·PUA 유니코드 잔재 제거 외에, 실제 헤더가 없는 표(라벨:값
나열형)나 2단 헤더(대분류+소분류) 표도 GFM pipe-table 문법이 헤더 행을 강제하는 것과 무관하게
정확히 처리한다 — 헤더 없는 표는 애초에 표로 안 만들고 `label: value` 텍스트로 직행하고, 연속된
헤더 행은 컬럼별로 하나로 합친다. RAG 파서로 쓸 때(다운스트림이 표를 자연어로 다시 평탄화하는
경우) 다운스트림이 "이게 진짜 헤더인지 위장된 헤더인지"를 텍스트 패턴만으로 추측할 필요가
없어진다.

## 설치

```bash
pip install -e .              # CLI만
pip install -e ".[api]"       # CLI + API(FastAPI) 서버까지
pip install -e ".[api,dotenv]"  # + .env 자동 로드
```

## CLI 사용법

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

## API 사용법

```bash
uvicorn hwp_hierarchical_md.api:app --host 0.0.0.0 --port 8000
```

서버 환경변수(클라이언트가 아니라 배포하는 쪽이 한 번 설정):

| 변수 | 기본값 | 설명 |
|---|---|---|
| `HWP2MD_BACKEND` | `ollama` | `ollama` \| `openai` \| `groq` \| `gemini` |
| `HWP2MD_MODEL` | `qwen3.5:9b` | 모델명 |
| `HWP2MD_HOST` | `http://localhost:11434` | `ollama`일 때만, Ollama 서버 주소 |
| `HWP2MD_API_KEY` | - | `ollama`가 아닐 때 필요 (또는 `OPENAI_API_KEY` 등 표준 이름도 인식) |
| `HWP2MD_BASE_URL` | - | openai/groq/gemini는 기본값 있음, 다른 제공자는 직접 지정 |

엔드포인트:
- `GET /v1/health` — npx/백엔드 설정이 유효한지 점검
- `POST /v1/convert` — multipart 파일 업로드(`file` 필드) -> Markdown 텍스트 반환

```bash
curl -X POST http://localhost:8000/v1/convert -F "file=@공고문.hwp"
```

## Docker로 배포

```bash
docker build -t hwp-hierarchical-md .
docker run -p 8000:8000 -e HWP2MD_BACKEND=groq -e HWP2MD_API_KEY=$GROQ_API_KEY \
  -e HWP2MD_MODEL=openai/gpt-oss-20b hwp-hierarchical-md
```

이미지에는 Ollama를 넣지 않는다(GPU 필요한 별도 컴포넌트) — `HWP2MD_BACKEND`를 클라우드
제공자로 설정하거나, Ollama를 별도 컨테이너/호스트로 띄우고 `HWP2MD_HOST`로 가리키면 된다.

## 라이선스

**MIT + [Commons Clause](https://commonsclause.com/)**(`LICENSE` 참고) — 사용·수정·재배포는
MIT 그대로 자유롭지만, 이 소프트웨어의 기능에서 가치가 나오는 유료 상품/서비스(호스팅 포함)로
**판매**하는 것만 제한한다. 원저작자는 이 라이선스에 스스로 묶이지 않으므로 별도로 상용 배포할 수
있다 — 코드는 동일하고 배포 방식(관리형 호스팅 등)만 유료화하는 구조([rembg](https://github.com/danielgatis/rembg)
류의 "오픈소스 SaaS" 모델과 같은 패턴).

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
- API에 인증/rate limit 추가 (지금은 열려있는 상태로 배포하면 안 됨 — 프록시/게이트웨이 단에서
  막거나 직접 추가 필요)
