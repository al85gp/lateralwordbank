"""The Streamlit dashboard renders every report without a browser (streamlit.testing.v1.AppTest)."""

import shutil

import pandas as pd
import pytest
import requests_mock

from conftest import FakeWorldBank
from wb_pipeline.config import load_config
from wb_pipeline.export import REPORTS
from wb_pipeline.pipeline import REPO_ROOT, Paths, run

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP = str(REPO_ROOT / "dashboard" / "app.py")


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    """The four CSVs from a real pipeline run on the recorded API responses."""
    tmp = tmp_path_factory.mktemp("pipeline")
    env = pytest.MonkeyPatch()
    env.setenv("RUNTIME__REQUEST_BACKOFF_FACTOR", "0")
    env.setenv("WAREHOUSE_PATH", str(tmp / "warehouse.duckdb"))
    paths = Paths(
        warehouse=tmp / "warehouse.duckdb",
        output_dir=tmp / "output",
        pipelines_dir=tmp / "dlt_pipelines",
        dbt_project_dir=REPO_ROOT / "dbt",
        dbt_target_dir=tmp / "dbt_target",
        dbt_log_dir=tmp / "dbt_logs",
    )
    try:
        with requests_mock.Mocker(case_sensitive=True) as mocker:
            FakeWorldBank(mocker)
            run(load_config(REPO_ROOT / "config" / "pipeline.yml"), paths)
    finally:
        env.undo()
    return paths.output_dir


def render(monkeypatch, output_dir):
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def test_dashboard_renders_the_pipeline_reports(monkeypatch, reports):
    app = render(monkeypatch, reports)

    assert [t.label for t in app.tabs] == ["Coverage", "Year over year", "Latest year", "Income groups"]
    assert [(m.label, m.value) for m in app.metric][:3] == [("Values requested", "252"), ("Reported", "252"), ("Missing", "0")]
    assert app.success[0].value == "Every requested country × indicator × year has a reported value."


@pytest.mark.parametrize("code", ["SP.POP.TOTL", "NY.GDP.MKTP.CD", "NY.GDP.PCAP.CD", "SP.DYN.LE00.IN"])
def test_dashboard_renders_every_indicator(monkeypatch, reports, code):
    app = render(monkeypatch, reports)
    app.selectbox[0].set_value(code).run()
    assert not app.exception
    assert code in app.caption[1].value


def test_dashboard_shows_missing_values_as_missing(monkeypatch, reports, tmp_path):
    for name in REPORTS:
        shutil.copy(reports / f"{name}.csv", tmp_path)
    # Make Brazil's 2023 population missing, as the reports would show it.
    yoy = pd.read_csv(tmp_path / "rpt_yoy_change.csv")
    row = (yoy["indicator_code"] == "SP.POP.TOTL") & (yoy["country_iso3"] == "BRA") & (yoy["year"] == 2023)
    yoy.loc[row, ["value", "abs_change", "pct_change"]] = None
    yoy.loc[row, "change_status"] = "no_current_value"
    yoy.to_csv(tmp_path / "rpt_yoy_change.csv", index=False)
    pd.DataFrame([{
        "indicator_code": "SP.POP.TOTL", "indicator_name": "Population, total", "country_iso3": "BRA",
        "country_name": "Brazil", "year": 2023, "value_status": "null_in_source", "years_reported": 6,
        "years_expected": 7, "request_url": "", "body_sha256": "", "record_index": "",
    }]).to_csv(tmp_path / "rpt_missing_or_incomplete.csv", index=False)  # fmt: skip

    app = render(monkeypatch, tmp_path)
    app.selectbox[0].set_value("SP.POP.TOTL").run()

    assert not app.exception
    assert [(m.label, m.value) for m in app.metric][:3] == [("Values requested", "252"), ("Reported", "251"), ("Missing", "1")]
    assert "1 requested values are missing" in app.warning[0].value
    assert app.dataframe[0].value["value_status"].tolist() == ["null_in_source"]


def test_dashboard_without_reports_says_how_to_create_them(monkeypatch, tmp_path):
    app = render(monkeypatch, tmp_path)
    assert "Run the pipeline first" in app.info[0].value
    assert not app.tabs
