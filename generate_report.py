"""
GainARK OntoLeap — HTML Report Generator
Usage:
    python generate_report.py [fixture_path] [--ledger ledger_path]

Defaults:
    fixture_path  = fixtures/ordwaylabs_com_fixture.json
    ledger_path   = truth_ledger/history.jsonl

Outputs:
    report_<brand>.html  (self-contained, open in any browser)
"""

import json
import os
import sys
import re
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {sys.argv[i][2:]: sys.argv[i+1] for i in range(1, len(sys.argv)-1) if sys.argv[i].startswith("--")}

fixture_path = args[0] if args else os.path.join(BASE, "fixtures", "ordwaylabs_com_fixture.json")
ledger_path  = flags.get("ledger", os.path.join(BASE, "truth_ledger", "history.jsonl"))

if not os.path.exists(fixture_path):
    print(f"Fixture not found: {fixture_path}")
    sys.exit(1)

with open(fixture_path, encoding="utf-8") as f:
    data = json.load(f)

# ── History ───────────────────────────────────────────────────────────────────
history = []
if os.path.exists(ledger_path):
    brand_key = data.get("brand_name", "").lower()
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                if brand_key in row.get("brand", "").lower():
                    history.append(row)
            except Exception:
                pass

# ── Data extraction ────────────────────────────────────────────────────────────
brand        = data.get("brand_name", "Unknown")
mgi          = float(data.get("marketing_grounding_index", 0))
total_claims = data.get("total_marketing_claims", 0)
total_tech   = data.get("total_technical_capabilities", 0)
verified     = data.get("verified_claims_count", 0)
unbacked     = data.get("unbacked_claims_count", 0)
hidden       = data.get("hidden_capabilities_count", 0)
summary      = data.get("executive_summary", "")
drift_alerts = data.get("drift_alerts", [])
rdf_turtle   = (data.get("rdf_turtle") or "")[:3000]

# Verified triples
verified_triples = []
for vt in data.get("verified_triples", []):
    if isinstance(vt, dict):
        verified_triples.append(f"{vt.get('object','')} ({vt.get('predicate','')})")
    elif isinstance(vt, str):
        verified_triples.append(vt)

# Hidden capabilities
hidden_caps = []
for hc in data.get("hidden_capabilities", []):
    if isinstance(hc, dict):
        hidden_caps.append(hc.get("object", str(hc)))
    elif isinstance(hc, str):
        hidden_caps.append(hc)

# ── Alert severity parsing ────────────────────────────────────────────────────
SREV = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
SCOL = {
    "CRITICAL": ("#FF4757", "#2D0A0A"),
    "HIGH":     ("#F59E0B", "#2A1A00"),
    "MEDIUM":   ("#FBBF24", "#231B00"),
    "LOW":      ("#94A3B8", "#131C28"),
}
LIGHT_SCOL = {
    "CRITICAL": ("#DC2626", "#FFF0F0"),
    "HIGH":     ("#D97706", "#FFFBEB"),
    "MEDIUM":   ("#CA8A04", "#FEFCE8"),
    "LOW":      ("#64748B", "#F1F5F9"),
}

def parse_alert(a):
    m = re.match(r"\[(\w+)\]\s*(.*)", a)
    if m:
        return m.group(1).upper(), m.group(2).strip()
    return "LOW", a

alerts_parsed = [parse_alert(a) for a in drift_alerts]
alerts_parsed.sort(key=lambda x: SREV.get(x[0], 9))

# ── MGI arc SVG ──────────────────────────────────────────────────────────────
def arc_path(cx, cy, r, start_deg, end_deg):
    import math
    s = math.radians(start_deg); e = math.radians(end_deg)
    x1,y1 = cx+r*math.cos(s), cy+r*math.sin(s)
    x2,y2 = cx+r*math.cos(e), cy+r*math.sin(e)
    laf = 1 if (end_deg-start_deg) > 180 else 0
    return f"M {x1:.2f} {y1:.2f} A {r} {r} 0 {laf} 1 {x2:.2f} {y2:.2f}"

# Arc from 210° to 330° (210° spread). Value maps 0→100 to 210→330
start_a = 210
total_a = 120
fill_a  = start_a + (mgi / 100) * total_a

track = arc_path(100, 100, 72, start_a, start_a + total_a)
value = arc_path(100, 100, 72, start_a, fill_a) if mgi > 0 else ""

# Color for MGI fill
if mgi >= 70:   arc_color = "#00C9A7"
elif mgi >= 40: arc_color = "#F59E0B"
else:           arc_color = "#EF4444"

