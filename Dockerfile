# 배포용 이미지 — kordoc 실행에 필요한 Node.js(npx)와 API 서버 실행에 필요한 Python을 함께 담는다.
# Ollama는 이 이미지 안에 넣지 않는다 — GPU가 필요한 별도 컴포넌트이므로, 배포 시
# HWP2MD_BACKEND=openai|groq|gemini로 클라우드 LLM을 쓰거나, Ollama를 별도 컨테이너/호스트로
# 붙이고 HWP2MD_HOST로 가리키는 걸 전제로 한다.

FROM python:3.12-slim

# Node.js 20 설치 (kordoc을 npx로 실행하는 데 필요)
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[api,dotenv,tracing]"

# kordoc을 미리 한 번 받아둬서(콜드스타트 시 npx 다운로드 지연 방지) 첫 요청부터 빠르게
RUN npx --yes kordoc@4.9.0 --version || true

EXPOSE 8000
ENV HWP2MD_BACKEND=ollama
CMD ["uvicorn", "hwp_hierarchical_md.api:app", "--host", "0.0.0.0", "--port", "8000"]
