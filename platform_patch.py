import html
import json
import os
import time
import urllib.error
import urllib.request

import uvicorn
from fastapi import Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

import app.main as main_mod
from app import db, kube
from app.auth import require_auth
from app.main import (
    CLUSTER_SCOPE_NS,
    DETAIL_REFRESH_SECONDS,
    REFRESH_SECONDS,
    _icon,
    _page as original_page,
    _render_dashboard,
    _status_pill,
    app,
)

SETTINGS_PATH = os.environ.get("ASSISTANT_SETTINGS_PATH", "/data/db/assistant.json")


STYLE = """
<style>
:root{--bg:#f3f6f8;--panel:#fff;--panel2:#eef3f5;--ink:#111827;--muted:#647284;--line:#d7e1e7;--accent:#13795b;--blue:#0f5f8f;--bad:#b42318;--ok:#15803d;--warn:#a16207;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Avenir Next,Segoe UI,system-ui,sans-serif;background:var(--bg);color:var(--ink)}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:320px minmax(0,1fr);min-height:100dvh}.sidebar{position:sticky;top:0;height:100dvh;overflow:auto;background:#101820;color:#eef7f4;border-right:1px solid rgba(255,255,255,.1);padding:18px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;margin-bottom:16px}.brand-mark{display:grid;place-items:center;width:32px;height:32px;border-radius:8px;background:#d7f8e8;color:#0b3b2d;font:900 15px var(--mono)}
.assistant-panel{background:#16232d;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:14px;box-shadow:0 10px 28px rgba(0,0,0,.2)}.assistant-panel h2{color:#fff;font-size:16px;margin:0 0 6px}.assistant-panel p{color:#aebcc8;font-size:13px;line-height:1.45;margin:6px 0 12px}
.status-light{display:inline-flex;align-items:center;gap:7px;font-size:12px;font-weight:800;color:#cbd5df}.status-light:before{content:"";width:8px;height:8px;border-radius:50%;background:#ef6357}.status-light.ok:before{background:#35c784;box-shadow:0 0 0 4px rgba(53,199,132,.13)}
.nav{margin-top:16px}.nav a,.cluster-link{display:flex;justify-content:space-between;gap:10px;color:#dce7ed;border-radius:7px;padding:9px 10px;font-size:14px}.nav a:hover,.cluster-link:hover{background:rgba(255,255,255,.08);text-decoration:none}.sidebar h4{color:#8ea0ad;font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin:18px 0 7px}.side-note{color:#8ea0ad;font-size:12px;line-height:1.45}
main{min-width:0}.topbar{position:sticky;top:0;z-index:5;padding:14px 28px;border-bottom:1px solid var(--line);background:rgba(243,246,248,.88);backdrop-filter:blur(10px);color:var(--muted);font-size:13px}.content{max-width:1500px;padding:26px 28px 46px}
h1{margin:0 0 8px;font-size:clamp(28px,4vw,44px);line-height:1.03;font-weight:800;text-wrap:balance}h2{margin:28px 0 12px;font-size:18px}.muted{color:var(--muted);font-size:13px;line-height:1.45}.page-head{display:flex;justify-content:space-between;align-items:flex-end;gap:18px;margin-bottom:20px}
.btn,button{display:inline-flex;align-items:center;justify-content:center;min-height:38px;background:var(--ink);color:#fff;border:0;padding:8px 15px;border-radius:7px;cursor:pointer;font-weight:750;font-size:14px}.btn:hover,button:hover{background:#263445;text-decoration:none}.btn.secondary{background:#e4ecef;color:#13202a}.btn.full{width:100%}
input,select,textarea{width:100%;background:#fff;color:var(--ink);border:1px solid #aebdc8;border-radius:7px;padding:9px 11px;font:inherit;font-size:14px}input:focus,select:focus,textarea:focus{outline:3px solid rgba(19,121,91,.18);border-color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:17px;margin-bottom:14px}.stat-grid,.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:15px;box-shadow:0 18px 50px rgba(25,43,56,.12)}.stat .n{display:block;margin-top:6px;font:800 30px/1 var(--mono)}
.kind-card{background:#fff;border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:13px;display:flex;gap:10px;min-height:86px;color:var(--ink)}.kind-card:hover{box-shadow:0 18px 50px rgba(25,43,56,.12);text-decoration:none}.kind-title{display:block;color:#0f1720;font-weight:800;overflow-wrap:anywhere}.kind-meta{display:block;color:var(--muted);font-size:12px;margin-top:5px}.scope-badge,.pill{display:inline-flex;align-items:center;min-height:23px;border-radius:6px;padding:2px 8px;font-size:12px;font-weight:750}.scope-badge{margin-top:8px;color:#0f5d48;background:#dff6ea;border:1px solid #bde7d0}.pill-ok{background:#dcfce7;color:var(--ok)}.pill-warn{background:#fef3c7;color:var(--warn)}.pill-bad{background:#fee4e2;color:var(--bad)}.pill-neutral{background:#e8eef2;color:#52606f}
.live-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px;animation:pulse 1.8s infinite}@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}th{background:var(--panel2);color:#506070;text-align:left;font-size:12px;letter-spacing:.04em;text-transform:uppercase;padding:11px 13px}td{padding:11px 13px;border-top:1px solid var(--line);font-size:14px;vertical-align:top}tr.clickable{cursor:pointer}tr.clickable:hover td{background:#f7fafb}
pre{background:#101820;color:#edf7fb;border:1px solid #253746;border-radius:8px;padding:15px;overflow:auto;white-space:pre-wrap;font:13px/1.55 var(--mono)}.tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin:18px 0;overflow:auto}.tab-btn{background:transparent;color:#52606f;border-bottom:3px solid transparent}.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}.tab-panel{display:none}.tab-panel.active{display:block}.qa{background:#fff;border-left:4px solid var(--accent);border-radius:0 8px 8px 0;padding:12px 14px;margin-bottom:10px}
.split{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,420px);gap:18px;align-items:start}.resource-layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:18px;align-items:start}.group-nav{position:sticky;top:70px;background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px}.group-nav a{display:flex;justify-content:space-between;color:#17222c;padding:9px 10px;border-radius:6px;font-size:13px}.group-nav a:hover{background:var(--panel2);text-decoration:none}.resource-group{margin-bottom:22px;scroll-margin-top:84px}.resource-group-head{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:12px}
@media(max-width:1000px){.app{grid-template-columns:1fr}.sidebar{position:relative;height:auto}.resource-layout,.split{grid-template-columns:1fr}.group-nav{position:relative;top:auto}.page-head{align-items:flex-start;flex-direction:column}}
</style>
"""


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    data["updated_at"] = time.time()
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f)


