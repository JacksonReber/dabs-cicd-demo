from libraries.config import get_conf, parse_threshold


class _FakeConf:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _FakeSpark:
    def __init__(self, values):
        self.conf = _FakeConf(values)


def test_get_conf_returns_value_when_present():
    spark = _FakeSpark({"env": "prod"})
    assert get_conf(spark, "env", "dev") == "prod"


def test_get_conf_returns_default_when_missing():
    spark = _FakeSpark({})
    assert get_conf(spark, "env", "dev") == "dev"


def test_parse_threshold_parses_numeric_string():
    assert parse_threshold("15000") == 15000.0


def test_parse_threshold_falls_back_on_empty_or_bad_input():
    assert parse_threshold("", default=0.0) == 0.0
    assert parse_threshold(None, default=1.5) == 1.5
    assert parse_threshold("not-a-number", default=2.0) == 2.0
