import logging
import tempfile
import time

import yaml
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes import dynamic
from kubernetes.client.rest import ApiException

from . import db

log = logging.getLogger("k8s-ai-ops")

KIND_CACHE_TTL = 60
_kind_cache: dict[str, tuple[float, list[dict]]] = {}


def _api_client_for(cluster_row: dict) -> k8s_client.ApiClient:
    kind = cluster_row["kind"]
    if kind == "incluster":
        k8s_config.load_incluster_config()
        return k8s_client.ApiClient()
    if kind == "kubeconfig":
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(cluster_row["kubeconfig"])
            path = f.name
        return k8s_config.new_client_from_config(config_file=path)
    # token auth
    conf = k8s_client.Configuration()
    conf.host = cluster_row["api_server"]
    conf.api_key["authorization"] = cluster_row["token"]
    conf.api_key_prefix["authorization"] = "Bearer"
    conf.verify_ssl = bool(cluster_row["verify_ssl"])
    return k8s_client.ApiClient(configuration=conf)


def dynamic_client(cluster_name: str) -> dynamic.DynamicClient:
    row = db.get_cluster(cluster_name)
    if not row:
        raise ValueError(f"unknown cluster {cluster_name}")
    return dynamic.DynamicClient(_api_client_for(row))


def core_v1(cluster_name: str) -> k8s_client.CoreV1Api:
    row = db.get_cluster(cluster_name)
    if not row:
        raise ValueError(f"unknown cluster {cluster_name}")
    return k8s_client.CoreV1Api(_api_client_for(row))


def test_connection(cluster_row: dict) -> tuple[bool, str]:
    try:
        api = _api_client_for(cluster_row)
        version_api = k8s_client.VersionApi(api)
        v = version_api.get_code()
        return True, f"{v.git_version}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _discover_kinds(cluster_name: str) -> list[dict]:
    """Live discovery of every listable resource type on the cluster (same data
    'kubectl api-resources' uses) so built-ins and CRDs both show up automatically."""
    row = db.get_cluster(cluster_name)
    if not row:
        raise ValueError(f"unknown cluster {cluster_name}")
    api = _api_client_for(row)
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []

    def add(group_version: str, resources: list[dict]):
        for r in resources:
            if "/" in r["name"]:  # skip subresources: pods/log, deployments/status, ...
                continue
            if "list" not in r.get("verbs", []):
                continue
            key = (group_version, r["kind"])
            if key in seen:
                continue
            seen.add(key)
            slug = f"{r['kind']}-{group_version.replace('/', '-')}"
            label = r["kind"] if group_version == "v1" else f"{r['kind']} ({group_version})"
            out.append({
                "slug": slug, "label": label, "kind": r["kind"],
                "api_version": group_version, "namespaced": bool(r.get("namespaced", True)),
            })

    core = api.call_api("/api/v1", "GET", response_type=object, auth_settings=["BearerToken"])[0]
    add("v1", core.get("resources", []))

    groups = api.call_api("/apis", "GET", response_type=object, auth_settings=["BearerToken"])[0]
    for g in groups.get("groups", []):
        gv = g["preferredVersion"]["groupVersion"]
        try:
            rl = api.call_api(f"/apis/{gv}", "GET", response_type=object, auth_settings=["BearerToken"])[0]
        except ApiException as e:
            log.warning("discovery failed for %s: %s", gv, e)
            continue
        add(gv, rl.get("resources", []))

    return sorted(out, key=lambda k: k["label"])


def list_kinds(cluster_name: str) -> list[dict]:
    now = time.time()
    cached = _kind_cache.get(cluster_name)
    if cached and now - cached[0] < KIND_CACHE_TTL:
        return cached[1]
    kinds = _discover_kinds(cluster_name)
    _kind_cache[cluster_name] = (now, kinds)
    return kinds


def kind_by_slug(cluster_name: str, slug: str) -> dict:
    for k in list_kinds(cluster_name):
        if k["slug"] == slug:
            return k
    raise ValueError(f"unknown kind {slug}")


def list_namespaces(cluster_name: str) -> list[str]:
    dyn = dynamic_client(cluster_name)
    res = dyn.resources.get(api_version="v1", kind="Namespace")
    return sorted(item.metadata.name for item in res.get().items)


def list_objects(cluster_name: str, namespace: str | None, slug: str) -> list[dict]:
    meta = kind_by_slug(cluster_name, slug)
    av, k, namespaced = meta["api_version"], meta["kind"], meta["namespaced"]
    dyn = dynamic_client(cluster_name)
    res = dyn.resources.get(api_version=av, kind=k)
    items = res.get(namespace=namespace).items if namespaced else res.get().items
    out = []
    for it in items:
        d = it.to_dict()
        m = d.get("metadata", {})
        status = d.get("status", {})
        row = {
            "name": m.get("name"),
            "namespace": m.get("namespace"),
            "age": m.get("creationTimestamp"),
        }
        if k == "Pod":
            row["phase"] = status.get("phase")
            row["node"] = d.get("spec", {}).get("nodeName")
            cs = status.get("containerStatuses") or []
            row["restarts"] = sum(c.get("restartCount", 0) for c in cs)
            row["ready"] = f"{sum(1 for c in cs if c.get('ready'))}/{len(cs)}"
        elif k == "Secret":
            row["type"] = d.get("type")
            row["keys"] = list((d.get("data") or {}).keys())
        elif k in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"):
            row["ready"] = f"{status.get('readyReplicas', 0) or 0}/{status.get('replicas', 0) or 0}"
        out.append(row)
    return sorted(out, key=lambda r: (r.get("namespace") or "", r["name"]))


def _redact(d: dict, kind: str) -> dict:
    if kind == "Secret" and "data" in d:
        d = dict(d)
        d["data"] = {k: "***redacted***" for k in d["data"]}
    return d


def get_object(cluster_name: str, namespace: str | None, slug: str, name: str) -> dict:
    meta = kind_by_slug(cluster_name, slug)
    av, k, namespaced = meta["api_version"], meta["kind"], meta["namespaced"]
    dyn = dynamic_client(cluster_name)
    res = dyn.resources.get(api_version=av, kind=k)
    obj = res.get(name=name, namespace=namespace) if namespaced else res.get(name=name)
    return _redact(obj.to_dict(), k)


def get_object_yaml(cluster_name: str, namespace: str | None, slug: str, name: str) -> str:
    d = get_object(cluster_name, namespace, slug, name)
    return yaml.safe_dump(d, sort_keys=False, default_flow_style=False)


def related_events(cluster_name: str, namespace: str, name: str) -> list[dict]:
    try:
        core = core_v1(cluster_name)
        events = core.list_namespaced_event(namespace=namespace).items
        return [
            {"type": e.type, "reason": e.reason, "message": e.message, "count": e.count,
             "last_seen": e.last_timestamp.isoformat() if e.last_timestamp else None}
            for e in events if e.involved_object and e.involved_object.name == name
        ]
    except ApiException as e:
        log.warning("event fetch failed: %s", e)
        return []


def pod_log_tail(cluster_name: str, namespace: str, name: str, tail_lines: int = 100) -> str:
    try:
        core = core_v1(cluster_name)
        return core.read_namespaced_pod_log(name=name, namespace=namespace, tail_lines=tail_lines)
    except ApiException as e:
        return f"(log fetch failed: {e.reason})"
