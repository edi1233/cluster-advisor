import datetime as dt
import logging
from collections import Counter, defaultdict

from kubernetes.client.rest import ApiException

from .k8s_client import parse_cpu, parse_mem

log = logging.getLogger("k8s-ai-ops")

# thresholds
RESTART_WARN = 5
CPU_HIGH_UTIL = 0.85   # usage / limit-or-request
CPU_LOW_UTIL = 0.20
MEM_HIGH_UTIL = 0.85
MEM_LOW_UTIL = 0.25
MIN_SAMPLE_MI = 16      # ignore right-sizing noise below this memory floor
MIN_SAMPLE_M = 15       # ignore right-sizing noise below this cpu floor (millicores)


def _age_hours(timestamp) -> float:
    if not timestamp:
        return 0.0
    now = dt.datetime.now(dt.timezone.utc)
    return (now - timestamp).total_seconds() / 3600


def analyze(core, apps, custom) -> dict:
    report: dict = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "node_issues": [],
        "pod_issues": [],
        "event_summary": [],
        "rightsizing": [],
        "no_requests": [],
        "orphaned_terminal_pods": [],
        "summary": {},
    }

    # ---- nodes ----
    nodes = core.list_node().items
    bad_conditions = {"DiskPressure", "MemoryPressure", "PIDPressure", "NetworkUnavailable"}
    node_ready = {}
    for n in nodes:
        name = n.metadata.name
        for c in n.status.conditions or []:
            if c.type == "Ready":
                node_ready[name] = c.status == "True"
                if c.status != "True":
                    report["node_issues"].append({
                        "node": name, "condition": "Ready", "status": c.status,
                        "message": c.message, "severity": "critical",
                    })
            elif c.type in bad_conditions and c.status == "True":
                report["node_issues"].append({
                    "node": name, "condition": c.type, "status": c.status,
                    "message": c.message, "severity": "warning",
                    "transitioned": c.last_transition_time.isoformat() if c.last_transition_time else None,
                })

    # ---- pods ----
    pods = core.list_pod_for_all_namespaces().items
    phase_counts = Counter()
    terminal_by_ns = defaultdict(int)
    pod_index = {}  # (ns, name) -> pod for join with metrics

    for p in pods:
        ns = p.metadata.namespace
        name = p.metadata.name
        phase = p.status.phase
        phase_counts[phase] += 1
        pod_index[(ns, name)] = p

        restarts = sum(cs.restart_count for cs in (p.status.container_statuses or []))
        owner_kinds = {o.kind for o in (p.metadata.owner_references or [])}
        is_job_owned = "Job" in owner_kinds

        if phase in ("Failed",) and not is_job_owned:
            age = _age_hours(p.metadata.creation_timestamp)
            reason = p.status.reason or (p.status.container_statuses[0].state.terminated.reason
                                          if p.status.container_statuses and p.status.container_statuses[0].state.terminated
                                          else None)
            report["pod_issues"].append({
                "namespace": ns, "pod": name, "phase": phase, "reason": reason,
                "node": p.spec.node_name, "restarts": restarts,
                "message": p.status.message, "age_hours": round(age, 1),
                "severity": "warning",
            })

        if phase in ("Failed", "Succeeded") and is_job_owned is False:
            terminal_by_ns[ns] += 1

        if phase == "Pending":
            age = _age_hours(p.metadata.creation_timestamp)
            if age > 0.166:  # >10 min stuck pending
                report["pod_issues"].append({
                    "namespace": ns, "pod": name, "phase": "Pending", "reason": "StuckScheduling",
                    "node": p.spec.node_name, "restarts": 0, "age_hours": round(age, 1),
                    "severity": "critical",
                })

        if restarts >= RESTART_WARN and phase == "Running":
            report["pod_issues"].append({
                "namespace": ns, "pod": name, "phase": phase, "reason": "HighRestartCount",
                "node": p.spec.node_name, "restarts": restarts, "severity": "warning",
            })

        # missing resource requests (Running pods only, skip completed jobs)
        if phase == "Running":
            for c in p.spec.containers or []:
                req = (c.resources.requests or {}) if c.resources else {}
                if not req.get("cpu") or not req.get("memory"):
                    report["no_requests"].append({
                        "namespace": ns, "pod": name, "container": c.name,
                        "missing": [k for k in ("cpu", "memory") if not req.get(k)],
                    })

    report["summary"]["pod_phase_counts"] = dict(phase_counts)
    report["orphaned_terminal_pods"] = [
        {"namespace": ns, "count": n} for ns, n in sorted(terminal_by_ns.items(), key=lambda x: -x[1]) if n > 0
    ]

    # ---- events (last window, warnings first) ----
    try:
        events = core.list_event_for_all_namespaces(limit=500).items
        reason_counts = Counter()
        for e in events:
            if getattr(e, "type", None) == "Warning":
                reason_counts[e.reason] += 1
        report["event_summary"] = [
            {"reason": r, "count": c} for r, c in reason_counts.most_common(15)
        ]
    except ApiException as e:
        log.warning("event list failed: %s", e)

    # ---- right-sizing from metrics.k8s.io ----
    try:
        pod_metrics = custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")
        for item in pod_metrics.get("items", []):
            ns = item["metadata"]["namespace"]
            name = item["metadata"]["name"]
            pod = pod_index.get((ns, name))
            if not pod or pod.status.phase != "Running":
                continue
            containers_spec = {c.name: c for c in (pod.spec.containers or [])}
            for c in item.get("containers", []):
                cname = c["name"]
                spec = containers_spec.get(cname)
                if not spec:
                    continue
                usage_cpu_m = parse_cpu(c["usage"].get("cpu"))
                usage_mem_mi = parse_mem(c["usage"].get("memory"))
                res = spec.resources
                req = (res.requests or {}) if res else {}
                lim = (res.limits or {}) if res else {}
                req_cpu_m = parse_cpu(req.get("cpu"))
                lim_cpu_m = parse_cpu(lim.get("cpu"))
                req_mem_mi = parse_mem(req.get("memory"))
                lim_mem_mi = parse_mem(lim.get("memory"))

                cpu_ceiling = lim_cpu_m or req_cpu_m
                mem_ceiling = lim_mem_mi or req_mem_mi

                suggestions = []
                if req_cpu_m and usage_cpu_m >= MIN_SAMPLE_M:
                    util = usage_cpu_m / cpu_ceiling if cpu_ceiling else None
                    if util is not None and util >= CPU_HIGH_UTIL:
                        suggestions.append(f"increase CPU (using {usage_cpu_m:.0f}m vs ceiling {cpu_ceiling:.0f}m, {util:.0%})")
                    elif util is not None and util <= CPU_LOW_UTIL and req_cpu_m > MIN_SAMPLE_M:
                        suggestions.append(f"decrease CPU request (using {usage_cpu_m:.0f}m vs request {req_cpu_m:.0f}m, {util:.0%})")
                if req_mem_mi and usage_mem_mi >= MIN_SAMPLE_MI:
                    util = usage_mem_mi / mem_ceiling if mem_ceiling else None
                    if util is not None and util >= MEM_HIGH_UTIL:
                        suggestions.append(f"increase memory (using {usage_mem_mi:.0f}Mi vs ceiling {mem_ceiling:.0f}Mi, {util:.0%})")
                    elif util is not None and util <= MEM_LOW_UTIL and req_mem_mi > MIN_SAMPLE_MI:
                        suggestions.append(f"decrease memory request (using {usage_mem_mi:.0f}Mi vs request {req_mem_mi:.0f}Mi, {util:.0%})")

                if suggestions:
                    report["rightsizing"].append({
                        "namespace": ns, "pod": name, "container": cname,
                        "cpu_usage_m": round(usage_cpu_m, 1), "cpu_request_m": round(req_cpu_m, 1), "cpu_limit_m": round(lim_cpu_m, 1),
                        "mem_usage_mi": round(usage_mem_mi, 1), "mem_request_mi": round(req_mem_mi, 1), "mem_limit_mi": round(lim_mem_mi, 1),
                        "suggestions": suggestions,
                    })
    except ApiException as e:
        log.warning("metrics.k8s.io unavailable: %s", e)

    report["summary"]["node_count"] = len(nodes)
    report["summary"]["pod_count"] = len(pods)
    report["summary"]["node_issue_count"] = len(report["node_issues"])
    report["summary"]["pod_issue_count"] = len(report["pod_issues"])
    report["summary"]["rightsizing_count"] = len(report["rightsizing"])
    report["summary"]["no_requests_count"] = len(report["no_requests"])
    return report