# ── History sparkline ─────────────────────────────────────────────────────────
spark_points = ""
if len(history) >= 2:
    vals = [h.get("grounding_index", 0) for h in history[-12:]]
    n = len(vals)
    w, h_sp = 200, 40
    pts = " ".join(
        f"{i*(w/(n-1)):.1f},{h_sp - (v/100)*h_sp:.1f}"
        for i, v in enumerate(vals)
    )
    spark_points = pts

# ── Alert rows HTML ───────────────────────────────────────────────────────────
def alert_rows():
    if not alerts_parsed:
        return '<p class="no-alerts">No drift alerts — all verifiable marketing claims are backed by technical evidence.</p>'
    rows = []
    for sev, text in alerts_parsed:
        dark_bg, dark_border = SCOL.get(sev, SCOL["LOW"])
        light_bg, light_border = LIGHT_SCOL.get(sev, LIGHT_SCOL["LOW"])
        rows.append(f"""
        <div class="alert-row sev-{sev.lower()}"
             style="--alert-dark:{dark_bg};--alert-dark-bg:{dark_border};--alert-light:{light_border};--alert-light-bg:{light_bg};">
          <span class="badge badge-{sev.lower()}">{sev}</span>
          <span class="alert-text">{text[:200]}</span>
        </div>""")
    return "\n".join(rows)

# ── Verified chips HTML ───────────────────────────────────────────────────────
def verified_chips():
    chips = []
    for v in verified_triples[:60]:
        chips.append(f'<span class="chip chip-verified">{v[:60]}</span>')
    if len(verified_triples) > 60:
        chips.append(f'<span class="chip chip-more">+{len(verified_triples)-60} more</span>')
    return "\n".join(chips)

# ── Hidden caps HTML ──────────────────────────────────────────────────────────
def hidden_chips():
    chips = []
    for h in hidden_caps[:80]:
        chips.append(f'<span class="chip chip-hidden">{h[:55]}</span>')
    if len(hidden_caps) > 80:
        chips.append(f'<span class="chip chip-more">+{len(hidden_caps)-80} more</span>')
    return "\n".join(chips)

# ── Generate HTML ─────────────────────────────────────────────────────────────
generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{brand} — OntoLeap Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=Playfair+Display:wght@700;900&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
/* ── Tokens ── */
:root {{
  --bg:         #F4F6FA;
  --surface:    #FFFFFF;
  --surface-2:  #F0F3F8;
  --border:     #E2E8F0;
  --text:       #0F1C2E;
  --muted:      #64748B;
  --verified:   #059669;
  --verified-bg:#ECFDF5;
  --hidden:     #6366F1;
  --hidden-bg:  #EEF2FF;
  --arc:        {arc_color};
  --tag-cr:     #DC2626; --tag-cr-bg:#FFF0F0;
  --tag-hi:     #D97706; --tag-hi-bg:#FFFBEB;
  --tag-me:     #CA8A04; --tag-me-bg:#FEFCE8;
  --tag-lo:     #64748B; --tag-lo-bg:#F1F5F9;
  --mono:       'JetBrains Mono', monospace;
  --body:       'DM Sans', system-ui, sans-serif;
  --display:    'Playfair Display', serif;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:         #0A1220;
    --surface:    #111D2E;
    --surface-2:  #0F1928;
    --border:     #1E3048;
    --text:       #E2EAF4;
    --muted:      #7A93B4;
    --verified:   #10B981;
    --verified-bg:#052E1C;
    --hidden:     #818CF8;
    --hidden-bg:  #1E1B4B;
    --tag-cr:     #F87171; --tag-cr-bg:#2D0808;
    --tag-hi:     #FBB740; --tag-hi-bg:#2A1800;
    --tag-me:     #FCD34D; --tag-me-bg:#221B00;
    --tag-lo:     #94A3B8; --tag-lo-bg:#131C28;
  }}
}}
:root[data-theme="dark"] {{
    --bg:         #0A1220;
    --surface:    #111D2E;
    --surface-2:  #0F1928;
    --border:     #1E3048;
    --text:       #E2EAF4;
    --muted:      #7A93B4;
    --verified:   #10B981;
    --verified-bg:#052E1C;
    --hidden:     #818CF8;
    --hidden-bg:  #1E1B4B;
    --tag-cr:     #F87171; --tag-cr-bg:#2D0808;
    --tag-hi:     #FBB740; --tag-hi-bg:#2A1800;
    --tag-me:     #FCD34D; --tag-me-bg:#221B00;
    --tag-lo:     #94A3B8; --tag-lo-bg:#131C28;
}}

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: var(--body);
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  font-size: 15px;
}}

