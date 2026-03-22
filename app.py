#!/usr/bin/env python3
"""
Vita Santé — Dashboards influenceurs
Hébergé sur Vercel (serverless Flask).

Routes:
  GET /<slug>              → dashboard HTML de l'influenceur
  GET /api/<slug>/data     → données JSON depuis GA4
  GET /static/<filename>   → assets statiques
"""

import importlib
import json
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Dimension, Metric,
    FilterExpression, Filter, OrderBy,
)

app = Flask(__name__, static_folder="static")

# ---------------------------------------------------------------------------
# Client GA4 — credentials depuis variable d'env sur Vercel,
#              ou fichier local en dev.
# ---------------------------------------------------------------------------
def _build_ga4_client():
    creds_json = os.environ.get("GA4_CREDENTIALS_JSON")
    if creds_json:
        from google.oauth2.service_account import Credentials
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )
        return BetaAnalyticsDataClient(credentials=creds)
    # Fallback local
    local_file = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\Garo_\.config\ga4-service-account.json",
    )
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = local_file
    return BetaAnalyticsDataClient()


client = _build_ga4_client()

# ---------------------------------------------------------------------------
# Chargement dynamique des configs influenceurs depuis dashboards/<slug>.py
# ---------------------------------------------------------------------------
def _load_config(slug):
    try:
        mod = importlib.import_module(f"dashboards.{slug}")
        return mod
    except ModuleNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Requêtes GA4
# ---------------------------------------------------------------------------
def _campaign_filter(utm_campaign):
    return FilterExpression(
        filter=Filter(
            field_name="sessionCampaignName",
            string_filter=Filter.StringFilter(value=utm_campaign, case_sensitive=False),
        )
    )


def fetch_kpis(property_id, utm_campaign, start_date, end_date):
    resp = client.run_report(RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="conversions"),
        ],
        dimension_filter=_campaign_filter(utm_campaign),
    ))
    if resp.rows:
        v    = resp.rows[0].metric_values
        sess = int(float(v[0].value))
        conv = int(float(v[2].value))
        return {
            "sessions":    sess,
            "users":       int(float(v[1].value)),
            "conversions": conv,
            "conv_rate":   f"{conv / sess * 100:.1f}%" if sess else "s.o.",
        }
    return {"sessions": 0, "users": 0, "conversions": 0, "conv_rate": "s.o."}


def fetch_timeseries(property_id, utm_campaign, start_date, end_date):
    resp = client.run_report(RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="sessions"), Metric(name="conversions")],
        dimension_filter=_campaign_filter(utm_campaign),
        order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
    ))
    dates, sessions, conversions = [], [], []
    for row in resp.rows:
        d = datetime.strptime(row.dimension_values[0].value, "%Y%m%d")
        dates.append(d.strftime("%d/%m"))
        sessions.append(int(float(row.metric_values[0].value)))
        conversions.append(int(float(row.metric_values[1].value)))
    return {"dates": dates, "sessions": sessions, "conversions": conversions}


