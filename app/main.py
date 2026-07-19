import asyncio
import html
import json
import logging
import os

from fastapi import Depends, FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import db, kube
from .analyzer import analyze
from .auth import require_auth
from .k8s_client import load_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("k8s-ai-ops")

REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "120"))
DEEPDIVE_PATH = os.environ.get("DEEPDIVE_PATH", "/data/deepdive/report.json")

app = FastAPI(title="k8s-ai-ops")

state = {"report": None, "error": None}
core = apps_api = custom = None

CLUSTER_SCOPE_NS = "-"  # sentinel path segment for cluster-scoped kinds


@app.on_event("startup")
async def startup():
    global core, apps_api, custom
    db.init_db()
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


# ---- internal companion queue API (no auth: internal-network only, used by the scheduled AI responder) ----

@app.get("/api/companion/pending")
async def api_companion_pending():
    return JSONResponse(db.list_pending_questions())


@app.post("/api/companion/{qid}/answer")
async def api_companion_answer(qid: int, payload: dict):
    db.answer_question(qid, payload.get("answer", ""))
    return {"ok": True}


# ---- page chrome ----

NAV = """
<div style="margin-bottom:1.5rem">
  <a href="/" style="color:#60a5fa;margin-right:1.2rem">Dashboard</a>
  <a href="/clusters" style="color:#60a5fa;margin-right:1.2rem">Clusters</a>
</div>
"""

STYLE = """
<style>
body { font-family: -apple-system, sans-serif; margin: 2rem; background:#0b0f19; color:#e5e7eb; }
h1,h2,h3 { color:#f9fafb; }
a { color:#60a5fa; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
td, th { border: 1px solid #374151; padding: 6px 10px; text-align: left; font-size: 14px; }
th { background: #1f2937; }
.card { background:#111827; padding:1rem 1.5rem; border-radius:8px; margin-bottom:1rem; }
input, select, textarea { background:#1f2937; color:#e5e7eb; border:1px solid #374151; border-radius:4px; padding:6px; }
button { background:#2563eb; color:#fff; border:none; padding:8px 16px; border-radius:4px; cursor:pointer; }
pre { background:#111827; padding:1rem; border-radius:8px; overflow-x:auto; white-space:pre-wrap; }
.badge { padding:2px 8px; border-radius:10px; font-size:12px; color:#fff; }
</style>
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<html><head><title>{title}</title>{STYLE}</head><body>{NAV}{body}</body></html>")


def _badge(sev: str) -> str:
    color = {"critical": "#dc2626", "warning": "#d97706"}.get(sev, "#6b7280")
    return f'<span class="badge" style="background:{color}">{sev}</span>'


# ---- dashboard (pxinf issues, unchanged behavior) ----

def _render_dashboard() -> str:
    r = state["report"]
    dd = _read_deepdive()
    if r is None:
        return "<h1>k8s-ai-ops</h1><p>Collecting initial data…</p>"

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
<h1>🤖 k8s-ai-ops — pxinf cluster advisor</h1>
<p>generated {r['generated_at']} · refreshes every {REFRESH_SECONDS}s</p>
<div style="display:flex;gap:1.5rem;margin-bottom:1.5rem">
  <div class="card">Nodes<b style="display:block;font-size:22px">{s['node_count']}</b></div>
  <div class="card">Pods<b style="display:block;font-size:22px">{s['pod_count']}</b></div>
  <div class="card">Node issues<b style="display:block;font-size:22px">{s['node_issue_count']}</b></div>
  <div class="card">Pod issues<b style="display:block;font-size:22px">{s['pod_issue_count']}</b></div>
  <div class="card">Right-size suggestions<b style="display:block;font-size:22px">{s['rightsizing_count']}</b></div>
  <div class="card">Containers w/o requests<b style="display:block;font-size:22px">{s['no_requests_count']}</b></div>
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
<p><a href="/api/analyze">raw JSON</a></p>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard(_=Depends(require_auth)):
    return _page("k8s-ai-ops", _render_dashboard())


# ---- clusters ----

@app.get("/clusters", response_class=HTMLResponse)
async def clusters_page(_=Depends(require_auth)):
    rows = ""
    for c in db.list_clusters():
        rows += (f"<tr><td><a href='/browse/{c['name']}'>{c['name']}</a></td><td>{c['kind']}</td>"
                  f"<td>{c.get('api_server') or ''}</td></tr>")
    body = f"""
