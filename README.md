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
pip install -e ".[dotenv,tracing]"  # + Langfuse 트레이싱까지 (아래 "관측/트레이싱" 참고)
pip install -e ".[test]"      # 개발 시 테스트 실행용 (배포에는 불필요)
```

## 테스트

LLM 호출 없이 결정론적으로 동작하는 순수 변환 로직(`normalize_html_tables.py` 등)은
회귀 테스트로 고정돼 있다:

```bash
pip install -e ".[test]"
pytest tests/
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

# Groq 등 TPM 한도가 낮은 백엔드에서 배치 요청이 계속 실패하면 후보 배치 크기를 직접 낮춤
# (기본값은 Ollama VRAM 기준 70 — 아래 "LLM 백엔드 검증 메모" 참고)
hwp2md convert input.hwp -o output.md --backend groq --model openai/gpt-oss-20b --max-candidates-per-call 10
```

## API 사용법

```bash
uvicorn hwp_hierarchical_md.api:app --host 0.0.0.0 --port 8000
```

서버 환경변수(클라이언트가 아니라 배포하는 쪽이 한 번 설정). 아래 이 문서에 나오는
`HWP2MD_*`/`LANGFUSE_*` 환경변수는 전부 같은 방식으로 설정한다 — Docker/systemd 등
배포 환경에 직접 주입해도 되고, `pip install -e ".[api,dotenv]"`처럼 `dotenv` extra를
설치했다면 서버를 띄우는 작업 디렉터리의 `.env` 파일에 적어둬도 기동 시 자동으로 읽는다
(`_load_dotenv_if_present()`, `api.py`/`cli.py` 공통). 즉 로컬 개발 중엔 `.env`에,
운영 배포에선 컨테이너/오케스트레이터의 환경변수 주입 방식에 맞춰 쓰면 된다:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `HWP2MD_BACKEND` | `ollama` | `ollama` \| `openai` \| `groq` \| `gemini` |
| `HWP2MD_MODEL` | `qwen3.5:9b` | 모델명 |
| `HWP2MD_HOST` | `http://localhost:11434` | `ollama`일 때만, Ollama 서버 주소 |
| `HWP2MD_API_KEY` | - | `ollama`가 아닐 때 필요 (또는 `OPENAI_API_KEY` 등 표준 이름도 인식) |
| `HWP2MD_BASE_URL` | - | openai/groq/gemini는 기본값 있음, 다른 제공자는 직접 지정 |

엔드포인트:
- `GET /v1/health` — npx/백엔드 설정이 유효한지 점검(인증 불필요)
- `POST /v1/convert` — multipart 파일 업로드(`file` 필드) -> Markdown 텍스트 반환

```bash
# -H는 인증 활성화 시에만, -F max_candidates_per_call은 선택(아래 "Groq TPM 튜닝" 참고)
curl -X POST http://localhost:8000/v1/convert -F "file=@공고문.hwp" \
  -H "Authorization: Bearer $HWP2MD_API_KEYS" \
  -F "max_candidates_per_call=10"
```

### 인증 / rate limit

| 변수 | 기본값 | 설명 |
|---|---|---|
| `HWP2MD_API_KEYS` | - | 쉼표로 구분한 허용 API 키 목록. 미설정 시 인증 없이 열린 상태로 동작(로컬/사내망용) |
| `HWP2MD_RATE_LIMIT_PER_MINUTE` | - | API 키 하나당 분당 요청 한도. 미설정 시 rate limit 없음 |

`HWP2MD_API_KEYS`를 설정하면 `/v1/convert`가 `Authorization: Bearer <key>` 헤더를 요구한다
(없거나 틀리면 401). `HWP2MD_RATE_LIMIT_PER_MINUTE`까지 설정하면 API 키별 분당 요청 수를
제한한다(초과 시 429) — 별도 카운터 저장소 없이, 요청마다 이미 남기는 Langfuse 트레이스
(아래 "관측/트레이싱" 참고, 요청을 호출한 API 키가 `user_id`로 태깅됨)를 그대로 세어서
판단하므로 **rate limit을 쓰려면 Langfuse 설정이 먼저 돼 있어야 한다** — 아래 절의 안내대로
`.env`에 `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL`을 채우면 인증과
별개로 자동 활성화된다. Langfuse 미설정 시 인증은 그대로 동작하고 rate limit만 조용히
꺼진 채로 동작한다.

**한계(실측 확인됨, `rate_limit.py` 참고)**: Langfuse는 트레이스를 인입한 뒤 조회 API에서
실제로 반환되기까지 색인 지연이 있다 — 몇 초 안에도 안 잡히는 경우를 실측으로 확인했다.
그래서 이 rate limit은 정확한 실시간 카운터가 아니라 대략적인 제한이고, **같은 지연 구간에
몰린 버스트 요청은 한도를 넘겨도 통과할 수 있다.** 지속적인 남용을 거칠게 막는 용도로만
쓰고, 정확한 하드 리밋이 필요하면 별도 카운터(Redis 등)를 추가해야 한다.

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
  (정확한 분류 반환, "대표자 : (인)" 서명란도 not_heading으로 정확히 판단) — 배치 크기(기본 70,
  Ollama VRAM 기준으로 튜닝된 값)가 Groq 무료 티어 한도보다 크면 이제 자동으로 줄여 재시도한다
  (아래 "Groq TPM 대응" 참고, 2026-08-27) — 여전히 계속 실패하면 Dev Tier로 업그레이드가
  필요할 수 있음(코드 버그 아님, 계정 등급에 따른 제약).

