CLUSTER := kairos
IMAGE   := kairos-scheduler:dev

.PHONY: install lint test run cluster-up cluster-down image deploy undeploy logs

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

image:          ## build the image and side-load it into the kind cluster
	docker build -t $(IMAGE) .
	kind load docker-image $(IMAGE) --name $(CLUSTER)

deploy: image   ## run kairos as a pod in the cluster (restarts it to pick up the new image)
	kubectl apply -k deploy/kairos
	kubectl -n kairos-system rollout restart deployment/kairos-scheduler
	kubectl -n kairos-system rollout status deployment/kairos-scheduler

undeploy:
	kubectl delete -k deploy/kairos --ignore-not-found

logs:           ## follow the in-cluster scheduler's logs
	kubectl -n kairos-system logs -f deployment/kairos-scheduler
