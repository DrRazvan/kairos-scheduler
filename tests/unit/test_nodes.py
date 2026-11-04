import random

from kubernetes.client import (
    V1Node,
    V1NodeCondition,
    V1NodeSpec,
    V1NodeStatus,
    V1ObjectMeta,
    V1Taint,
)

from kairos.nodes import filter_nodes, is_ready, pick_node, summarize_rejections


def make_node(
    name: str,
    ready: bool = True,
    unschedulable: bool = False,
    taints: list[V1Taint] | None = None,
) -> V1Node:
    return V1Node(
        metadata=V1ObjectMeta(name=name),
        spec=V1NodeSpec(unschedulable=unschedulable, taints=taints),
        status=V1NodeStatus(
            conditions=[V1NodeCondition(type="Ready", status="True" if ready else "False")]
        ),
    )


def test_is_ready() -> None:
    assert is_ready(make_node("a"))
    assert not is_ready(make_node("a", ready=False))
    assert not is_ready(V1Node(metadata=V1ObjectMeta(name="no-status")))


def test_filter_keeps_only_healthy_untainted_nodes() -> None:
    nodes = [
        make_node("ok"),
        make_node("not-ready", ready=False),
        make_node("cordoned", unschedulable=True),
        make_node(
            "control-plane",
            taints=[V1Taint(key="node-role.kubernetes.io/control-plane", effect="NoSchedule")],
        ),
        make_node("soft-taint", taints=[V1Taint(key="x", effect="PreferNoSchedule")]),
    ]
    feasible, rejected = filter_nodes(nodes)
    assert [n.metadata.name for n in feasible] == ["ok", "soft-taint"]
    assert {r.node: r.reason for r in rejected} == {
        "not-ready": "node(s) were not Ready",
        "cordoned": "node(s) were unschedulable",
        "control-plane": (
            "node(s) had untolerated taint {node-role.kubernetes.io/control-plane: NoSchedule}"
        ),
    }


def test_pick_node() -> None:
    nodes = [make_node("a"), make_node("b"), make_node("c")]
    assert pick_node([]) is None
    picked = {pick_node(nodes, random.Random(seed)).metadata.name for seed in range(50)}
    assert picked == {"a", "b", "c"}  # random, but every node gets chosen eventually


def test_summarize_rejections_looks_like_kube_scheduler() -> None:
    _, rejected = filter_nodes(
        [
            make_node("a", unschedulable=True),
            make_node("b", unschedulable=True),
            make_node("c", ready=False),
        ]
    )
    assert summarize_rejections(3, rejected) == (
        "0/3 nodes are available: 1 node(s) were not Ready, 2 node(s) were unschedulable."
    )