<h1>Clusters</h1>
<table><tr><th>Name</th><th>Auth</th><th>API server</th></tr>{rows}</table>
<div class="card">
<h3>Add cluster</h3>
<form method="post" action="/clusters/add">
  <p>Name: <input name="name" required></p>
  <p>Auth type:
    <select name="auth_kind">
      <option value="token">API server + bearer token</option>
      <option value="kubeconfig">Paste kubeconfig</option>
    </select>
  </p>
  <p>API server (for token auth): <input name="api_server" placeholder="https://host:6443" style="width:320px"></p>
  <p>Bearer token (for token auth): <input name="token" style="width:320px"></p>
  <p>Verify TLS: <input type="checkbox" name="verify_ssl" checked></p>
  <p>Kubeconfig (for kubeconfig auth):<br><textarea name="kubeconfig" rows="8" cols="60"></textarea></p>
  <button type="submit">Add + test connection</button>
</form>
</div>
"""
    return _page("Clusters", body)


@app.post("/clusters/add")
async def clusters_add(
    name: str = Form(...),
    auth_kind: str = Form(...),
    api_server: str = Form(""),
    token: str = Form(""),
    verify_ssl: str = Form(None),
    kubeconfig: str = Form(""),
    _=Depends(require_auth),
):
    row = {
        "kind": auth_kind, "api_server": api_server or None, "token": token or None,
        "verify_ssl": 1 if verify_ssl else 0, "kubeconfig": kubeconfig or None,
    }
    ok, msg = kube.test_connection(row)
    if not ok:
        return _page("Clusters", f"<h1>Connection failed</h1><pre>{html.escape(msg)}</pre><p><a href='/clusters'>back</a></p>")
    db.add_cluster(name, auth_kind, api_server or None, token or None, bool(verify_ssl), kubeconfig or None)
    return RedirectResponse(url="/clusters", status_code=303)


# ---- browse ----

@app.get("/browse/{cluster}", response_class=HTMLResponse)
async def browse_cluster(cluster: str, _=Depends(require_auth)):
    try:
        namespaces = kube.list_namespaces(cluster)
    except Exception as e:  # noqa: BLE001
        return _page("Browse", f"<h1>{cluster}</h1><p style='color:#f87171'>Error listing namespaces: {html.escape(str(e))}</p>")

    kind_links_cluster_scope = "".join(
        f"<a class='card' style='display:inline-block;margin-right:8px' href='/browse/{cluster}/{CLUSTER_SCOPE_NS}/{label}'>{label}</a>"
        for label, _av, _k, ns in kube.RESOURCE_KINDS if not ns
    )
    ns_options = "".join(f"<option value='{n}'>{n}</option>" for n in namespaces)
    kind_options = "".join(f"<option value='{label}'>{label}</option>" for label, _av, _k, ns in kube.RESOURCE_KINDS if ns)

    body = f"""
<h1>Browse — {cluster}</h1>
<div class="card">
<h3>Cluster-scoped</h3>
{kind_links_cluster_scope}
</div>
<div class="card">
<h3>Namespaced</h3>
<form method="get" id="nsform" action="#" onsubmit="event.preventDefault(); location.href='/browse/{cluster}/'+document.getElementById('ns').value+'/'+document.getElementById('kind').value;">
  Namespace: <select id="ns" name="ns">{ns_options}</select>
  Kind: <select id="kind" name="kind">{kind_options}</select>
  <button type="submit">Browse</button>
