"""Bronze layer — generate mock trade data.

`env` is read from pipeline configuration (set in resources/pipelines.yml as
`${var.env}`) and stamped on every row, demonstrating the variable flow:
databricks.yml -> configuration -> spark.conf.get -> output data.
"""

from datetime import date

from pyspark import pipelines as dp
from pyspark.sql import functions as F

from libraries.config import get_conf

# Both naming helpers are imported to show the shared `libraries` package is
# available to pipeline code. `label_for_env` is used below; `table_fqn` is
# imported as a ready-to-use helper for building fully-qualified table names
# (handy if you switch from resource-level catalog/schema defaults to explicit
# names) — it is intentionally not called here.
from libraries.naming import label_for_env, table_fqn  # noqa: F401

env = get_conf(spark, "env", "dev")  # noqa: F821 — `spark` is provided by the pipeline runtime


@dp.table(
    name="bronze.trades",
    comment=f"Raw mock trades ({label_for_env(env)})",
)
def bronze_trades():
    rows = [
        ("ACC001", date(2024, 1, 15), "AAPL", 100, 185.50),
        ("ACC001", date(2024, 1, 16), "MSFT", 50, 375.20),
        ("ACC002", date(2024, 1, 15), "GOOGL", 10, 140.80),
        ("ACC002", date(2024, 1, 17), "AAPL", 200, 186.10),
        ("ACC003", date(2024, 1, 15), "AMZN", 30, 178.30),
        ("ACC003", date(2024, 1, 18), "NVDA", 25, 620.50),
        ("ACC004", date(2024, 1, 16), "TSLA", 75, 215.80),
        ("ACC004", date(2024, 1, 19), "MSFT", 60, 376.90),
        ("ACC005", date(2024, 1, 17), "AAPL", 150, 187.40),
        ("ACC005", date(2024, 1, 20), "GOOGL", 20, 141.50),
    ]
    columns = ["account_id", "trade_date", "symbol", "shares", "price"]
    return (
        spark.createDataFrame(rows, columns)  # noqa: F821
        .withColumn("env", F.lit(env))
    )
