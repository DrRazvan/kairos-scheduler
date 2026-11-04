# Kairos — Implementation Plan & Learning Roadmap

> *Kairos* (Greek): "the right moment". A scheduler's job is to put each pod in the right place at the right moment.

This is a project that teaches as it goes. Each phase has three parts:

- **Learn**: the Kubernetes concepts you need first
- **Build**: what you write in this repo
- **Done when**: a concrete check that the phase is finished

## Starting point

The roadmap assumes some container operations experience (Docker, ideally Docker Swarm) and
working Python, but no prior Kubernetes knowledge. Phase 0 maps familiar Swarm concepts onto
their Kubernetes equivalents.

Don't skip Phase 0. Everything after it depends on understanding how the API server, the
scheduler and the kubelet talk to each other.

---

## The big picture (read this first)

When you run `kubectl apply -f pod.yaml`:

1. **kubectl** sends the Pod to the **API server**, which stores it in **etcd**.
2. The Pod has no `spec.nodeName`, so it is **Pending**.
3. The **scheduler** *watches* the API server for Pending pods. It picks one and runs:
   - **Filter**: which nodes *can* run this pod? (enough CPU/memory, taints, selectors…)
   - **Score**: of those, which node is *best*? (least loaded, bin-packing, cheapest…)
4. The scheduler **binds** the pod: it POSTs a `Binding` object, which sets `spec.nodeName`.
5. The **kubelet** on that node sees a pod assigned to it and starts the containers.

**The scheduler never starts containers.** It only answers one question: *"which node?"*
Kubernetes lets you run your own scheduler next to the default one. A pod picks a
scheduler by setting `spec.schedulerName`. That is how Kairos fits in.

```
            watch Pending pods (schedulerName=kairos-scheduler)
  ┌───────────┐  ─────────────────────────────────►  ┌──────────────────┐
  │ API server│                                      │  Kairos (Python) │
  │  + etcd   │  ◄─────────────────────────────────  │ filter → score   │
  └───────────┘     POST pods/<name>/binding         └──────────────────┘
        │
        │ pod.spec.nodeName = worker-2
        ▼
  ┌───────────┐
  │  kubelet  │  starts the containers
  └───────────┘
```

---

## Phase 0 — Kubernetes from zero (≈ 1 week, faster coming from Swarm)

**From Swarm to Kubernetes: a concept map**

| Docker Swarm | Kubernetes | Note |
|---|---|---|
| manager node | control plane (API server, etcd, scheduler, controller-manager) | In K8s these are separate components you can swap out. That is why a custom scheduler is possible. |
| worker node / `dockerd` | node / `kubelet` + container runtime | |
| task | **Pod** (1+ containers sharing network/IP) | |
| service (`replicas: 3`) | **Deployment** → ReplicaSet → Pods | |
| service VIP / routing mesh | **Service** (ClusterIP/NodePort) + kube-proxy | |
| `placement.constraints: node.labels.x==y` | `nodeSelector` / **node affinity** (required) | the Filter phase |
| `placement.preferences: spread` | **topologySpreadConstraints** / affinity (preferred) | the Score phase |
| `resources.reservations` | `resources.requests` | the scheduler only looks at *requests* |
| `resources.limits` | `resources.limits` | enforced by the kubelet/cgroups, not by the scheduler |
| `docker node update --availability drain` | `kubectl cordon` + `kubectl drain` | |
| `docker stack deploy -c compose.yml` | `kubectl apply -f` / Kustomize / **Helm** | |
| secrets/configs | Secret / ConfigMap | |
| *(no equivalent)* | **taints & tolerations**, PriorityClasses, preemption, RBAC, CRDs | new concepts, so focus here |
| *(no equivalent)* | **declarative API + watch + controllers** | the central K8s idea, and the one Kairos uses |

**The key mindset shift:** in Swarm, the manager does everything internally. In Kubernetes,
everything is an **object in the API**. Independent components *watch* objects and *reconcile*
them toward the desired state. The scheduler is just one of those watchers. So is Kairos.

**Learn**
- Containers vs Pods; what a Node is; control plane vs worker nodes
- `kubectl` basics: `get`, `describe`, `apply`, `delete`, `logs`, `-o yaml`, `--watch`
- Pods, Deployments, ReplicaSets, Namespaces
- **Labels and selectors** (the scheduler uses these constantly)
- **Resource requests & limits** (`cpu: 250m`, `memory: 128Mi`) and node *allocatable*
- **Taints & tolerations**, **nodeSelector**, **node affinity**
- **Events** (`kubectl get events`): how the scheduler reports what it did
- **The API itself**: `kubectl proxy` + `curl`, the REST paths, `?watch=true`, `resourceVersion`
- **RBAC**: ServiceAccount, ClusterRole, ClusterRoleBinding

