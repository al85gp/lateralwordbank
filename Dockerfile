# Runtime image: extract, dbt build, export. Runs as the host user (see docker-compose.yml),
# so everything it writes goes to the mounted data/ and output/ folders or to /tmp.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    WAREHOUSE_PATH=/app/data/warehouse.duckdb \
    PIPELINES_DIR=/app/data/dlt_pipelines \
    OUTPUT_DIR=/app/output \
    DBT_TARGET_PATH=/tmp/dbt/target \
    DBT_LOG_PATH=/tmp/dbt/logs \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY .dlt ./.dlt
COPY config ./config
COPY dbt ./dbt
COPY wb_pipeline ./wb_pipeline

ENTRYPOINT ["python", "-m", "wb_pipeline"]
CMD ["run"]


# Test image: the runtime image plus pytest and the recorded API fixtures.
FROM runtime AS test

COPY requirements-dev.txt requirements-dashboard.txt ./
RUN pip install -r requirements-dev.txt -r requirements-dashboard.txt -c constraints.txt

COPY pytest.ini ./
COPY dashboard ./dashboard
COPY tests ./tests

ENTRYPOINT ["python", "-m", "pytest"]
CMD []


# Dashboard image (R-ANL-05): Streamlit over output/*.csv, mounted read-only. It needs none of the
# pipeline's packages, so it starts from the base image rather than from runtime.
FROM python:3.12-slim AS dashboard

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    OUTPUT_DIR=/app/output \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_CLIENT_TOOLBAR_MODE=viewer

WORKDIR /app

COPY requirements-dashboard.txt constraints.txt ./
RUN pip install -r requirements-dashboard.txt -c constraints.txt

COPY dashboard ./dashboard

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3)"

ENTRYPOINT ["streamlit", "run", "dashboard/app.py"]
