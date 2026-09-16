"""Gold layer (reference pipeline) — count securities per sector.

Reads only this pipeline's own bronze table (bronze.securities) — it does NOT
see the trades pipeline's tables, because the reference pipeline's `libraries`
glob is scoped to src/reference/**.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F


@dp.materialized_view(
    name="gold.securities_by_sector",
    comment="Count of securities per sector",
)
def gold_securities_by_sector():
    return (
        spark.read.table("bronze.securities")  # noqa: F821
        .groupBy("sector")
        .agg(F.count("*").alias("security_count"))
        .orderBy(F.col("security_count").desc(), "sector")
    )
