"""Entry point: `kairos` / `python -m kairos`."""

from __future__ import annotations

import argparse
import logging

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from kairos import __version__
from kairos.scheduler import Scheduler

DEFAULT_SCHEDULER_NAME = "kairos-scheduler"


def load_kube_config(kubeconfig: str | None) -> str:
    """In-cluster config when running as a pod, otherwise ~/.kube/config (or --kubeconfig)."""
    if kubeconfig is None:
        try:
            config.load_incluster_config()
            return "in-cluster"
        except ConfigException:
            pass
    config.load_kube_config(config_file=kubeconfig)
    return kubeconfig or "~/.kube/config"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kairos", description=__doc__)
    parser.add_argument("--scheduler-name", default=DEFAULT_SCHEDULER_NAME)
    parser.add_argument("--kubeconfig", default=None, help="path to kubeconfig (default: auto)")
    parser.add_argument("--resync-seconds", type=int, default=60)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--version", action="version", version=f"kairos {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    source = load_kube_config(args.kubeconfig)
    logging.getLogger("kairos").info("kairos %s, kube config: %s", __version__, source)

    scheduler = Scheduler(
        client.CoreV1Api(), args.scheduler_name, resync_seconds=args.resync_seconds
    )
    try:
        scheduler.run()
    except KeyboardInterrupt:
        logging.getLogger("kairos").info("shutting down")


if __name__ == "__main__":
    main()