/* ── Layout ── */
.page {{ max-width: 1060px; margin: 0 auto; padding: 40px 24px 80px; }}

/* ── Header ── */
.header {{
  display: flex; align-items: flex-start;
  justify-content: space-between; flex-wrap: wrap;
  gap: 32px; margin-bottom: 48px;
  padding-bottom: 32px;
  border-bottom: 1px solid var(--border);
}}
.header-left {{ flex: 1; min-width: 260px; }}
.eyebrow {{
  font-size: 11px; font-weight: 600; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted);
  margin-bottom: 8px;
}}
.brand-name {{
  font-family: var(--display);
  font-size: clamp(28px, 5vw, 42px);
  font-weight: 900; line-height: 1.1;
  color: var(--text); margin-bottom: 12px;
  text-wrap: balance;
}}
.summary-text {{
  font-size: 14px; color: var(--muted); max-width: 480px; line-height: 1.7;
}}
.generated {{ font-size: 12px; color: var(--muted); margin-top: 12px; }}

/* ── MGI Gauge ── */
.gauge-wrap {{ text-align: center; flex-shrink: 0; }}
.gauge-svg {{ overflow: visible; }}
.gauge-track {{ fill: none; stroke: var(--border); stroke-width: 10; stroke-linecap: round; }}
.gauge-fill  {{ fill: none; stroke: var(--arc);    stroke-width: 10; stroke-linecap: round; }}
.gauge-value {{
  font-family: var(--display); font-weight: 900;
  font-size: 38px; fill: var(--text); dominant-baseline: middle; text-anchor: middle;
}}
.gauge-label {{
  font-family: var(--body); font-size: 11px; letter-spacing: .1em;
  text-transform: uppercase; fill: var(--muted); dominant-baseline: middle; text-anchor: middle;
}}

/* ── Stat strip ── */
.stats {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px; margin-bottom: 40px;
}}
.stat {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px;
}}
.stat-num {{
  font-family: var(--display); font-size: 30px; font-weight: 700;
  line-height: 1; font-variant-numeric: tabular-nums;
}}
.stat-label {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
.stat.s-verified .stat-num {{ color: var(--verified); }}
.stat.s-hidden   .stat-num {{ color: var(--hidden); }}

/* ── Section ── */
.section {{ margin-bottom: 40px; }}
.section-head {{
  display: flex; align-items: baseline; gap: 12px;
  margin-bottom: 16px;
}}
.section-title {{
  font-size: 13px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted);
}}
.section-count {{
  font-size: 12px; color: var(--muted);
  background: var(--surface-2); border-radius: 20px; padding: 1px 8px;
}}

/* ── Alerts ── */
.alerts-list {{ display: flex; flex-direction: column; gap: 8px; }}
.alert-row {{
  display: flex; align-items: flex-start; gap: 12px;
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--alert-dark);
  border-radius: 8px; padding: 12px 14px;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) .alert-row {{ background: var(--alert-dark-bg); border-color: var(--alert-dark); border-left-color: var(--alert-dark); }}
}}
:root[data-theme="dark"] .alert-row {{ background: var(--alert-dark-bg); border-color: var(--alert-dark); border-left-color: var(--alert-dark); }}
.alert-text {{ font-size: 13.5px; line-height: 1.5; color: var(--text); }}
.no-alerts {{
  color: var(--verified); font-weight: 500; font-size: 14px;
  background: var(--verified-bg); border-radius: 8px; padding: 16px 18px;
}}

/* ── Badges ── */
.badge {{
  display: inline-block; font-size: 10px; font-weight: 700;
  letter-spacing: .08em; padding: 2px 8px; border-radius: 4px;
  text-transform: uppercase; white-space: nowrap; flex-shrink: 0;
  margin-top: 2px;
}}
.badge-critical {{ background: var(--tag-cr-bg); color: var(--tag-cr); }}
.badge-high     {{ background: var(--tag-hi-bg); color: var(--tag-hi); }}
.badge-medium   {{ background: var(--tag-me-bg); color: var(--tag-me); }}
.badge-low      {{ background: var(--tag-lo-bg); color: var(--tag-lo); }}

