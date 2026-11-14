FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Job is selected at deploy time: python -m src.jobs.<job_name>
CMD ["python", "-m", "src.jobs.daily_eod"]
