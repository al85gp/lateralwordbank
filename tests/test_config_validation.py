import copy

import pytest
import yaml

from wb_pipeline.config import ConfigError, parse_config
from wb_pipeline.pipeline import REPO_ROOT

VALID = yaml.safe_load((REPO_ROOT / "config" / "pipeline.yml").read_text())


def with_change(change):
    raw = copy.deepcopy(VALID)
    change(raw)
    return raw


def test_config_validation_accepts_the_shipped_config():
    config = parse_config(VALID)
    assert config.countries == ("BRA", "CAN", "DEU", "GBR", "IND", "JPN", "MEX", "USA", "ZAF")
    assert [i.code for i in config.indicators] == ["SP.POP.TOTL", "NY.GDP.MKTP.CD", "NY.GDP.PCAP.CD", "SP.DYN.LE00.IN"]
    assert list(config.years) == list(range(2018, 2025))
    assert config.api.per_page == 50
    assert config.api.validation_attempts == 4


def test_config_validation_adding_a_country_or_indicator_is_one_list_entry():
    raw = with_change(lambda c: (c["countries"].append("FRA"),
                                 c["indicators"].append({"code": "SP.URB.TOTL", "unit": "people", "aggregation": "sum"})))
    config = parse_config(raw)
    assert "FRA" in config.countries
    assert config.indicators[-1].code == "SP.URB.TOTL"


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda c: c["countries"].append("fra"), "ISO3"),
        (lambda c: c["countries"].append("FRAN"), "ISO3"),
        (lambda c: c["countries"].append("BRA"), "duplicate"),
        (lambda c: c.update(countries=[]), "non-empty"),
        (lambda c: c["indicators"].append(dict(c["indicators"][0])), "duplicate"),
        (lambda c: c["indicators"][0].update(aggregation="mean"), "aggregation"),
        (lambda c: c["indicators"][1].update(code="ny.gdp.mktp.cd"), "indicator code"),
        (lambda c: c["indicators"][1].update(unit=""), "unit"),
        (lambda c: c.update(indicators=[i for i in c["indicators"] if i["code"] != "SP.POP.TOTL"]), "population_weighted_mean"),
        (lambda c: c["years"].update(start=2025), "after"),
        (lambda c: c["years"].update(start="2018"), "integer"),
        (lambda c: c["api"].update(validation_attempts=0), "validation_attempts"),
        (lambda c: c["api"].update(per_page=32768), "per_page"),
        (lambda c: c["api"].update(base_url="http://api.worldbank.org/v2"), "https"),
        (lambda c: c.update(contries=["BRA"]), "unknown key"),
    ],
)
def test_config_validation_rejects(change, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(with_change(change))
