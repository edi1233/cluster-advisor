import asyncio
import json
import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .analyzer import analyze
from .k8s_client import load_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("k8s-ai-ops")

REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "120"))
DEEPDIVE_PATH = os.environ.get("DEEPDIVE_PATH", "/data/deepdive/report.json")

app = FastAPI(title="k8s-ai-ops")

state = {"report": None, "error": None}
core = apps_api = custom = None


@app.on_event("startup")
async def startup():
    global core, apps_api, custom
    core, apps_api, custom = load_client()
    asyncio.create_task(_loop())


async def _loop():
    while True:
        try:
            state["report"] = await asyncio.to_thread(analyze, core, apps_api, custom)
            state["error"] = None
        except Exception as e:  # noqa: BLE001
            log.exception("analysis failed")
            state["error"] = str(e)
        await asyncio.sleep(REFRESH_SECONDS)


def _read_deepdive():
    if not os.path.exists(DEEPDIVE_PATH):
        return None
    try:
        with open(DEEPDIVE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("deepdive read failed: %s", e)
        return None


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/analyze")
async def api_analyze():
    if state["report"] is None:
        state["report"] = await asyncio.to_thread(analyze, core, apps_api, custom)
    return JSONResponse(state["report"])


@app.get("/api/deepdive")
async def api_deepdive():
    return JSONResponse(_read_deepdive() or {})


def _badge(sev: str) -> str:
    color = {"critical": "#dc2626", "warning": "#d97706"}.get(sev, "#6b7280")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px">{sev}</span>'


def _render_html() -> str:
    r = state["report"]
    dd = _read_deepdive()
    if r is None:
        return "<html><body><h1>k8s-ai-ops</h1><p>Collecting initial data…</p></body></html>"

    s = r["summary"]
    rows_node = "".join(
        f"<tr><td>{n['node']}</td><td>{n['condition']}</td><td>{_badge(n['severity'])}</td><td>{n.get('message','')}</td></tr>"
        for n in r["node_issues"]
    ) or "<tr><td colspan=4>No node issues detected</td></tr>"

    rows_pod = "".join(
        f"<tr><td>{p['namespace']}/{p['pod']}</td><td>{p['phase']}</td><td>{p.get('reason','')}</td>"
        f"<td>{p.get('restarts',0)}</td><td>{p.get('node','')}</td><td>{_badge(p['severity'])}</td></tr>"
        for p in r["pod_issues"]
    ) or "<tr><td colspan=6>No pod issues detected</td></tr>"

    rows_rs = "".join(
        f"<tr><td>{x['namespace']}/{x['pod']}</td><td>{x['container']}</td>"
        f"<td>{x['cpu_usage_m']}m / req {x['cpu_request_m']}m / lim {x['cpu_limit_m']}m</td>"
        f"<td>{x['mem_usage_mi']}Mi / req {x['mem_request_mi']}Mi / lim {x['mem_limit_mi']}Mi</td>"
        f"<td>{'; '.join(x['suggestions'])}</td></tr>"
        for x in r["rightsizing"]
    ) or "<tr><td colspan=5>No right-sizing suggestions right now</td></tr>"

    rows_ev = "".join(
        f"<tr><td>{e['reason']}</td><td>{e['count']}</td></tr>" for e in r["event_summary"]
    ) or "<tr><td colspan=2>No warning events</td></tr>"

    rows_orphan = "".join(
        f"<tr><td>{o['namespace']}</td><td>{o['count']}</td></tr>" for o in r["orphaned_terminal_pods"]
    ) or "<tr><td colspan=2>None</td></tr>"

    deepdive_html = "<p>No AI deep-dive report yet. It is written periodically by a scheduled analysis run.</p>"
    if dd:
        deepdive_html = f"<p style='color:#6b7280'>generated {dd.get('generated_at','')}</p><div style='white-space:pre-wrap'>{dd.get('narrative','')}</div>"

    return f"""
<html><head><title>k8s-ai-ops — pxinf cluster advisor</title>
<meta http-equiv="refresh" content="{REFRESH_SECONDS}">
<style>
body {{ font-family: -apple-system, sans-serif; margin: 2rem; background:#0b0f19; color:#e5e7eb; }}
h1,h2 {{ color:#f9fafb; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
td, th {{ border: 1px solid #374151; padding: 6px 10px; text-align: left; font-size: 14px; }}
th {{ background: #1f2937; }}
.summary {{ display:flex; gap:2rem; margin-bottom:1.5rem; }}
.card {{ background:#111827; padding:1rem 1.5rem; border-radius:8px; }}
.card b {{ font-size:22px; display:block; }}
</style></head>
<body>
<h1>🤖 k8s-ai-ops — cluster advisor</h1>
<p>generated {r['generated_at']} · refreshes every {REFRESH_SECONDS}s</p>
<div class="summary">
  <div class="card">Nodes<b>{s['node_count']}</b></div>
  <div class="card">Pods<b>{s['pod_count']}</b></div>
  <div class="card">Node issues<b>{s['node_issue_count']}</b></div>
  <div class="card">Pod issues<b>{s['pod_issue_count']}</b></div>
  <div class="card">Right-size suggestions<b>{s['rightsizing_count']}</b></div>
  <div class="card">Containers w/o requests<b>{s['no_requests_count']}</b></div>
</div>

<h2>AI Deep-Dive (latest scheduled analysis)</h2>
{deepdive_html}

<h2>Node issues</h2>
<table><tr><th>Node</th><th>Condition</th><th>Severity</th><th>Message</th></tr>{rows_node}</table>

<h2>Pod issues</h2>
<table><tr><th>Pod</th><th>Phase</th><th>Reason</th><th>Restarts</th><th>Node</th><th>Severity</th></tr>{rows_pod}</table>

<h2>Resource right-sizing suggestions</h2>
<table><tr><th>Pod</th><th>Container</th><th>CPU</th><th>Memory</th><th>Suggestion</th></tr>{rows_rs}</table>

<h2>Top warning events</h2>
<table><tr><th>Reason</th><th>Count</th></tr>{rows_ev}</table>

<h2>Orphaned terminal pods by namespace (GC candidates)</h2>
<table><tr><th>Namespace</th><th>Count</th></tr>{rows_orphan}</table>

<p><a href="/api/analyze" style="color:#60a5fa">raw JSON</a></p>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return _render_html()