/* ── Chips ── */
.chip-grid {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.chip {{
  font-size: 12.5px; padding: 5px 11px; border-radius: 6px;
  line-height: 1.4;
}}
.chip-verified {{ background: var(--verified-bg); color: var(--verified); }}
.chip-hidden   {{ background: var(--hidden-bg);   color: var(--hidden); }}
.chip-more     {{
  background: var(--surface-2); color: var(--muted);
  border: 1px solid var(--border);
}}

/* ── RDF block ── */
.rdf-block {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px; padding: 20px;
  overflow-x: auto;
}}
.rdf-block pre {{
  font-family: var(--mono); font-size: 12px; line-height: 1.7;
  color: var(--muted); white-space: pre-wrap; word-break: break-word;
}}

/* ── Sparkline ── */
.spark-wrap {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 20px;
}}
.spark-label {{ font-size: 11px; color: var(--muted); margin-bottom: 12px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }}

/* ── Two-col grid ── */
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
@media (max-width: 700px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 24px;
}}
</style>
</head>
<body>
<div class="page">

  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <div class="eyebrow">GainARK OntoLeap · Product Truth Audit</div>
      <h1 class="brand-name">{brand}</h1>
      <p class="summary-text">{summary[:300]}</p>
      <p class="generated">Generated {generated_at}</p>
    </div>
    <div class="gauge-wrap">
      <svg class="gauge-svg" width="200" height="140" viewBox="20 30 160 120">
        <path class="gauge-track" d="{track}" />
        {"<path class='gauge-fill' d='" + value + "' />" if value else ""}
        <text class="gauge-value" x="100" y="105">{mgi:.1f}<tspan font-size="18">%</tspan></text>
        <text class="gauge-label" x="100" y="122">MGI Score</text>
      </svg>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats">
    <div class="stat">
      <div class="stat-num">{total_claims}</div>
      <div class="stat-label">Marketing Claims</div>
    </div>
    <div class="stat">
      <div class="stat-num">{total_tech}</div>
      <div class="stat-label">Tech Capabilities</div>
    </div>
    <div class="stat s-verified">
      <div class="stat-num">{verified}</div>
      <div class="stat-label">Verified Claims</div>
    </div>
    <div class="stat">
      <div class="stat-num">{unbacked}</div>
      <div class="stat-label">Unbacked Claims</div>
    </div>
    <div class="stat s-hidden">
      <div class="stat-num">{hidden}</div>
      <div class="stat-label">Hidden Capabilities</div>
    </div>
    <div class="stat">
      <div class="stat-num">{len(drift_alerts)}</div>
      <div class="stat-label">Drift Alerts</div>
    </div>
  </div>

  <!-- Drift Alerts -->
  <div class="section">
    <div class="section-head">
      <span class="section-title">Drift Alerts</span>
      <span class="section-count">{len(drift_alerts)}</span>
    </div>
    <div class="alerts-list">
      {alert_rows()}
    </div>
  </div>

  <!-- Verified + Hidden -->
  <div class="two-col">
    <div class="card section">
      <div class="section-head">
        <span class="section-title">Verified Claims</span>
        <span class="section-count">{len(verified_triples)}</span>
      </div>
      <div class="chip-grid">
        {verified_chips()}
      </div>
    </div>
    <div class="card section">
      <div class="section-head">
        <span class="section-title">Hidden Capabilities</span>
        <span class="section-count">{len(hidden_caps)}</span>
      </div>
      <div class="chip-grid">
        {hidden_chips()}
      </div>
    </div>
  </div>

  <!-- Progression -->
  {"<div class='section spark-wrap'><div class='spark-label'>MGI Progression (" + str(len(history)) + " audits)</div><svg width='100%' height='50' viewBox='0 0 200 40' preserveAspectRatio='none'><polyline points='" + spark_points + "' fill='none' stroke='" + arc_color + "' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg></div>" if spark_points else ""}

  <!-- RDF Turtle -->
  {"<div class='section'><div class='section-head'><span class='section-title'>Knowledge Graph (RDF Turtle excerpt)</span></div><div class='rdf-block'><pre>" + rdf_turtle.replace("<","&lt;").replace(">","&gt;") + "...</pre></div></div>" if rdf_turtle else ""}

</div>
</body>
</html>"""

# ── Save ─────────────────────────────────────────────────────────────────────
slug = re.sub(r"[^a-z0-9]+", "_", brand.lower()).strip("_")
out_path = os.path.join(BASE, f"report_{slug}.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Report saved: {out_path}")
print(f"Open in browser: file:///{out_path.replace(os.sep, '/')}")