**Build / exercises** (write notes in `docs/notes/phase-0.md`)
1. Install `kind` and create the 3-worker cluster: `make cluster-up`
2. Label the nodes, taint one, and deploy pods that use `nodeSelector` and `tolerations`.
   Predict where each pod lands *before* applying it.
3. Create a pod that requests more CPU than any node has. Read the `FailedScheduling` event.
4. Run `kubectl proxy`, then `curl` the nodes and pods. Then
   `curl "localhost:8001/api/v1/pods?watch=true"` and watch the event stream while you create pods.
5. **The "aha" exercise: be the scheduler by hand.**
   - Apply `deploy/examples/pod-kairos.yaml` (it sets `schedulerName: kairos-scheduler`).
   - It stays Pending forever, because nobody is scheduling it.
   - Bind it yourself with `curl` (POST a `Binding` to `/api/v1/namespaces/default/pods/<name>/binding`).
   - Watch it start running. **You just did what Kairos will automate.**

**Done when:** you can explain the five steps above and you have bound a pod by hand.

---

## Phase 1 — The dumbest scheduler that works (MVP)

**Learn**
- The official Python client (`kubernetes`): loading kubeconfig, `CoreV1Api`, `watch.Watch`
- `fieldSelector` (`spec.nodeName=`, `spec.schedulerName=`) to filter server-side
- Node conditions (`Ready`) and `spec.unschedulable` (what `kubectl cordon` sets)

**Build**
- `kairos` CLI entry point that connects to the cluster
- A loop: watch Pending pods with our `schedulerName`, choose a **random Ready, schedulable node**, bind it
- Emit a `Scheduled` Event on the pod (so it shows up in `kubectl describe pod`)
- Logs that say clearly what happened and why

**Done when:** `kubectl apply -f deploy/examples/pod-kairos.yaml` gets the pod Running,
with no manual steps.

---

## Phase 2 — Real filtering and scoring (the plugin framework)

**Learn**
- How kube-scheduler is structured: the **Scheduling Framework** and its *extension points*
  (PreFilter, Filter, PostFilter, PreScore, Score, NormalizeScore, Reserve, Permit, Bind, PostBind)
- How requests are summed per node; parsing quantities (`500m`, `1Gi`, `2`)

**Build**
- Your own small framework: a `Plugin` interface plus a pipeline that runs Filter plugins, then Score plugins
- Filter plugins: `NodeUnschedulable`, `NodeResourcesFit`, `TaintToleration`, `NodeSelector/NodeAffinity`
- Score plugins: `LeastAllocated` (spread), `MostAllocated` (bin-pack), `BalancedAllocation`
- Score normalization (0–100) and **per-plugin weights**
- A YAML config file that turns plugins on and off (similar to `KubeSchedulerConfiguration`)
- Unit tests for every plugin with fake nodes and pods (`pytest`). No cluster needed.

**Done when:** switching the config from spread to bin-pack visibly changes where pods land,
and every plugin has unit tests.

---

## Phase 3 — Doing it efficiently: cache, queue, retries

**Learn**
- **List + Watch** and why informers exist; `resourceVersion`; the `410 Gone` error and re-listing
- Why the scheduler keeps a **local cache** instead of asking the API every time
- **Optimistic "assume"**: the cache records the pod as placed before the bind call finishes
- **Scheduling queue**: active queue, backoff queue, and the unschedulable pool; exponential backoff
- Moving from sync to **asyncio** (`kubernetes_asyncio`): the scheduling cycle stays serial, binding runs concurrently

**Build**
- A node/pod cache kept up to date by watches
- A priority queue that respects `priorityClassName`, plus backoff for unschedulable pods
- Re-queueing pods when the cluster changes (a node is added, a pod is deleted)
- A `FailedScheduling` event with the reason, e.g. `0/3 nodes are available: 3 Insufficient cpu.`

**Done when:** creating 200 pods at once is handled smoothly, and pods that can't fit
are retried automatically when a new node joins.

---

## Phase 4 — Production-grade: run it *inside* the cluster

