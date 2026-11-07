# Playwright公式イメージ（v1.64.1-noble）のマルチアーキテクチャdigestで固定
# イメージの版は package.json の @playwright/test と必ず揃える
FROM mcr.microsoft.com/playwright:v1.64.1-noble@sha256:2f05f8a734509a7b3f26a912639488ee08f58a7b292d14d62271bcff9038768f

WORKDIR /e2e

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

CMD ["npx", "playwright", "test"]
