FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/RONALDOAMD9/ronaldo-umbrel-apps"
LABEL org.opencontainers.image.title="Tofa para Trakt"
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py sync.py live.py index.html ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8787
CMD ["python", "/app/app.py"]
