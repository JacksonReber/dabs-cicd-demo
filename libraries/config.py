"""Configuration helpers shared across pipeline transformation files.

`get_conf` is the single place pipeline code reads values that were passed in
via the pipeline resource's `configuration` block in resources/pipelines.yml.
"""


def get_conf(spark, key, default=None):
    """Read a pipeline configuration value (set in resources/pipelines.yml)."""
    return spark.conf.get(key, default)


def parse_threshold(value, default: float = 0.0) -> float:
    """Parse a configuration string into a float, with a safe fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
