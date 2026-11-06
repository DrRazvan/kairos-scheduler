"""Node selection: the "which node?" question, as pure functions (no API calls).

A node is feasible if it is Ready, schedulable, untainted, and matches the pod's
nodeSelector. We pick one at random. Phase 2 replaces this with Filter/Score plugins.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from kubernetes.client import V1Node, V1Pod

# Taint effects that keep pods off a node unless the pod tolerates them.
# Phase 1 has no toleration support yet, so such nodes are simply skipped
# (this is what keeps pods off the kind control-plane node).
BLOCKING_TAINT_EFFECTS = frozenset({"NoSchedule", "NoExecute"})


@dataclass(frozen=True)
class Rejection:
    """Why a node was filtered out. Used for FailedScheduling messages."""

    node: str
    reason: str


def node_name(node: V1Node) -> str:
    if node.metadata is None or not node.metadata.name:
        raise ValueError("node from the API has no name")
    return node.metadata.name


def is_ready(node: V1Node) -> bool:
    """True if the node's `Ready` condition is `True` (kubelet is healthy)."""
    conditions = node.status.conditions if node.status else None
    return any(c.type == "Ready" and c.status == "True" for c in conditions or [])


def matches_node_selector(pod: V1Pod, node: V1Node) -> bool:
    """True if the node has every `key: value` label from the pod's nodeSelector.

    Same rule as Kubernetes: all pairs must match exactly; an empty selector matches any node.
    """
    selector = (pod.spec.node_selector if pod.spec else None) or {}
    labels = (node.metadata.labels if node.metadata else None) or {}
    return all(labels.get(key) == value for key, value in selector.items())


def rejection_reason(node: V1Node, pod: V1Pod) -> str | None:
    """Return why `node` can't take `pod`, or None if it can."""
    if not is_ready(node):
        return "node(s) were not Ready"
    if node.spec and node.spec.unschedulable:
        return "node(s) were unschedulable"
    for taint in (node.spec.taints if node.spec else None) or []:
        if taint.effect in BLOCKING_TAINT_EFFECTS:
            return f"node(s) had untolerated taint {{{taint.key}: {taint.effect}}}"
    if not matches_node_selector(pod, node):
        return "node(s) didn't match Pod's node affinity/selector"
    return None


def filter_nodes(nodes: list[V1Node], pod: V1Pod) -> tuple[list[V1Node], list[Rejection]]:
    """Split nodes into (feasible, rejected-with-reason) for this pod."""
    feasible: list[V1Node] = []
    rejected: list[Rejection] = []
    for node in nodes:
        reason = rejection_reason(node, pod)
        if reason is None:
            feasible.append(node)
        else:
            rejected.append(Rejection(node=node_name(node), reason=reason))
    return feasible, rejected


def pick_node(feasible: list[V1Node], rng: random.Random | None = None) -> V1Node | None:
    """Pick one feasible node at random, or None if there are none."""
    if not feasible:
        return None
    return (rng or random).choice(feasible)


def summarize_rejections(total: int, rejected: list[Rejection]) -> str:
    """Build a kube-scheduler-style message: `0/4 nodes are available: 1 node ..., 3 ...`."""
    counts: dict[str, int] = {}
    for r in rejected:
        counts[r.reason] = counts.get(r.reason, 0) + 1
    parts = ", ".join(f"{n} {reason}" for reason, n in sorted(counts.items()))
    return f"0/{total} nodes are available" + (f": {parts}." if parts else ".")
