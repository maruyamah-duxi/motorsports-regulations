# JAF 諸規則ビューア / Cloud Run 用イメージ
#
#  1. SPA を Vite でビルド
#  2. content/ から全文検索用 SQLite を生成してイメージに焼き込む
#  3. FastAPI で SPA・規則HTML・検索APIをまとめて配る
#
# ローカル確認:
#   docker build -t jaf-reg . && docker run --rm -p 8080:8080 jaf-reg

# --- 1. フロントエンド ------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else npm install --no-audit --no-fund; fi
COPY tsconfig.json vite.config.ts index.html index.tsx App.tsx ./
COPY components ./components
COPY services ./services
COPY types.ts data.ts data_part1.ts data_part2.ts ./
RUN npm run build

# --- 2. サーバ --------------------------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY server/requirements.txt ./server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

# 検索インデックスの生成は標準ライブラリだけで済む
COPY pipeline/build_index.py ./pipeline/build_index.py
COPY content ./content
RUN python pipeline/build_index.py --content content --out data/search.db

COPY server ./server
COPY --from=web /app/dist ./dist

ENV PORT=8080 APP_ROOT=/app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT}"]
