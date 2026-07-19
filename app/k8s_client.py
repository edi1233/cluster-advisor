import logging

from kubernetes import client, config

log = logging.getLogger("k8s-ai-ops")


def load_client() -> tuple[client.CoreV1Api, client.AppsV1Api, client.CustomObjectsApi]:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api(), client.CustomObjectsApi()


def parse_cpu(q: str | None) -> float:
    """Return millicores for a Kubernetes CPU quantity string."""
    if not q:
        return 0.0
    q = str(q)
    if q.endswith("n"):
        return float(q[:-1]) / 1_000_000
    if q.endswith("u"):
        return float(q[:-1]) / 1_000
    if q.endswith("m"):
        return float(q[:-1])
    return float(q) * 1000


def parse_mem(q: str | None) -> float:
    """Return MiB for a Kubernetes memory quantity string."""
    if not q:
        return 0.0
    q = str(q)
    units = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "K": 1 / 1024 * 0.976563,
        "M": 0.953674,
        "G": 953.674,
    }
    for suffix, factor in units.items():
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * factor
    # plain bytes
    try:
        return float(q) / (1024 * 1024)
    except ValueError:
        return 0.0
