"""Gold layer — aggregate enriched trades into a per-account portfolio summary."""

from pyspark import pipelines as dp
from pyspark.sql import functions as F


@dp.materialized_view(
    name="gold.portfolio_summary",
    comment="Per-account portfolio value and trade counts",
)
def gold_portfolio_summary():
    return (
        spark.read.table("silver.trades")  # noqa: F821
        .groupBy("account_id")
        .agg(
            F.round(F.sum("trade_value"), 2).alias("total_portfolio_value"),
            F.count("*").alias("total_trades"),
        )
        .orderBy("account_id")
    )