def fetch_sources(property_id, utm_campaign, start_date, end_date):
    resp = client.run_report(RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionManualAdContent"),
        ],
        metrics=[Metric(name="sessions"), Metric(name="conversions")],
        dimension_filter=_campaign_filter(utm_campaign),
    ))
    rows = []
    for row in resp.rows:
        source  = row.dimension_values[0].value
        content = row.dimension_values[1].value
        sess    = int(float(row.metric_values[0].value))
        convs   = int(float(row.metric_values[1].value))
        rows.append({
            "label":       f"{source.capitalize()} \u2014 {content}" if content else source.capitalize(),
            "sessions":    sess,
            "conversions": convs,
            "conv_rate":   f"{convs / sess * 100:.1f}%" if sess else "s.o.",
        })
    rows.sort(key=lambda x: x["sessions"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)


@app.route("/api/<slug>/data")
def api_data(slug):
    cfg = _load_config(slug)
    if cfg is None:
        return jsonify({"error": f"Influenceur '{slug}' introuvable"}), 404

    start = request.args.get("start", "")
    end   = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start et end requis"}), 400
    try:
        datetime.strptime(start, "%Y-%m-%d")
        datetime.strptime(end,   "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Format de date invalide (YYYY-MM-DD)"}), 400

    try:
        return jsonify({
            "kpis":      fetch_kpis(cfg.PROPERTY_ID, cfg.UTM_CAMPAIGN, start, end),
            "timeserie": fetch_timeseries(cfg.PROPERTY_ID, cfg.UTM_CAMPAIGN, start, end),
            "sources":   fetch_sources(cfg.PROPERTY_ID, cfg.UTM_CAMPAIGN, start, end),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    return "<p>Vita Santé Dashboards &mdash; <a href='/chloe'>Chloé</a></p>"


@app.route("/<slug>")
def dashboard(slug):
    cfg = _load_config(slug)
    if cfg is None:
        return f"<p>Dashboard '{slug}' introuvable.</p>", 404

    today = datetime.today()
    s = (today - timedelta(days=29)).strftime("%Y-%m-%d")
    e = today.strftime("%Y-%m-%d")
    html = HTML_TEMPLATE.replace("{{SLUG}}", cfg.SLUG)
    html = html.replace("{{DISPLAY_NAME}}", cfg.DISPLAY_NAME)
    html = html.replace("{{UTM_CAMPAIGN}}", cfg.UTM_CAMPAIGN)
    html = html.replace("{{DEFAULT_START}}", s)
    html = html.replace("{{DEFAULT_END}}", e)
    return html


# ---------------------------------------------------------------------------
# Template HTML
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Tableau de bord &mdash; {{DISPLAY_NAME}} x Vita Sant&eacute;</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg:     #0f0f0f;
      --surf:   #181818;
      --surf2:  #202020;
      --border: #2c2c2c;
      --gold:   #ffd100;
      --green:  #4ade80;
      --text:   #efefef;
      --muted:  #777;
      --font:   'Helvetica Neue', Helvetica, Arial, sans-serif;
    }
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding-bottom: 60px;
    }

    header {
      background: var(--surf);
      border-bottom: 3px solid var(--gold);
      padding: 22px 40px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
    }
    .logo img {
      height: 48px;
      width: auto;
    }
    .header-center h1 { font-size: 17px; font-weight: 700; text-align: center; }
    .header-center p  { font-size: 12px; color: var(--muted); text-align: center; margin-top: 2px; }

    .date-bar {
      background: var(--surf2);
      border-bottom: 1px solid var(--border);
      padding: 14px 40px;
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
    }
    .date-bar label {
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .date-bar input[type="date"] {
      background: var(--surf);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 7px 12px;
      color: var(--text);
      font-family: var(--font);
      font-size: 13px;
      cursor: pointer;
      color-scheme: dark;
    }
    .date-bar input[type="date"]:focus { outline: none; border-color: var(--gold); }
    .sep { color: var(--muted); font-size: 13px; }
    .btn-refresh {
      background: var(--gold);
      color: #111;
      border: none;
      border-radius: 6px;
      padding: 8px 20px;
      font-family: var(--font);
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    .btn-refresh:hover   { opacity: .85; }
    .btn-refresh:disabled { opacity: .4; cursor: default; }
    .presets { display: flex; gap: 6px; margin-left: 8px; }
    .preset {
      background: transparent;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 12px;
      color: var(--muted);
      font-family: var(--font);
      font-size: 12px;
      cursor: pointer;
    }
    .preset:hover  { border-color: var(--gold); color: var(--gold); }
    .preset.active { border-color: var(--gold); color: var(--gold); background: rgba(255,209,0,.07); }

    .status-bar { height: 3px; background: transparent; transition: background .3s; }
    .status-bar.loading { background: var(--gold); animation: pulse 1s infinite; }
    @keyframes pulse { 0%,100%{opacity:.4} 50%{opacity:1} }

    .main { max-width: 980px; margin: 0 auto; padding: 32px 24px 0; }

    .kpis {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 14px;
      margin-bottom: 24px;
    }
    @media(max-width:600px) { .kpis { grid-template-columns: 1fr 1fr; } }
    .kpi {
      background: var(--surf);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 22px 18px;
      position: relative;
      overflow: hidden;
    }
    .kpi::after {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 3px;
      background: var(--gold);
    }
    .kpi.green::after { background: var(--green); }
    .kpi-label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); margin-bottom: 10px; }
    .kpi-val   { font-size: 34px; font-weight: 800; line-height: 1; color: var(--text); }
    .kpi.green .kpi-val { color: var(--green); }
    .kpi-sub   { font-size: 11px; color: var(--muted); margin-top: 7px; }

    .card { background: var(--surf); border: 1px solid var(--border); border-radius: 12px; padding: 26px 28px; margin-bottom: 20px; }
    .card-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); margin-bottom: 22px; }
    .chart-wrap { position: relative; height: 210px; }

    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    thead th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); padding: 0 12px 12px; border-bottom: 1px solid var(--border); }
    thead th.num { text-align: right; }
    tbody td { padding: 13px 12px; border-bottom: 1px solid var(--border); color: var(--text); }
    tbody td.num { text-align: right; font-variant-numeric: tabular-nums; }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover td { background: var(--surf2); }
    .empty { text-align: center; color: var(--muted); padding: 28px; }

    footer { text-align: center; color: var(--muted); font-size: 11px; margin-top: 40px; }
    footer strong { color: var(--gold); }
  </style>
