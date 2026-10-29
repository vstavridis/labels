FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[registry]"
ENV VSPM_REGISTRY_HOST=0.0.0.0 VSPM_REGISTRY_PORT=8765
EXPOSE 8765
CMD ["vspm-registry"]
