# syntax=docker/dockerfile:1
# H-6(ADR-2026-09-09-B) -- frontend 컨테이너. 빌드 컨텍스트는 저장소 루트(compose.prod.yml
# 참조) -- 워크스페이스(frontend/packages/*)가 frontend/apps/web과 함께 있어야 npm ci가
# 버전을 맞출 수 있다.

FROM node:22-slim AS builder
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY frontend/packages ./frontend/packages
COPY frontend/apps ./frontend/apps

RUN cd frontend && npm ci && npm run build:web

# nginx-unprivileged: 이미 비루트(uid 101)로 8080을 리슨하도록 만들어진 이미지 --
# 표준 nginx를 비루트로 재구성하는 대신 이걸 쓴다(불변식을 스스로 흩지 않는 쪽).
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/frontend/apps/web/dist /usr/share/nginx/html

EXPOSE 8080
