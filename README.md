# hwp-hierarchical-md (service/CLI + API 버전)

공개 저장소 [hwp-hierarchical-md-skill](https://github.com/adover134/korean_official_document_parser_skill)의
파이프라인 코드를 pip 설치 가능한 패키지 + CLI + 배포용 API로 재구성한 버전. 파싱/판단 로직 자체는
스킬 저장소와 동일하다 — 여기는 "서비스로 배포해서 쓸 수 있게 만드는" 확장만 다룬다(CLI 폴더 배치
처리, 사전 환경 점검, FastAPI 배포 레이어). 설계/아키텍처 설명은 스킬 저장소의 README/SKILL.md를
참고.

## 스킬 저장소와의 차이

- `run_pipeline.py`가 단일 파일만 처리했던 것을, `hwp2md convert`가 **폴더 입력(배치 처리)**도
  받도록 확장 (`cli.py`).
- kordoc(npx)·Ollama가 준비 안 된 상태에서 처리 도중 알아보기 힘든 subprocess 에러로 죽는 대신,
  시작 전에 `hwp2md doctor`(또는 `convert` 내장 사전 점검)로 분명한 진단 메시지를 낸다.
- `pip install -e .`로 설치 가능한 패키지 구조(`pyproject.toml`, `src/` 레이아웃, `hwp2md` 콘솔
  스크립트 진입점)로 재구성.
- **배포용 HTTP API(`api.py`, FastAPI)** — `POST /v1/convert`에 HWP/HWPX 파일을 올리면 Markdown을
  반환한다. 서버 쪽에서 백엔드를 환경변수로 미리 설정해두므로(`HWP2MD_BACKEND` 등) 클라이언트가
  자기 LLM 키를 들고 올 필요가 없다. `Dockerfile`로 바로 컨테이너 배포 가능.

(LLM 백엔드 추상화(`llm_backend.py`, Ollama + OpenAI 호환)는 스킬 저장소에도 동일하게 들어가
있다 — 두 저장소가 같은 핵심 로직을 공유하고, 이쪽은 배포 편의 기능만 추가한 구조다.)

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
