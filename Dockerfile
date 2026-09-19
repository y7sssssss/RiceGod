FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system ricegod \
    && useradd --system --gid ricegod --create-home ricegod

COPY requirements.lock ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement requirements.lock

COPY pyproject.toml README.md LICENSE ./
COPY ricegod ./ricegod
COPY scripts ./scripts
COPY data ./data

RUN chown -R ricegod:ricegod /app
USER ricegod

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "ricegod.main:app", "--host", "0.0.0.0", "--port", "8000"]
