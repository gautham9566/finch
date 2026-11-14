"""Apply backward corporate-action (split/bonus) adjustments to raw 1-min OHLCV."""

import duckdb


def compute_cumulative_factors(splits_rows: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    """Return (symbol, ex_date, factor): bars BEFORE ex_date are divided by factor."""
    by_sym: dict[str, list[tuple[str, float]]] = {}
    for sym, ex_date, ratio in splits_rows:
        if ratio > 0:
            by_sym.setdefault(sym, []).append((ex_date, ratio))

    out: list[tuple[str, str, float]] = []
    for sym, events in by_sym.items():
        events.sort(key=lambda x: x[0], reverse=True)
        cum = 1.0
        for ex_date, ratio in events:
            cum *= ratio
            out.append((sym, ex_date, cum))
    return out


def adjust_1min_parquet(
    raw_parquet: str,
    splits_parquet: str,
    out_dir: str,
    con: duckdb.DuckDBPyConnection | None = None,
) -> None:
    """Filter bad bars and split-adjust 1-min OHLCV in one streaming pass (ASOF join).

    Each bar is divided by the cumulative factor of all splits whose ex_date is
    AFTER the bar's date; volume is multiplied by the same factor.
    Output is Parquet partitioned by year.
    """
    con = con or duckdb.connect()
    con.execute(f"""
        COPY (
            WITH factors AS (
                SELECT
                    symbol,
                    CAST(ex_date AS DATE) AS ex_date,
                    EXP(SUM(LN(split_ratio)) OVER (
                        PARTITION BY symbol ORDER BY CAST(ex_date AS DATE) DESC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    )) AS cum_factor
                FROM read_parquet('{splits_parquet}')
                WHERE split_ratio > 0
            ),
            bars AS (
                SELECT
                    symbol,
                    CAST(timestamp_ist AS TIMESTAMP) AS ts,
                    CAST(CAST(timestamp_ist AS TIMESTAMP) AS DATE) AS bar_date,
                    CAST(open AS DOUBLE) AS open,
                    CAST(high AS DOUBLE) AS high,
                    CAST(low AS DOUBLE) AS low,
                    CAST(close AS DOUBLE) AS close,
                    CAST(volume AS BIGINT) AS volume
                FROM read_parquet('{raw_parquet}')
                WHERE open > 0 AND close > 0 AND high >= low AND volume >= 0
            )
            SELECT
                b.symbol,
                b.ts AS timestamp_ist,
                b.open / COALESCE(f.cum_factor, 1.0) AS open,
                b.high / COALESCE(f.cum_factor, 1.0) AS high,
                b.low / COALESCE(f.cum_factor, 1.0) AS low,
                b.close / COALESCE(f.cum_factor, 1.0) AS close,
                CAST(ROUND(b.volume * COALESCE(f.cum_factor, 1.0)) AS BIGINT) AS volume,
                YEAR(b.ts) AS year
            FROM bars b
            ASOF LEFT JOIN factors f
                ON b.symbol = f.symbol AND b.bar_date < f.ex_date
        ) TO '{out_dir}' (FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE 1)
    """)
