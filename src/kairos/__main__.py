"""Entry point: `kairos` / `python -m kairos`. The scheduling loop arrives in Phase 1."""

from kairos import __version__


def main() -> None:
    print(f"kairos {__version__}: nothing scheduled yet, see docs/PLAN.md (Phase 1)")


if __name__ == "__main__":
    main()