## 관측/트레이싱 (Langfuse)

API 요청 하나당 트레이스 루트 span(`convert-document`, 호출한 API 키가 `user_id`로 태깅됨)
아래에 Pass1(헤더 분류, 유일하게 LLM을 호출하는 단계)의 span(`classify-document-headings`)이
중첩되고, 그 안에 실제 LLM 호출들이 generation으로 다시 중첩된다(후보가 많아 여러 배치로
나뉘면 배치별 generation이 형제로 묶임) — 모델명·입출력·토큰 사용량·백엔드
(`backend:OllamaBackend`/`backend:OpenAICompatBackend` 태그)가 전부 남는다
([Langfuse](https://langfuse.com), `tracing.py`로 선택적 활성화). 이 트레이스는 위
"인증 / rate limit" 절의 분당 요청 제한 판단에도 그대로 쓰인다.

```bash
pip install -e ".[dotenv,tracing]"
```

`.env`(또는 배포 환경변수)에 다음을 채우면 자동으로 활성화된다 — 안 채우면 조용히 비활성화된
채로(트레이싱 없이) 정상 동작한다:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com  # EU. US는 us.cloud.langfuse.com, 셀프호스팅은 그 URL
LANGFUSE_TRACING_ENVIRONMENT=development  # production/staging과 로컬 테스트 트레이스를 구분(권장)
```

이 파이프라인은 raw urllib로 LLM API를 직접 호출해서(OpenAI SDK를 안 씀) Langfuse의 자동
계측을 못 쓴다 — `tracing.py`가 수동으로 계측한다. 실제 호출 데이터는 이미 설치돼 있는
[langfuse-python](https://github.com/langfuse/langfuse-python) SDK로 바로 조회 가능하다
(Node.js 별도 설치가 필요한 Langfuse CLI 대신, `rate_limit.py`가 rate limit 판단에 쓰는 것과
동일한 `client.api.observations.get_many()` 호출):

```bash
python3 -c "
from hwp_hierarchical_md.cli import _load_dotenv_if_present; _load_dotenv_if_present()
from langfuse import get_client
for o in get_client().api.observations.get_many(name='classify-document-headings', limit=5).data:
    print(o.id, o.start_time, o.user_id)
"
```

### 클라우드 백엔드 TPM 대응 — 실패하면 자동으로 배치를 줄여 재시도(2026-08-27)

OpenAI 호환 클라우드 백엔드(Groq/OpenAI 등)는 응답 자체에 실제 TPM(분당 토큰) 한도를 실어
보낸다 — 대시보드 조회 없이도 매 호출의 응답 헤더(`x-ratelimit-limit-tokens`/
`x-ratelimit-remaining-tokens`)와, 한도를 넘었을 때의 에러 메시지(`"... Limit 8000, Requested
14483 ..."`)로 정확한 수치를 알려준다. `llm_backend.py`가 이 값을 읽어 `RateLimitExceeded`로
구조화하고, `classify_and_merge()`가 그 정확한 비율만큼 배치를 자동으로 줄여 즉시 재시도한다
(제공자별 한도를 미리 추측/하드코딩하지 않고, 매번 그 제공자가 실제로 알려주는 수치를
그대로 씀). 분당 누적 한도 자체가 소진된 경우는 배치를 줄여도 소용없으므로, 제공자가 알려준
`retry-after`만큼 기다린 뒤 같은 크기로 재시도한다.

"요청 자체가 한 번에 너무 큼" vs "누적 한도가 소진됨"은 HTTP 상태 코드가 아니라 파싱한
Limit/Requested 수치로 판단한다 — Groq는 전자를 413으로 구분해 보내지만, OpenAI는 같은
상황도 429로 보낸다(실측 확인). 상태 코드로 나눴다면 OpenAI에서는 이 경우를 오판해 배치를
안 줄이고 똑같은 크기로 계속 실패했을 것 — Groq로 검증했지만 Groq 전용 로직은 아니다.

`--max-candidates-per-call`(CLI)/`max_candidates_per_call`(API)은 이제 고정값이 아니라
**시작 크기**다 — 위 트레이싱으로 배치별 실제 토큰 사용량을 Langfuse 대시보드에서 보고
싶으면 여전히 낮춰서 시작할 수 있지만, 지정 안 해도 실패 시 자동으로 축소되므로 처음부터
정확한 값을 몰라도 된다("LLM 백엔드 검증 메모"의 실측값은 참고용). API 쪽은 서버가 배포
시 고정해둔 백엔드(`HWP2MD_BACKEND`)에 보낼 배치 크기만 요청자가 조절하는 것으로, 위
"인증/rate limit"과 마찬가지로 서버의 LLM 키 자체를 클라이언트가 대신 쓰는 구조는 아니다.
