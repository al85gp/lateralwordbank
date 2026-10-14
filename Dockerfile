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

COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt -c constraints.txt

COPY pytest.ini ./
COPY tests ./tests

ENTRYPOINT ["python", "-m", "pytest"]
CMD []
