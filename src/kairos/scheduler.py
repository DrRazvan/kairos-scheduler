"""The scheduling loop: watch Pending pods addressed to us, pick a node, bind.

Loop shape (List + Watch, the core Kubernetes pattern):

    1. LIST pending pods with our schedulerName and schedule each one.
       The list response carries a `resourceVersion`: a bookmark in etcd's history.
    2. WATCH from that resourceVersion, so we get every change after the list
       and nothing is missed between the two calls.
    3. The watch ends after `resync_seconds` (or with 410 Gone if our bookmark
       got too old). Go back to 1. The re-list also retries pods that failed
       to schedule last time, for example because no node was free.

Phase 3 replaces this with a cache plus a proper scheduling queue with backoff.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from kubernetes import client, watch
from kubernetes.client import CoreV1Api, V1Pod
from kubernetes.client.exceptions import ApiException

from kairos.nodes import filter_nodes, node_name, pick_node, summarize_rejections

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PodRef:
    """The fields we need from a pod, validated once (API objects are all-Optional)."""

    namespace: str
    name: str
    uid: str

    @classmethod
    def of(cls, pod: V1Pod) -> PodRef:
        meta = pod.metadata
        if meta is None or not (meta.namespace and meta.name and meta.uid):
            raise ValueError("pod from the API is missing namespace/name/uid")
        return cls(namespace=meta.namespace, name=meta.name, uid=meta.uid)

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"


def needs_scheduling(pod: V1Pod) -> bool:
    """Unbound and not being deleted."""
    bound = bool(pod.spec and pod.spec.node_name)
    deleting = bool(pod.metadata and pod.metadata.deletion_timestamp)
    return not bound and not deleting


class Scheduler:
    def __init__(self, api: CoreV1Api, scheduler_name: str, resync_seconds: int = 60) -> None:
        self.api = api
        self.scheduler_name = scheduler_name
        self.resync_seconds = resync_seconds
        # Server-side filtering: the API server only sends us unbound pods that ask for us.
        self.field_selector = f"spec.schedulerName={scheduler_name},spec.nodeName="

    # ---- main loop ---------------------------------------------------------------

    def run(self) -> None:
        log.info("kairos started: scheduling pods with schedulerName=%s", self.scheduler_name)
        while True:
            resource_version = self._list_and_schedule()
            self._watch_and_schedule(resource_version)

    def _list_and_schedule(self) -> str | None:
        pods = self.api.list_pod_for_all_namespaces(field_selector=self.field_selector)
        log.debug("list: %d unscheduled pod(s)", len(pods.items))
        for pod in pods.items:
            self.schedule_one(pod)
        return pods.metadata.resource_version if pods.metadata else None

    def _watch_and_schedule(self, resource_version: str | None) -> None:
        w = watch.Watch()
        try:
            for event in w.stream(
                self.api.list_pod_for_all_namespaces,
                field_selector=self.field_selector,
                resource_version=resource_version,
                timeout_seconds=self.resync_seconds,
            ):
                if event["type"] in ("ADDED", "MODIFIED"):
                    self.schedule_one(event["object"])
        except ApiException as e:
            if e.status != 410:  # 410 Gone = bookmark expired; a re-list fixes it.
                raise
            log.info("watch expired (410 Gone), re-listing")
        finally:
            w.stop()

    # ---- one scheduling attempt ----------------------------------------------------

    def schedule_one(self, pod: V1Pod) -> None:
        if not needs_scheduling(pod):
            return
        ref = PodRef.of(pod)

        nodes = self.api.list_node().items
        feasible, rejected = filter_nodes(nodes, pod)
        node = pick_node(feasible)

        if node is None:
            message = summarize_rejections(len(nodes), rejected)
            log.warning("cannot schedule %s: %s", ref, message)
            self._emit_event(ref, "Warning", "FailedScheduling", message)
            return

        target = node_name(node)
        try:
            self._bind(ref, target)
        except ApiException as e:
            if e.status == 409:  # someone (or a previous attempt of ours) bound it already
                log.info("%s already bound, skipping", ref)
                return
            log.error("binding %s to %s failed: %s", ref, target, e.reason)
            self._emit_event(ref, "Warning", "FailedScheduling", f"Binding rejected: {e.reason}")
            return

        log.info(
            "bound %s -> %s (random pick from %d feasible node(s))", ref, target, len(feasible)
        )
        self._emit_event(ref, "Normal", "Scheduled", f"Successfully assigned {ref} to {target}")

    def _bind(self, ref: PodRef, target: str) -> None:
        """POST /api/v1/namespaces/{ns}/bindings. This sets spec.nodeName."""
        body = client.V1Binding(
            # The uid guards against binding a *new* pod that reused the same name.
            metadata=client.V1ObjectMeta(name=ref.name, uid=ref.uid),
            target=client.V1ObjectReference(kind="Node", api_version="v1", name=target),
        )
        # _preload_content=False: the Python client fails to deserialize the Binding
        # response (known upstream bug). The bind itself succeeds, and we don't need the body.
        self.api.create_namespaced_binding(
            namespace=ref.namespace, body=body, _preload_content=False
        )

    def _emit_event(self, ref: PodRef, type_: str, reason: str, message: str) -> None:
        """Create an Event so the result shows up in `kubectl describe pod`."""
        now = datetime.now(UTC)
        event = client.CoreV1Event(
            metadata=client.V1ObjectMeta(generate_name=f"{ref.name}."),
            involved_object=client.V1ObjectReference(
                kind="Pod", api_version="v1", namespace=ref.namespace, name=ref.name, uid=ref.uid
            ),
            type=type_,
            reason=reason,
            message=message,
            source=client.V1EventSource(component=self.scheduler_name),
            reporting_component=self.scheduler_name,
            reporting_instance=self.scheduler_name,
            first_timestamp=now,
            last_timestamp=now,
            count=1,
        )
        try:
            self.api.create_namespaced_event(namespace=ref.namespace, body=event)
        except ApiException as e:  # events are best-effort; never fail scheduling over one
            log.warning("could not emit %s event for %s: %s", reason, ref, e.reason)
