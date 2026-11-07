# node:26.13.0-trixie-slim（Docker Hub公式イメージ）のマルチアーキテクチャdigestで固定
FROM node:26.13.0-trixie-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072

WORKDIR /app

# 依存はコンテナ内にだけ導入する（ホストには導入しない）
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev"]
