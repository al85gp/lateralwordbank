"""Streamlit dashboard over the pipeline's four reports (R-ANL-05).

It reads output/*.csv, not the DuckDB warehouse. The CSVs are replaced only after a passing
dbt build, so the dashboard never shows a half-built state, and it never holds the warehouse's
single-writer lock that the next pipeline run needs. Missing values are shown as gaps or as
missing, never as zero.

Run: streamlit run dashboard/app.py   (Docker: docker compose up -d --build dashboard)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR") or Path(__file__).resolve().parent.parent / "output")
REPORTS = ("rpt_missing_or_incomplete", "rpt_yoy_change", "rpt_latest_comparison", "rpt_income_group_by_year")
STATUS_COLORS = {"reported": "#4c78a8", "null_in_source": "#f58518", "not_returned": "#e45756"}
# Path marks break at a missing value instead of joining across it; nothing is drawn as zero.
LINE = {"point": True, "invalid": "break-paths-filter-domains"}
# SI prefixes, with "B" for billions instead of "G".
BIG_NUMBER_AXIS = alt.Axis(format="~s", labelExpr="replace(datum.label, 'G', 'B')")
CHART_HEIGHT = 380
# Thousands separators in tables; changes to two decimals.
NUMBERS = {
    name: st.column_config.NumberColumn(format="localized")
    for name in ("value", "prior_value", "abs_change")
} | {"pct_change": st.column_config.NumberColumn("pct_change (%)", format="%.2f")}


@st.cache_data
def read_report(path: str, modified: float) -> pd.DataFrame:
    """`modified` is part of the cache key, so a new export is read again."""
    return pd.read_csv(path)


def load_reports() -> tuple[dict[str, pd.DataFrame], datetime] | None:
    files = {name: OUTPUT_DIR / f"{name}.csv" for name in REPORTS}
    if not all(path.exists() for path in files.values()):
        return None
    reports = {name: read_report(str(path), path.stat().st_mtime) for name, path in files.items()}
    exported = datetime.fromtimestamp(max(p.stat().st_mtime for p in files.values()), tz=timezone.utc)
    return reports, exported


def fmt(value: float | None, unit: str = "") -> str:
    if value is None or pd.isna(value):
        return "missing"
    for limit, suffix in ((1e12, " T"), (1e9, " B"), (1e6, " M")):
        if abs(value) >= limit:
            return f"{value / limit:,.2f}{suffix}" + (f" {unit}" if unit else "")
    return f"{value:,.2f}" + (f" {unit}" if unit else "")


st.set_page_config(page_title="World Bank indicators", layout="wide")
st.title("World Bank indicators")

loaded = load_reports()
if loaded is None:
    st.info(
        f"No reports in `{OUTPUT_DIR}` yet. Run the pipeline first: "
        "`docker compose up --build` or `python -m wb_pipeline run`."
    )
    st.stop()

reports, exported = loaded
missing = reports["rpt_missing_or_incomplete"]
yoy = reports["rpt_yoy_change"]
latest = reports["rpt_latest_comparison"]
groups = reports["rpt_income_group_by_year"]

st.caption(
    f"From the pipeline's reports in `output/`, exported {exported:%Y-%m-%d %H:%M} UTC. "
    "Source: World Bank API, World Development Indicators. Missing values are never shown as zero."
)

# --- filters ---------------------------------------------------------------------------------

indicators = yoy[["indicator_code", "indicator_name"]].drop_duplicates().sort_values("indicator_name")
names = dict(zip(indicators["indicator_code"], indicators["indicator_name"]))
units = dict(zip(latest["indicator_code"], latest["unit"]))
countries = sorted(yoy["country_name"].dropna().unique())

with st.sidebar:
    st.header("Filters")
    indicator = st.selectbox("Indicator", list(names), format_func=lambda code: names[code])
    chosen = st.multiselect("Countries", countries, default=countries)
    st.caption("The country filter applies to the coverage and year-over-year tabs.")

unit = units.get(indicator, "")
st.subheader(names[indicator])
st.caption(f"`{indicator}` · unit: {unit}")

coverage_tab, yoy_tab, latest_tab, groups_tab = st.tabs(
    ["Coverage", "Year over year", "Latest year", "Income groups"]
)

# --- R-ANL-01: missing or incomplete -------------------------------------------------------------

with coverage_tab:
    requested = len(yoy)
    not_reported = len(missing)
    c1, c2, c3 = st.columns(3)
    c1.metric("Values requested", f"{requested:,}")
    c2.metric("Reported", f"{requested - not_reported:,}")
    c3.metric("Missing", f"{not_reported:,}")

    if not_reported == 0:
        st.success("Every requested country × indicator × year has a reported value.")
    else:
        st.warning(f"{not_reported} requested values are missing. They stay missing in every report.")

    grid = yoy.loc[(yoy["indicator_code"] == indicator) & yoy["country_name"].isin(chosen),
                   ["country_iso3", "country_name", "year"]]  # fmt: skip
    statuses = missing.loc[missing["indicator_code"] == indicator, ["country_iso3", "year", "value_status"]]
    grid = grid.merge(statuses, on=["country_iso3", "year"], how="left")
    grid["value_status"] = grid["value_status"].fillna("reported")
    heatmap = (
        alt.Chart(grid)
        .mark_rect(stroke="white")
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("country_name:N", title=None),
            color=alt.Color(
                "value_status:N",
                title="Status",
                scale=alt.Scale(domain=list(STATUS_COLORS), range=list(STATUS_COLORS.values())),
            ),
            tooltip=["country_name", "year", "value_status"],
        )
        .properties(title=f"Status of each requested value: {names[indicator]}")
    )
    st.altair_chart(heatmap, width="stretch")

    if not_reported:
        st.dataframe(
            missing[["indicator_name", "country_name", "year", "value_status", "years_reported", "years_expected"]],
            hide_index=True,
            width="stretch",
        )

# --- R-ANL-02: year-over-year change ------------------------------------------------------------

with yoy_tab:
    series = yoy[(yoy["indicator_code"] == indicator) & yoy["country_name"].isin(chosen)]
    levels = (
        alt.Chart(series)
        .mark_line(**LINE)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("value:Q", title=unit, axis=BIG_NUMBER_AXIS),
            color=alt.Color("country_name:N", title="Country"),
            tooltip=["country_name", "year", alt.Tooltip("value:Q", format=",.2f")],
        )
        .properties(title="Value", height=CHART_HEIGHT)
    )
    changes = (
        alt.Chart(series)
        .mark_line(**LINE)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("pct_change:Q", title="% change vs prior year"),
            color=alt.Color("country_name:N", title="Country"),
            tooltip=[
                "country_name",
                "year",
                alt.Tooltip("pct_change:Q", format=",.2f"),
                alt.Tooltip("abs_change:Q", format=",.2f"),
                "change_status",
            ],
        )
        .properties(title="Year-over-year change", height=CHART_HEIGHT)
    )
    zero_line = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(strokeDash=[4, 4]).encode(y="y:Q")
    st.altair_chart(levels, width="stretch")
    st.altair_chart(changes + zero_line, width="stretch")

    status_counts = series["change_status"].value_counts().rename_axis("change_status").reset_index(name="rows")
    st.caption(
        "A change is blank, never zero, when it can't be computed. `change_status` says why: "
        "`first_year_in_scope`, `no_current_value`, `no_prior_value` or `prior_is_zero` "
        "(the absolute change is kept, the percentage is not)."
    )
    st.dataframe(status_counts, hide_index=True)
    with st.expander("All rows"):
        st.dataframe(
            series[["country_name", "year", "value", "prior_value", "abs_change", "pct_change", "change_status"]],
            hide_index=True,
            width="stretch",
            column_config=NUMBERS,
        )

# --- R-ANL-03: latest available year ------------------------------------------------------------

with latest_tab:
    ranking = latest[latest["indicator_code"] == indicator].sort_values("value_rank")
    with_value = ranking[ranking["value"].notna()]
    without_value = ranking[ranking["value"].isna()]
    bars = (
        alt.Chart(with_value)
        .mark_bar()
        .encode(
            x=alt.X("value:Q", title=unit, axis=BIG_NUMBER_AXIS),
            y=alt.Y("country_name:N", title=None, sort="-x"),
            tooltip=[
                "country_name",
                "latest_year",
                alt.Tooltip("value:Q", format=",.2f"),
                "value_rank",
                "years_behind",
            ],
        )
        .properties(title="Each country at its latest year with a reported value")
    )
    st.altair_chart(bars, width="stretch")

    behind = with_value[with_value["years_behind"] > 0]
    if behind.empty and not with_value.empty:
        st.caption(f"Every country is compared at {int(with_value['latest_year'].max())}.")
    for row in behind.itertuples():
        st.warning(f"{row.country_name} is shown at {int(row.latest_year)}, {int(row.years_behind)} year(s) behind.")
    if not without_value.empty:
        st.warning("No reported value in scope: " + ", ".join(without_value["country_name"]))

    top = with_value.iloc[0] if not with_value.empty else None
    if top is not None:
        st.metric(f"Highest: {top.country_name} ({int(top.latest_year)})", fmt(top.value, unit))

# --- R-ANL-04: income groups by year ------------------------------------------------------------

with groups_tab:
    by_group = groups[groups["indicator_code"] == indicator]
    aggregation = by_group["aggregation"].iloc[0] if not by_group.empty else ""
    lines = (
        alt.Chart(by_group)
        .mark_line(**LINE)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("value:Q", title=unit, axis=BIG_NUMBER_AXIS),
            color=alt.Color("income_group_name:N", title="Income group"),
            tooltip=[
                "income_group_name",
                "year",
                alt.Tooltip("value:Q", format=",.2f"),
                "countries_reporting",
                "countries_in_group",
            ],
        )
        .properties(title=f"By current income group ({aggregation.replace('_', ' ')})", height=CHART_HEIGHT)
    )
    st.altair_chart(lines, width="stretch")
    st.caption(
        "Income group is the current World Bank classification, applied to every year. "
        "A weighted mean is weighted by the same country-year's population."
    )

    incomplete = by_group[~by_group["is_complete"]]
    if incomplete.empty:
        st.caption("Every country in every group reported in every year.")
    else:
        st.warning(f"{len(incomplete)} group-years are built from only some of their countries.")
    st.dataframe(
        by_group[["income_group_name", "year", "value", "countries_reporting", "countries_in_group", "is_complete"]],
        hide_index=True,
        width="stretch",
        column_config=NUMBERS,
    )
