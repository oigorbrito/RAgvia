FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RPY_MIGRATIONS_DIR=/app/sql

WORKDIR /app

# Apply the current Debian security updates before Python dependencies are installed.
# The resulting release identity is the immutable image digest produced and scanned
# by trusted CI; deployment never rebuilds on the target host.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY requirements ./requirements
COPY app ./app
RUN python -m pip install pip==26.2.1 setuptools==80.9.0 \
    && python -m pip install --constraint requirements/constraints.txt '.[observability,ocr]' \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
              /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
              /usr/local/lib/python3.12/site-packages/setuptools \
              /usr/local/lib/python3.12/site-packages/setuptools-*.dist-info \
              /usr/local/lib/python3.12/site-packages/_distutils_hack \
              /usr/local/lib/python3.12/site-packages/distutils-precedence.pth \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12

COPY data ./data
COPY sql ./sql
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 rpy && chown -R rpy:rpy /app
USER rpy

EXPOSE 8000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