def ready(s=None):
    s = s or load_settings()
    return bool(s.get("enabled", True) and s.get("base_url") and s.get("model"))


def lmstudio(s, messages, max_tokens=900):
    url = s["base_url"].rstrip("/") + "/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if s.get("api_key"):
        headers["Authorization"] = "Bearer " + s["api_key"]
    body = json.dumps({"model": s["model"], "messages": messages, "temperature": 0.2, "max_tokens": max_tokens, "stream": False}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            data = json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LM Studio HTTP {e.code}: {e.read().decode('utf-8','replace')[:500]}") from e
    except Exception as e:
        raise RuntimeError(f"Could not reach LM Studio: {e}") from e
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or "(empty answer)"


def platform_page(title, body, breadcrumb=None, refresh=None):
    try:
        clusters = db.list_clusters()
    except Exception:
        clusters = []
    s = load_settings()
    cls = "ok" if ready(s) else ""
    text = "LM Studio connected" if ready(s) else "LM Studio not connected"
    links = "".join(f"<a class='cluster-link' href='/browse/{html.escape(c['name'])}'><span>{html.escape(c['name'])}</span><span>open</span></a>" for c in clusters)
    crumbs = " / ".join(f"<a href='{href}'>{html.escape(label)}</a>" if href else f"<span>{html.escape(label)}</span>" for label, href in (breadcrumb or []))
    tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return HTMLResponse(f"""<html><head><title>{html.escape(title)} · k8s-ai-ops</title>{tag}{STYLE}</head><body>
<div class="app"><aside class="sidebar">
  <div class="brand"><span class="brand-mark">K</span><span>k8s-ai-ops platform</span></div>
  <section class="assistant-panel"><h2>AI assistant</h2><p>LM Studio companion for live Kubernetes objects. Open any component, then ask about YAML, events, logs, config, risk, or remediation.</p><p><span class="status-light {cls}">{text}</span></p><a class="btn full" href="/assistant">Connection screen</a></section>
  <nav class="nav"><a href="/"><span>Dashboard</span><span>live</span></a><a href="/clusters"><span>Clusters</span><span>add</span></a><a href="/assistant"><span>Assistant</span><span>LM Studio</span></a></nav>
  <h4>Registered clusters</h4>{links or '<p class="side-note">No clusters registered yet.</p>'}
</aside><main><div class="topbar">{crumbs or '&nbsp;'}</div><div class="content">{body}</div></main></div>
<script>
function filterCards(inputId, containerId){{let i=document.getElementById(inputId);if(!i)return;let q=i.value.toLowerCase();document.querySelectorAll('#'+containerId+' .kind-card').forEach(e=>{{e.style.display=e.dataset.label.toLowerCase().includes(q)?'flex':'none'}});document.querySelectorAll('#'+containerId+' .resource-group').forEach(s=>{{s.style.display=Array.from(s.querySelectorAll('.kind-card')).some(e=>e.style.display!=='none')?'block':'none'}})}}
function showTab(n){{document.querySelectorAll('.tab-panel').forEach(e=>e.classList.remove('active'));document.querySelectorAll('.tab-btn').forEach(e=>e.classList.remove('active'));let p=document.getElementById('tab-'+n),b=document.getElementById('btn-'+n);if(p&&b){{p.classList.add('active');b.classList.add('active');location.hash=n}}}}
document.addEventListener('DOMContentLoaded',()=>{{let h=location.hash.replace('#','');if(h&&document.getElementById('tab-'+h))showTab(h)}})
</script></body></html>""")


async def dashboard(_=Depends(require_auth)):
    return platform_page("Dashboard", _render_dashboard(), [("Dashboard", "")], REFRESH_SECONDS)


async def assistant_page(saved: str | None = None, _=Depends(require_auth)):
    s = load_settings()
    msg = html.escape(s.get("last_test_message", ""))
    body = f"""
<div class="page-head"><div><h1>AI assistant connection</h1><p class="muted">Connect to an LM Studio server with the OpenAI-compatible API enabled. The URL must be reachable from inside the cluster.</p></div><span class="pill {'pill-ok' if ready(s) else 'pill-bad'}">{'connected' if ready(s) else 'not connected'}</span></div>
{('<div class="card"><b>Saved.</b> Connection settings updated.</div>' if saved else '')}
<div class="split"><form class="card" method="post" action="/assistant/save">
<h2 style="margin-top:0">LM Studio</h2>
<p><label>Base URL<br><input name="base_url" required placeholder="http://lmstudio.default.svc.cluster.local:1234" value="{html.escape(s.get('base_url',''))}"></label></p>
<p><label>Model<br><input name="model" required placeholder="local-model" value="{html.escape(s.get('model',''))}"></label></p>
<p><label>API key optional<br><input name="api_key" value="{html.escape(s.get('api_key',''))}"></label></p>
<p><label><input type="checkbox" name="enabled" style="width:auto" {'checked' if s.get('enabled', True) else ''}> Enable direct answers</label></p>
<p><label>System prompt<br><textarea name="system_prompt" rows="6">{html.escape(s.get('system_prompt','You are a senior Kubernetes SRE. Use only supplied live YAML, events, and logs. Give evidence, risk, root cause, and next action. Never invent cluster facts.'))}</textarea></label></p>
<button type="submit">Save and test</button></form>
<aside class="card"><h2 style="margin-top:0">Assistant behavior</h2><p class="muted">On each object detail page, the assistant sends current YAML, related events, and pod log tail to LM Studio. If LM Studio fails, the question remains captured in the existing queue.</p><p class="muted">{msg}</p></aside></div>"""
    return platform_page("Assistant", body, [("Assistant", "")])


async def assistant_save(base_url: str = Form(...), model: str = Form(...), api_key: str = Form(""), system_prompt: str = Form(""), enabled: str = Form(None), _=Depends(require_auth)):
    s = {"base_url": base_url.rstrip("/"), "model": model, "api_key": api_key, "system_prompt": system_prompt, "enabled": bool(enabled)}
    if enabled:
        try:
            s["last_test_message"] = lmstudio(s, [{"role": "user", "content": "Reply with exactly: connected"}], 20)[:300]
            s["last_test_ok"] = True
        except Exception as e:
            s["last_test_message"] = str(e)[:800]
            s["last_test_ok"] = False
    save_settings(s)
    return RedirectResponse("/assistant?saved=1", status_code=303)


async def ask(cluster: str, namespace: str, kind: str, name: str, question: str = Form(...), _=Depends(require_auth)):
    ns = None if namespace == CLUSTER_SCOPE_NS else namespace
    qid = db.submit_question(cluster, ns, kind, name, question)
    s = load_settings()
    if ready(s):
        try:
            meta = kube.kind_by_slug(cluster, kind)
            yml = kube.get_object_yaml(cluster, ns, kind, name)
            events = kube.related_events(cluster, ns, name) if ns else []
            logs = kube.pod_log_tail(cluster, ns, name) if meta.get("kind") == "Pod" and ns else "not applicable"
            ev = "\\n".join(f"{e.get('type')} {e.get('reason')} x{e.get('count')}: {e.get('message')}" for e in events[:25]) or "none"
            prompt = f"Question: {question}\\nCluster: {cluster}\\nNamespace: {ns or '(cluster-scoped)'}\\nKind: {meta.get('label')}\\nName: {name}\\n\\nYAML:\\n```yaml\\n{yml[:18000]}\\n```\\n\\nEvents:\\n{ev}\\n\\nLogs:\\n```\\n{logs[:8000]}\\n```"
            ans = lmstudio(s, [{"role": "system", "content": s.get("system_prompt") or "You are a Kubernetes SRE."}, {"role": "user", "content": prompt}])
            db.answer_question(qid, ans)
        except Exception as e:
            db.answer_question(qid, f"LM Studio direct answer failed: {e}")
    return RedirectResponse(f"/browse/{cluster}/{namespace}/{kind}/{name}#companion", status_code=303)


def replace(path, endpoint, methods=("GET",)):
    app.router.routes = [r for r in app.router.routes if not (isinstance(r, Route) and r.path == path and any(m in r.methods for m in methods))]
    (app.post(path) if "POST" in methods else app.get(path, response_class=HTMLResponse))(endpoint)


replace("/", dashboard)
replace("/browse/{cluster}/{namespace}/{kind}/{name}/ask", ask, ("POST",))
app.get("/assistant", response_class=HTMLResponse)(assistant_page)
app.post("/assistant/save")(assistant_save)
main_mod._page = platform_page

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
