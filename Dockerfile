# Builds the store, then serves it and the API from one Python process.
FROM node:22.20.0-slim AS store
WORKDIR /src
COPY guard-ui/src guard-ui/src
COPY demo-store/package.json demo-store/package-lock.json demo-store/
RUN cd demo-store && npm ci
COPY demo-store demo-store
RUN cd demo-store && npm run build

FROM python:3.12.5-slim
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app app
COPY --from=store /src/demo-store/dist /srv/store
ENV STORE_DIST=/srv/store \
    DATABASE_PATH=/tmp/payguard.db \
    PORT=8000
# Behind the host's proxy, --proxy-headers gives each visitor's own address to the rate limit.
CMD uvicorn app.asgi:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