**Learn**
- In-cluster config, ServiceAccounts, least-privilege **RBAC**
- **Leader election** with `Lease` objects (coordination.k8s.io), so you can run 2+ replicas safely
- Liveness and readiness probes; graceful shutdown (SIGTERM)
- Prometheus metrics; Helm charts

**Build**
- `Dockerfile` (multi-stage, non-root, small image)
- RBAC manifests plus a Deployment (Kustomize), then a **Helm chart**
- Leader election (2 replicas, only one active)
- `/metrics`: scheduling latency histogram, attempts by result, queue depth
- A `ServiceMonitor` (kube-prometheus-stack), PrometheusRules alerts, and a Grafana dashboard.
- `/healthz` and `/readyz`; structured JSON logs

**Done when:** `helm install kairos ./charts/kairos` works on a fresh kind cluster. Killing the
leader pod causes failover within seconds.

---

## Phase 5 — Testing & CI like a real project

**Build**
- GitHub Actions: `ruff` lint, `mypy` type-check, unit tests on every push
- **e2e tests in CI**: spin up kind in GitHub Actions, deploy Kairos, assert where pods land
- Property-based tests (`hypothesis`) for resource math: a chosen node must never be over-committed

**Done when:** a green CI badge on the README, and a PR that breaks scheduling fails CI.

---

## Phase 6 — Advanced features (pick 2–3)

| Feature | What you learn | Extension point |
|---|---|---|
| **Gang scheduling**: schedule all N pods of a job, or none of them (ML/MPI workloads) | Permit plugins, waiting pods, timeouts | Permit |
| **Preemption**: evict lower-priority pods so a high-priority pod fits | PriorityClasses, PodDisruptionBudgets, graceful eviction | PostFilter |
| **Load-aware scoring**: score by *real* CPU usage from metrics-server or Prometheus, not just requests | metrics API, caching external data | Score |
| **Cost/carbon-aware scoring**: prefer spot or cheaper nodes, or greener regions, using node labels | custom labels/annotations, multi-objective scoring | Score |
| **Pod anti-affinity / topology spread** across zones | topology keys, `topologySpreadConstraints` | PreFilter/Filter/Score |
| **Scheduler Extender mode**: also serve as an HTTP extender for the *default* kube-scheduler | the extender API; comparing the two approaches | — |

---

## Phase 7 — Showcase

- **Simulator & benchmark**: run the scheduling pipeline against a synthetic cluster (no real K8s needed)
  and compare strategies: utilization, fragmentation, pending time. Produce charts.
- **Grafana dashboard** for the Prometheus metrics
- README with an architecture diagram, a demo GIF, and benchmark results
- A write-up or blog post: *"I built a Kubernetes scheduler in Python — here's what I learned"*

## Phase 8 (optional) — A real cluster with Terraform + Ansible

kind is great for development, but it hides how a cluster is actually put together:
- **Terraform** to create 3–4 VMs (a cloud free tier, or Proxmox/libvirt locally)
- **Ansible** roles to install a cluster with **kubeadm**: container runtime, kubelet, control plane, join workers, CNI
- Deploy Kairos with Helm and the monitoring stack on that cluster

It also covers most of the **CKA** (Certified Kubernetes Administrator) cluster-setup domain.

## Certification

Phases 0–4 plus Phase 8 cover a large part of the **CKA** curriculum (scheduling, RBAC, cluster
setup, troubleshooting). The project gives depth; the certification gives breadth.

---

## Tooling decisions (and why)

| Choice | Why |
|---|---|
| Python 3.12 | Makes the scheduling logic easy to read. |
| `kubernetes` client in Phase 1, then `kubernetes_asyncio` from Phase 3 | Start simple, then learn *why* async helps |
| GitHub + GitHub Actions | Public code and CI in one place |
| `kind` for local clusters | Multi-node in seconds, and the same tool runs in GitHub Actions CI. (minikube with `--nodes 4` also works.) |
| `uv` for dependencies | Fast, modern, one tool for venv, lockfile and running commands |
| `pytest`, `ruff`, `mypy` | Standard Python quality stack |

## Reference material

- Kubernetes docs — Scheduling Framework: https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
- Configure multiple schedulers: https://kubernetes.io/docs/tasks/extend-kubernetes/configure-multiple-schedulers/
- kube-scheduler source (Go, for reading only): https://github.com/kubernetes/kubernetes/tree/master/pkg/scheduler
- scheduler-plugins (coscheduling, load-aware, …): https://github.com/kubernetes-sigs/scheduler-plugins