</head>
<body>

<header>
  <div class="logo"><img src="/static/logo.png" alt="Vita Sant&eacute;"></div>
  <div class="header-center">
    <h1>Tableau de bord &mdash; {{DISPLAY_NAME}}</h1>
    <p>Performance de tes liens partenaire</p>
  </div>
  <div style="width:120px"></div>
</header>

<div class="date-bar">
  <label>Du</label>
  <input type="date" id="start" value="{{DEFAULT_START}}">
  <span class="sep">&rarr;</span>
  <label>au</label>
  <input type="date" id="end" value="{{DEFAULT_END}}">
  <button class="btn-refresh" id="btn-refresh">Actualiser</button>
  <div class="presets">
    <button class="preset" data-days="7">7 j</button>
    <button class="preset active" data-days="30">30 j</button>
    <button class="preset" data-days="90">90 j</button>
  </div>
</div>
<div class="status-bar" id="status-bar"></div>

<div class="main">
  <div class="kpis">
    <div class="kpi">
      <div class="kpi-label">Visiteurs uniques</div>
      <div class="kpi-val" id="k-users">&mdash;</div>
      <div class="kpi-sub">via tes liens</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Sessions</div>
      <div class="kpi-val" id="k-sessions">&mdash;</div>
      <div class="kpi-sub">visites totales</div>
    </div>
    <div class="kpi green">
      <div class="kpi-label">Achats</div>
      <div class="kpi-val" id="k-conversions">&mdash;</div>
      <div class="kpi-sub" id="k-convrate">taux : &mdash;</div>
    </div>
  </div>

  <div class="card">
    <div class="card-title" id="chart-title">Sessions &amp; achats</div>
    <div class="chart-wrap"><canvas id="chart"></canvas></div>
  </div>

  <div class="card">
    <div class="card-title">R&eacute;partition par source</div>
    <table>
      <thead>
        <tr>
          <th>Source</th>
          <th class="num">Sessions</th>
          <th class="num">Achats</th>
          <th class="num">Taux conv.</th>
        </tr>
      </thead>
      <tbody id="sources-body"></tbody>
    </table>
  </div>
</div>

<footer>
  Donn&eacute;es filtr&eacute;es sur la campagne <strong>{{UTM_CAMPAIGN}}</strong> &middot; GA4 Vita Sant&eacute;
</footer>

<script>
var SLUG = "{{SLUG}}";
var API  = "/api/" + SLUG + "/data";
var chart = null;

function fmtDate(d) {
  return d.toISOString().slice(0, 10);
}

function setLoading(on) {
  document.getElementById('status-bar').className = on ? 'status-bar loading' : 'status-bar';
  document.getElementById('btn-refresh').disabled = on;
}

function clearTable() {
  var tbody = document.getElementById('sources-body');
  while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
  return tbody;
}

function makeEmptyRow(msg, cols) {
  var tr = document.createElement('tr');
  var td = document.createElement('td');
  td.colSpan = cols;
  td.className = 'empty';
  td.textContent = msg;
  tr.appendChild(td);
  return tr;
}

