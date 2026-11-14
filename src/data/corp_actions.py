"""Apply backward corporate-action (split/bonus) adjustments to raw 1-min OHLCV."""

import duckdb


def compute_cumulative_factors(splits_rows: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    """Return (symbol, ex_date, factor) where bars BEFORE ex_date divide by factor."""
    # Sort descending by ex_date per symbol so cumulative product works backward in time
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


def adjust_1min_parquet(raw_parquet: str, splits_parquet: str, out_parquet: str) -> None:
    """Adjust unadjusted 1-min OHLCV prices & volume using DuckDB (streaming)."""
    con = duckdb.connect()
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
            joined AS (
                SELECT
                    p.symbol,
                    p.timestamp_ist,
                    p.open, p.high, p.low, p.close, p.volume,
                    COALESCE((
                        SELECT f.cum_factor
                        FROM factors f
                        WHERE f.symbol = p.symbol
                          AND CAST(p.timestamp_ist AS DATE) < f.ex_date
                        ORDER BY f.ex_date ASC
                        LIMIT 1
                    ), 1.0) AS adj_factor
                FROM read_parquet('{raw_parquet}') p
            )
            SELECT
                symbol,
                timestamp_ist,
                open / adj_factor AS open,
                high / adj_factor AS high,
                low / adj_factor AS low,
                close / adj_factor AS close,
                CAST(ROUND(volume * adj_factor) AS BIGINT) AS volume,
                YEAR(timestamp_ist) AS year
            FROM joined
        ) TO '{out_parquet}' (FORMAT PARQUET, PARTITION_BY (year), OVERWRITE_OR_IGNORE 1)
    """)
