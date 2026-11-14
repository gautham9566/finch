"""Phase D1/D2: Pull Kaggle 1-min dataset, validate, adjust corporate actions, and write to GCS."""

import json
import os
import subprocess
from pathlib import Path
import duckdb
import kagglehub

BUCKET = os.getenv("FINCH_BUCKET", "gs://finch-514709-lake")
KAGGLE_DATASET = "nikhilkop/nse-750-clean-1min"
LOCAL_DIR = "/tmp/finch_kaggle"


def _load_kaggle_token() -> None:
    """Support new KAGGLE_API_TOKEN format inside kaggle.json."""
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    if not kaggle_json.exists():
        return
    data = json.loads(kaggle_json.read_text())
    if "KAGGLE_API_TOKEN" in data and not os.getenv("KAGGLE_API_TOKEN"):
        token = data["KAGGLE_API_TOKEN"]
        os.environ["KAGGLE_API_TOKEN"] = token
        access_token = kaggle_json.parent / "access_token"
        access_token.write_text(token)
        os.chmod(access_token, 0o600)


def download_kaggle() -> str:
    """Download Kaggle dataset via kagglehub and symlink into LOCAL_DIR (zero extra disk)."""
    _load_kaggle_token()
    os.makedirs(LOCAL_DIR, exist_ok=True)
    cached_path = kagglehub.dataset_download(KAGGLE_DATASET)
    for name in os.listdir(cached_path):
        src = os.path.join(cached_path, name)
        dst = os.path.join(LOCAL_DIR, name)
        if not os.path.exists(dst):
            os.symlink(src, dst)
    return LOCAL_DIR


def upload_raw_to_gcs(local_dir: str) -> None:
    """Copy raw downloaded files to gs://finch-514709-lake/raw/nse_750_1min/."""
    subprocess.run(
        ["gsutil", "-m", "cp", "-r", f"{local_dir}/*", f"{BUCKET}/raw/nse_750_1min/"],
        check=True,
    )


def build_clean_equities(local_parquet: str, out_parquet: str) -> dict:
    """Filter bad OHLCV rows, deduplicate, and export partitioned/clean Parquet."""
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT DISTINCT
                symbol,
                timestamp_ist,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS BIGINT) AS volume,
                YEAR(timestamp_ist) AS year
            FROM read_parquet('{local_parquet}')
            WHERE open > 0 AND high >= low AND close > 0 AND volume >= 0
        ) TO '{out_parquet}' (FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE 1)
    """)
    row = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT symbol) FROM read_parquet('{local_parquet}')").fetchone()
    return {"raw_rows": row[0], "symbols": row[1]}


if __name__ == "__main__":
    data_dir = download_kaggle()
    upload_raw_to_gcs(data_dir)
    stats = build_clean_equities(f"{data_dir}/equities_1min.parquet", "/tmp/finch_clean_equities")
    print("Clean export stats:", stats)
    subprocess.run(
        ["gsutil", "-m", "cp", "-r", "/tmp/finch_clean_equities", f"{BUCKET}/clean/equities_1min/"],
        check=True,
    )
