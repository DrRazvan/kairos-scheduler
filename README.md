# Kairos

A custom Kubernetes scheduler written in Python. It is built from scratch as a learning project
and modeled on the upstream kube-scheduler's Scheduling Framework.

> **Status:** Phase 1 (MVP) works. Kairos binds pods to a random Ready, schedulable node.
> Next up: Phase 2, real Filter/Score plugins. See the [roadmap](docs/PLAN.md).

## What it does (target)

Pods that set `spec.schedulerName: kairos-scheduler` are placed by Kairos instead of the
default scheduler. Kairos watches for those pods, filters out nodes that can't run them,
scores the rest with pluggable strategies (spread, bin-pack, load-aware, …), and binds each
pod to the best node.

## Quickstart (local cluster)

Prerequisites: Docker, [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation),
kubectl, [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
make cluster-up                                 # 1 control-plane + 3 worker nodes
kubectl get nodes --show-labels
kubectl apply -f deploy/examples/pod-kairos.yaml
kubectl get pod kairos-hello                    # Pending: nobody schedules it yet

make install && make run                        # start Kairos (Ctrl+C to stop)
kubectl get pod kairos-hello -o wide            # Running, on a random worker
kubectl describe pod kairos-hello               # Events: "Scheduled ... from kairos-scheduler"

kubectl apply -f deploy/examples/deployment-kairos.yaml   # 6 replicas, randomly spread
```

## Project layout

```
src/kairos/        scheduler source (grows phase by phase)
tests/             unit and e2e tests
deploy/kind/       local multi-node cluster config
deploy/examples/   sample workloads
docs/PLAN.md       roadmap + learning path
docs/notes/        your learning notes per phase
docs/adr/          architecture decision records
```
