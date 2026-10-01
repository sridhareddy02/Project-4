# Build the React app, then serve it and the API from one Python image.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY reports ./reports
COPY --from=web /web/dist ./frontend/dist
ENV PYTHONPATH=/app/src COPILOT_ROOT=/app
RUN python -m mktg_copilot build
EXPOSE 8000
CMD ["python", "-m", "mktg_copilot", "serve", "--host", "0.0.0.0", "--port", "8000"]
