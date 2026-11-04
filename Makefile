CLUSTER := kairos

.PHONY: install lint test run cluster-up cluster-down

install:        ## create venv and install dev deps
	uv sync

lint:           ## lint + type-check
	uv run ruff check . && uv run ruff format --check . && uv run mypy

test:           ## run unit tests
	uv run pytest

run:            ## run kairos locally against the current kube context
	uv run kairos

cluster-up:     ## local multi-node kind cluster
	kind create cluster --config deploy/kind/cluster.yaml

cluster-down:
	kind delete cluster --name $(CLUSTER)
