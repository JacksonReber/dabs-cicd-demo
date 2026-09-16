"""Bronze layer (reference pipeline) — mock securities reference data.

A SECOND pipeline in the same bundle. It shares the exact same `libraries/`
package as the trades pipeline — `get_conf` (read pipeline configuration) and
`label_for_env` (human-readable env label) are imported from the single copy
at the bundle root, resolved via the pipeline's `root_path: ..`. This is the
whole point of the restructure: one shared library, many pipelines.

Unlike the trades pipeline, this one reads only `env` from configuration — it
has no quality filter, so it never touches `quality_threshold`.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

from libraries.config import get_conf
from libraries.naming import label_for_env

env = get_conf(spark, "env", "dev")  # noqa: F821 — `spark` is provided by the pipeline runtime


@dp.table(
    name="bronze.securities",
    comment=f"Securities reference data ({label_for_env(env)})",
)
def bronze_securities():
    rows = [
        ("AAPL", "Apple Inc.", "Technology", "NASDAQ"),
        ("MSFT", "Microsoft Corp.", "Technology", "NASDAQ"),
        ("GOOGL", "Alphabet Inc.", "Communication Services", "NASDAQ"),
        ("AMZN", "Amazon.com Inc.", "Consumer Discretionary", "NASDAQ"),
        ("NVDA", "NVIDIA Corp.", "Technology", "NASDAQ"),
        ("TSLA", "Tesla Inc.", "Consumer Discretionary", "NASDAQ"),
    ]
    columns = ["symbol", "company_name", "sector", "exchange"]
    return (
        spark.createDataFrame(rows, columns)  # noqa: F821
        .withColumn("env", F.lit(env))
    )
