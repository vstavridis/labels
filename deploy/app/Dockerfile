# VS Production Manager Streamlit app, for any machine with Docker (Linux server, macOS, Windows).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends cups-client fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir ".[ai,google]"
ENV VSPM_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "src/vspm/ui/app.py", "--server.port", "8501", \
     "--server.address", "0.0.0.0", "--server.headless", "true", "--browser.gatherUsageStats", "false"]
