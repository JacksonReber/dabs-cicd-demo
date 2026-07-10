"""Naming helpers shared across pipeline transformation files.

Pure functions (no Spark dependency) so they can be unit-tested locally.
"""

_ENV_LABELS = {
    "dev": "Development",
    "staging": "Staging",
    "prod": "Production",
}


def table_fqn(catalog: str, schema: str, name: str) -> str:
    """Build a fully-qualified Unity Catalog table name."""
    return f"{catalog}.{schema}.{name}"


def label_for_env(env: str) -> str:
    """Map an environment code to a human-readable label."""
    return _ENV_LABELS.get(env, env.title())