</form>
</div>
"""
    return _page(f"Browse {cluster}", body)


@app.get("/browse/{cluster}/{namespace}/{kind}", response_class=HTMLResponse)
async def browse_list(cluster: str, namespace: str, kind: str, _=Depends(require_auth)):
    ns = None if namespace == CLUSTER_SCOPE_NS else namespace
    try:
        items = kube.list_objects(cluster, ns, kind)
    except Exception as e:  # noqa: BLE001
        return _page("Browse", f"<h1>{kind}</h1><p style='color:#f87171'>Error: {html.escape(str(e))}</p>")

    extra_cols = [k for k in ("phase", "ready", "restarts", "node", "type", "keys") if any(k in i for i in items)]
    header = "<th>Namespace</th><th>Name</th>" + "".join(f"<th>{c}</th>" for c in extra_cols)
    rows = ""
    for it in items:
        link_ns = it.get("namespace") or CLUSTER_SCOPE_NS
        rows += f"<tr><td>{it.get('namespace') or ''}</td><td><a href='/browse/{cluster}/{link_ns}/{kind}/{it['name']}'>{it['name']}</a></td>"
        for c in extra_cols:
            v = it.get(c, "")
            rows += f"<td>{', '.join(v) if isinstance(v, list) else v}</td>"
        rows += "</tr>"

    body = f"""
<h1>{kind} — {cluster}{' / ' + namespace if ns else ''}</h1>
<p><a href="/browse/{cluster}">back to browse</a></p>
<table><tr>{header}</tr>{rows or f"<tr><td colspan={2+len(extra_cols)}>No objects found</td></tr>"}</table>
"""
    return _page(kind, body)


@app.get("/browse/{cluster}/{namespace}/{kind}/{name}", response_class=HTMLResponse)
async def browse_detail(cluster: str, namespace: str, kind: str, name: str, _=Depends(require_auth)):
    ns = None if namespace == CLUSTER_SCOPE_NS else namespace
    try:
        yaml_text = kube.get_object_yaml(cluster, ns, kind, name)
    except Exception as e:  # noqa: BLE001
        yaml_text = f"(error fetching object: {e})"

    events = kube.related_events(cluster, ns, name) if ns else []
    ev_rows = "".join(
        f"<tr><td>{e['type']}</td><td>{e['reason']}</td><td>{e['message']}</td><td>{e['count']}</td></tr>" for e in events
    ) or "<tr><td colspan=4>No related events</td></tr>"

    logs_html = ""
    if kind == "Pods" and ns:
        logs = kube.pod_log_tail(cluster, ns, name)
        logs_html = f"<h2>Recent logs (tail)</h2><pre>{html.escape(logs)}</pre>"

    qas = db.list_recent_questions(cluster, ns, kind, name)
    qa_html = "".join(
        f"<div class='card'><b>Q:</b> {html.escape(q['question'])}<br>"
        f"<b>A:</b> {'<i>pending — the AI companion checks every ~5 min</i>' if not q['answer'] else html.escape(q['answer'])}</div>"
        for q in qas
    ) or "<p>No questions asked yet.</p>"

    body = f"""
<h1>{kind}/{name}</h1>
<p>{cluster}{' / ' + ns if ns else ''} · <a href="/browse/{cluster}/{namespace}/{kind}">back to list</a></p>
<h2>Object</h2>
<pre>{html.escape(yaml_text)}</pre>
<h2>Related events</h2>
<table><tr><th>Type</th><th>Reason</th><th>Message</th><th>Count</th></tr>{ev_rows}</table>
{logs_html}
<h2>🤖 AI companion</h2>
<p style="color:#9ca3af">Ask a question about this object. A scheduled AI analysis run checks for new questions roughly every 5 minutes and writes back a real answer (it has full cluster read access + reasoning, not a canned response).</p>
{qa_html}
<form method="post" action="/browse/{cluster}/{namespace}/{kind}/{name}/ask">
  <textarea name="question" rows="2" cols="60" placeholder="e.g. why does this pod keep restarting?" required></textarea><br>
  <button type="submit">Ask AI</button>
</form>
"""
    return _page(f"{kind}/{name}", body)


@app.post("/browse/{cluster}/{namespace}/{kind}/{name}/ask")
async def browse_ask(cluster: str, namespace: str, kind: str, name: str, question: str = Form(...), _=Depends(require_auth)):
    ns = None if namespace == CLUSTER_SCOPE_NS else namespace
    db.submit_question(cluster, ns, kind, name, question)
    return RedirectResponse(url=f"/browse/{cluster}/{namespace}/{kind}/{name}", status_code=303)
