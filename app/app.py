"""Conduct risk dashboard. Run: python app/app.py  (or gunicorn app.app:server)

Three views:
  Watchlist        which firms need review now and why, in plain English
  Firm             one firm's FCA returns and ombudsman decisions side by side
  Themes and model Consumer Duty outcomes, theme trends and the triage model's performance

Filtering the watchlist and copying the summary run in the browser through clientside
JavaScript callbacks (assets/clientside.js), so they respond without a server round trip.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import ClientsideFunction, Dash, Input, Output, State, dash_table, dcc, html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MARTS, REPORTS = ROOT / "data" / "marts", ROOT / "reports"

INK, MUTED, RULE, ACCENT = "#17202A", "#56616E", "#D5DBE1", "#0B5D66"
RAG = {"red": "#B42318", "amber": "#A15C07", "green": "#2F6B3A"}
SERIES = ["#0B5D66", "#7A4E9E", "#B5651D", "#3C6E9F", "#8A8F2E", "#A33B5B", "#4F7C62", "#6B6B6B"]


def load(name: str, dates: tuple[str, ...] = ()) -> pd.DataFrame:
    for ext, reader in ((".parquet", pd.read_parquet), (".csv.gz", pd.read_csv)):
        path = MARTS / f"{name}{ext}"
        if path.exists():
            df = reader(path)
            for col in dates:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col])
            return df
    return pd.DataFrame()


def load_json(name: str) -> dict:
    path = REPORTS / name
    return json.loads(path.read_text()) if path.exists() else {}


watch = load("watchlist")
fca = load("fca_firm_period", ("period_start",))
fos_firm = load("fos_firm_theme_month", ("decision_month",))
market = load("fos_market_month", ("decision_month",))
coverage = load("fos_month_coverage", ("decision_month", "first_decision_date", "last_decision_date"))
firms = load("firm_dim")
curve = load("model_pr_curve")
metrics = load_json("model_metrics.json")
ew_summary = load_json("early_warning_summary.json")
HAS_DATA = not market.empty


def style(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Source Sans 3, Segoe UI, sans-serif", size=13, color=INK),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None), hovermode="x unified",
        colorway=SERIES,
    )
    fig.update_xaxes(showgrid=False, linecolor=RULE, ticks="outside", tickcolor=RULE)
    fig.update_yaxes(gridcolor="#E7EBEF", zeroline=False)
    return fig


def headline() -> tuple[str, str, str]:
    if watch.empty:
        return "No firms are outside their normal range.", "", "No firms flagged."
    red_keys = set(watch.loc[watch["rag"] == "red", "firm_key"])
    red, amber = len(red_keys), len(set(watch.loc[watch["rag"] == "amber", "firm_key"]) - red_keys)
    big = f"{red} {'firm needs' if red == 1 else 'firms need'} review and {amber} need watching"
    if ew_summary.get("fos_status") == "ok":
        sub = (f"Based on ombudsman decisions to {pd.Timestamp(ew_summary.get('fos_month')):%B %Y} "
               f"and FCA returns for {ew_summary.get('fca_period')}.")
    else:
        sub = (f"Based on FCA returns for {ew_summary.get('fca_period')}. The ombudsman-decision test was "
               f"{ew_summary.get('fos_status', 'not run')}.")
    top = watch[watch["rag"] == "red"].head(5)
    detail = "; ".join(f"{r.firm_name} ({r.area}): {r.reason}" for r in top.itertuples())
    return big, sub, f"{big}. {sub} {detail}".strip()


def market_chart() -> go.Figure:
    m = market.groupby("decision_month", as_index=False)[["decisions", "upheld"]].sum()
    m["rate"] = m["upheld"] / m["decisions"]
    # Months still being published (see crm.coverage) are drawn paler so lag doesn't read as a fall.
    partial = set()
    if not coverage.empty:
        from crm.coverage import complete_months
        cov = complete_months(coverage)
        partial = set(cov.loc[~cov["is_complete"], "decision_month"])
    colours = ["#E3E9EE" if d in partial else "#B9C7D3" for d in m["decision_month"]]
    fig = go.Figure()
    fig.add_bar(x=m["decision_month"], y=m["decisions"], name="Decisions published", marker_color=colours,
                customdata=["still being published" if d in partial else "" for d in m["decision_month"]],
                hovertemplate="%{y:,} decisions %{customdata}<extra></extra>")
    fig.add_scatter(x=m["decision_month"], y=m["rate"], name="Share upheld", yaxis="y2",
                    line=dict(color=ACCENT, width=2.5), hovertemplate="%{y:.0%}")
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", tickformat=".0%", rangemode="tozero", showgrid=False))
    return style(fig)


def outcome_chart() -> go.Figure:
    cd = market.groupby("consumer_duty_label", as_index=False)[["decisions", "upheld"]].sum()
    cd["rate"] = cd["upheld"] / cd["decisions"]
    cd = cd.sort_values("decisions")
    fig = go.Figure(go.Bar(
        x=cd["decisions"], y=cd["consumer_duty_label"], orientation="h", marker_color=ACCENT,
        text=[f"{r:.0%} upheld" for r in cd["rate"]], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:,} decisions<extra></extra>"))
    return style(fig, 260)


def theme_heatmap() -> go.Figure:
    recent = market[market["decision_month"] >= market["decision_month"].max() - pd.DateOffset(months=23)]
    pivot = recent.pivot_table(index="theme_label", columns="decision_month", values="decisions", aggfunc="sum").fillna(0)
    pivot = pivot.loc[pivot.sum(axis=1).sort_values().index]
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[f"{c:%b %y}" for c in pivot.columns], y=pivot.index,
        colorscale=[[0, "#F2F5F7"], [0.5, "#6FA3A8"], [1, ACCENT]], colorbar=dict(title="Decisions"),
        hovertemplate="%{y}, %{x}: %{z:,}<extra></extra>"))
    return style(fig, 420)


def pr_chart() -> go.Figure:
    fig = go.Figure()
    if curve.empty or not metrics:
        return style(fig, 300)
    base = metrics["results"]["baseline_base_rate"]["base_rate"]
    fig.add_scatter(x=curve["recall"], y=curve["precision"], name="Triage model", line=dict(color=ACCENT, width=2.5))
    fig.add_scatter(x=[0, 1], y=[base, base], name="Random review", line=dict(color=MUTED, dash="dot"))
    fig.update_xaxes(title="Share of upheld complaints found", tickformat=".0%", range=[0, 1])
    fig.update_yaxes(title="Share of reviewed cases upheld", tickformat=".0%", range=[0, 1])
    return style(fig, 300)


def model_sentence() -> str:
    if not metrics:
        return "Run the model step to see how well the triage score ranks complaints."
    best = metrics["results"][metrics["best_model"]]
    lift = best["precision_at_top_10pct"] / max(best["base_rate"], 1e-9)
    return (f"Reviewing the top 10% of complaints by score finds upheld cases {best['precision_at_top_10pct']:.0%} of the time, "
            f"against {best['base_rate']:.0%} at random: {lift:.1f} times better. "
            f"Tested on {best['n']:,} decisions the model never saw during training.")


def rag_conditional() -> list[dict]:
    return [{"if": {"filter_query": f'{{rag}} = "{k}"', "column_id": "rag"}, "color": v, "fontWeight": 600}
            for k, v in RAG.items()]


TABLE_STYLE = dict(
    style_as_list_view=True,
    style_header={"fontWeight": 600, "backgroundColor": "transparent", "borderBottom": f"2px solid {INK}", "color": INK},
    style_cell={"fontFamily": "Source Sans 3, Segoe UI, sans-serif", "fontSize": 14, "padding": "10px 12px",
                "textAlign": "left", "whiteSpace": "normal", "height": "auto", "border": "none",
                "borderBottom": f"1px solid {RULE}", "backgroundColor": "transparent", "color": INK},
)


def watchlist_view() -> html.Div:
    big, sub, _ = headline()
    rows = watch.to_dict("records")
    return html.Div(className="view", children=[
        html.H2(big, className="hero"),
        html.P(sub, className="hero-sub"),
        html.Div(className="toolbar", children=[
            dcc.Checklist(id="rag-filter", options=[{"label": " Review now", "value": "red"},
                                                     {"label": " Watch", "value": "amber"}],
                          value=["red", "amber"], inline=True, className="rag-filter"),
            html.Button("Copy summary", id="copy-btn", n_clicks=0, className="button"),
            html.Span(id="copy-status", className="status", role="status"),
        ]),
        dcc.Store(id="watchlist-store", data=rows),
        dash_table.DataTable(
            id="watchlist", data=rows, page_size=15, sort_action="native",
            columns=[{"name": "Firm", "id": "firm_name"}, {"name": "Status", "id": "rag"},
                     {"name": "Signal", "id": "source"}, {"name": "Area", "id": "area"}, {"name": "Why", "id": "reason"}],
            style_data_conditional=rag_conditional(), **TABLE_STYLE),
        html.H3("Ombudsman decisions across the market"),
        dcc.Graph(figure=market_chart(), config={"displayModeBar": False}),
    ])


def firm_view() -> html.Div:
    options = (firms.sort_values("firm_name")[["firm_name", "firm_key"]].dropna()
               .rename(columns={"firm_name": "label", "firm_key": "value"}).to_dict("records"))
    default = watch["firm_key"].iloc[0] if not watch.empty else (options[0]["value"] if options else None)
    return html.Div(className="view", children=[
        html.Label("Firm", htmlFor="firm-select", className="field-label"),
        dcc.Dropdown(id="firm-select", options=options, value=default, clearable=False, className="firm-select"),
        html.Div(id="firm-signals"),
        html.Div(className="grid-2", children=[
            html.Div([html.H3("Complaints opened, FCA returns"), dcc.Graph(id="firm-fca", config={"displayModeBar": False})]),
            html.Div([html.H3("Ombudsman decisions by theme"), dcc.Graph(id="firm-fos", config={"displayModeBar": False})]),
        ]),
    ])


def themes_view() -> html.Div:
    explain = metrics.get("explain", {})
    return html.Div(className="view", children=[
        html.H3("Decisions by Consumer Duty outcome"),
        dcc.Graph(figure=outcome_chart(), config={"displayModeBar": False}),
        html.H3("Theme volumes, last 24 months"),
        dcc.Graph(figure=theme_heatmap(), config={"displayModeBar": False}),
        html.H3("Triage model"),
        html.P(model_sentence(), className="lede"),
        dcc.Graph(figure=pr_chart(), config={"displayModeBar": False}),
        html.Div(className="grid-2 terms", children=[
            html.Div([html.H4("Language that raises the score"),
                      html.P(", ".join(explain.get("terms_raising_uphold_likelihood", [])[:15]))]),
            html.Div([html.H4("Language that lowers it"),
                      html.P(", ".join(explain.get("terms_lowering_uphold_likelihood", [])[:15]))]),
        ]),
    ])


app = Dash(__name__, title="Conduct risk monitor", assets_folder=str(Path(__file__).parent / "assets"),
           external_stylesheets=["https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap"])
server = app.server

if not HAS_DATA:
    app.layout = html.Main(className="page", children=[
        html.H1("Conduct risk monitor"),
        html.P("No data yet. Run `make all` to download the data and build the marts, then reload this page.", className="lede"),
    ])
else:
    _, _, summary_text = headline()
    app.layout = html.Main(className="page", children=[
        html.Header(className="masthead", children=[
            html.H1("Conduct risk monitor"),
            html.P("Early warnings from UK ombudsman decisions and FCA complaints returns", className="tagline"),
        ]),
        dcc.Store(id="summary-text", data=summary_text),
        dcc.Tabs(className="tabs", parent_className="tabs-parent", children=[
            dcc.Tab(label="Watchlist", children=watchlist_view(), className="tab", selected_className="tab--selected"),
            dcc.Tab(label="Firm", children=firm_view(), className="tab", selected_className="tab--selected"),
            dcc.Tab(label="Themes and model", children=themes_view(), className="tab", selected_className="tab--selected"),
        ]),
        html.Footer("Sources: Financial Ombudsman Service published decisions; FCA firm-level complaints data "
                    "(Open Government Licence). Published decisions cover escalated complaints only.", className="footer"),
    ])

    app.clientside_callback(ClientsideFunction("crm", "filterWatchlist"),
                            Output("watchlist", "data"), Input("rag-filter", "value"), State("watchlist-store", "data"))
    app.clientside_callback(ClientsideFunction("crm", "copySummary"),
                            Output("copy-status", "children"), Input("copy-btn", "n_clicks"), State("summary-text", "data"))

    @app.callback(Output("firm-fca", "figure"), Output("firm-fos", "figure"), Output("firm-signals", "children"),
                  Input("firm-select", "value"))
    def update_firm(key):
        f = fca[fca["firm_key"] == key].sort_values("period_start") if not fca.empty else pd.DataFrame()
        fig1 = go.Figure()
        for product, g in (f.groupby("product_group") if not f.empty else []):
            fig1.add_scatter(x=g["period"], y=g["opened"], name=product.replace("_", " ").capitalize(), mode="lines+markers")
        if f.empty:
            fig1.add_annotation(text="This firm isn't in the FCA returns. It may report fewer than 500 complaints a half-year.",
                                showarrow=False, font=dict(color=MUTED))

        o = fos_firm[fos_firm["firm_key"] == key] if not fos_firm.empty else pd.DataFrame()
        fig2 = go.Figure()
        if not o.empty:
            o = o[o["decision_month"] >= o["decision_month"].max() - pd.DateOffset(months=23)]
            for theme, g in o.groupby("theme_label"):
                fig2.add_bar(x=g["decision_month"], y=g["decisions"], name=theme)
            fig2.update_layout(barmode="stack")
        else:
            fig2.add_annotation(text="No published ombudsman decisions for this firm.", showarrow=False, font=dict(color=MUTED))

        sig = watch[watch["firm_key"] == key]
        signals = (html.P("No warning signals this period.", className="lede") if sig.empty else
                   dash_table.DataTable(data=sig.to_dict("records"),
                                        columns=[{"name": "Status", "id": "rag"}, {"name": "Signal", "id": "source"},
                                                 {"name": "Area", "id": "area"}, {"name": "Why", "id": "reason"}],
                                        style_data_conditional=rag_conditional(), **TABLE_STYLE))
        return style(fig1), style(fig2), signals


if __name__ == "__main__":
    app.run(debug=False, port=8050)
