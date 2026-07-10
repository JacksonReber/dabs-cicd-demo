"""Silver layer — clean trades and apply the variable-driven quality filter.

`quality_threshold` is read from pipeline configuration. Different values per
target (dev/staging/prod) change how many rows survive, so deploying to a
different environment visibly changes the output with no code change.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

from libraries.config import get_conf, parse_threshold

threshold = parse_threshold(get_conf(spark, "quality_threshold", "0"))  # noqa: F821


@dp.materialized_view(
    name="silver.trades",
    comment="Enriched trades passing the quality_threshold filter",
)
def silver_trades():
    return (
        spark.read.table("bronze.trades")  # noqa: F821
        .dropna()
        .withColumn("trade_value", F.round(F.col("shares") * F.col("price"), 2))
        .filter(F.col("trade_value") >= F.lit(threshold))
        .withColumn("processed_at", F.current_timestamp())
    )