async function loadData() {
  var start = document.getElementById('start').value;
  var end   = document.getElementById('end').value;
  if (!start || !end || start > end) return;

  setLoading(true);
  var tbody = clearTable();
  tbody.appendChild(makeEmptyRow('Chargement...', 4));

  try {
    var res  = await fetch(API + '?start=' + encodeURIComponent(start) + '&end=' + encodeURIComponent(end));
    var data = await res.json();
    if (data.error) throw new Error(data.error);
    renderKPIs(data.kpis);
    renderChart(data.timeserie, start, end);
    renderSources(data.sources);
  } catch (e) {
    clearTable().appendChild(makeEmptyRow('Erreur de chargement', 4));
  } finally {
    setLoading(false);
  }
}

function renderKPIs(k) {
  document.getElementById('k-users').textContent       = k.users.toLocaleString('fr-CA');
  document.getElementById('k-sessions').textContent    = k.sessions.toLocaleString('fr-CA');
  document.getElementById('k-conversions').textContent = k.conversions.toLocaleString('fr-CA');
  document.getElementById('k-convrate').textContent    = 'taux : ' + k.conv_rate;
}

function renderChart(ts, start, end) {
  var d1 = new Date(start), d2 = new Date(end);
  var nb = Math.round((d2 - d1) / 86400000) + 1;
  document.getElementById('chart-title').textContent =
    'Sessions & achats \u2014 ' + nb + ' jour' + (nb > 1 ? 's' : '');

  if (chart) { chart.destroy(); chart = null; }

  chart = new Chart(document.getElementById('chart').getContext('2d'), {
    data: {
      labels: ts.dates,
      datasets: [
        {
          type: 'bar',
          label: 'Sessions',
          data: ts.sessions,
          backgroundColor: 'rgba(255,209,0,0.82)',
          borderRadius: 4,
          borderSkipped: false,
          yAxisID: 'y',
        },
        {
          type: 'line',
          label: 'Achats',
          data: ts.conversions,
          borderColor: '#4ade80',
          backgroundColor: 'rgba(74,222,128,0.08)',
          pointBackgroundColor: '#4ade80',
          pointRadius: ts.dates.length > 60 ? 2 : 4,
          borderWidth: 2,
          tension: 0.35,
          yAxisID: 'y1',
        },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#888', font: { size: 12 } } },
        tooltip: { backgroundColor: '#181818', borderColor: '#2c2c2c', borderWidth: 1, titleColor: '#efefef', bodyColor: '#999' },
      },
      scales: {
        x:  { ticks: { color: '#555', font: { size: 11 }, maxTicksLimit: 16 }, grid: { color: '#1d1d1d' } },
        y:  { position: 'left',  ticks: { color: '#555',    font: { size: 11 } }, grid: { color: '#1d1d1d' }, title: { display: true, text: 'Sessions', color: '#555',    font: { size: 11 } } },
        y1: { position: 'right', ticks: { color: '#4ade80', font: { size: 11 } }, grid: { drawOnChartArea: false }, title: { display: true, text: 'Achats',   color: '#4ade80', font: { size: 11 } }, min: 0 },
      },
    },
  });
}

function renderSources(sources) {
  var tbody = clearTable();
  if (!sources.length) {
    tbody.appendChild(makeEmptyRow('Aucune donn\u00e9e pour cette p\u00e9riode', 4));
    return;
  }
  sources.forEach(function(s) {
    var tr = document.createElement('tr');
    [
      { text: s.label,                               cls: ''    },
      { text: s.sessions.toLocaleString('fr-CA'),    cls: 'num' },
      { text: s.conversions.toLocaleString('fr-CA'), cls: 'num' },
      { text: s.conv_rate,                           cls: 'num' },
    ].forEach(function(c) {
      var td = document.createElement('td');
      td.textContent = c.text;
      if (c.cls) td.className = c.cls;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

document.querySelectorAll('.preset').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var days  = parseInt(btn.dataset.days, 10);
    var end   = new Date();
    var start = new Date();
    start.setDate(end.getDate() - days + 1);
    document.getElementById('start').value = fmtDate(start);
    document.getElementById('end').value   = fmtDate(end);
    document.querySelectorAll('.preset').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    loadData();
  });
});

document.getElementById('btn-refresh').addEventListener('click', function() {
  document.querySelectorAll('.preset').forEach(function(b) { b.classList.remove('active'); });
  loadData();
});

loadData();
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
