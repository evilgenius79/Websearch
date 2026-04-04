FROM python:3.11-slim

# Build deps for lxml (needs libxml2/libxslt headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# Gunicorn + gevent is required for SSE streaming.
# Waitress buffers full responses and breaks Server-Sent Events.
# -w 1: single worker — gevent handles concurrency via greenlets
# -k gevent: async worker class
# --worker-connections 1000: max concurrent greenlets
# --timeout 300: long timeout for slow searches
CMD ["gunicorn", "-w", "1", "-k", "gevent", \
     "--worker-connections", "1000", \
     "--timeout", "300", \
     "--bind", "0.0.0.0:5000", \
     "app:app"]
