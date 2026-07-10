from libraries.naming import table_fqn, label_for_env


def test_table_fqn_joins_three_parts():
    assert table_fqn("dabs_cicd_dev_catalog", "dabs_cicd_dev", "bronze_trades") == \
        "dabs_cicd_dev_catalog.dabs_cicd_dev.bronze_trades"


def test_label_for_env_known_values():
    assert label_for_env("dev") == "Development"
    assert label_for_env("staging") == "Staging"
    assert label_for_env("prod") == "Production"


def test_label_for_env_unknown_falls_back_to_titlecase():
    assert label_for_env("qa") == "Qa"
