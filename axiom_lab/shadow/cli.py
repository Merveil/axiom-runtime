"""
Axiom Shadow CLI — offline regression checker.

Invoked as:
    python -m axiom_lab.shadow check-regressions \\
        --store  build/shadow.db               \\
        --target http://localhost:8000         \\
        --limit  100                           \\
        [--rules  axiom_lab/rules/api_demo.json] \\
        [--output build/shadow_report.json]    \\
        [--regression-threshold 5.0]

Registered as the 'rrt' script entry point in pyproject.toml.

Exit codes
----------
    0  — regression_rate <= threshold (or store is empty)
    1  — store is empty, nothing to replay
    2  — regression_rate > threshold  (CI fail gate)
"""
from __future__ import annotations

import argparse
import sys

import httpx

from axiom_lab.shadow.event_store import ShadowEventStore
from axiom_lab.shadow.replay_runner import check_regressions, run_shadow_campaign, store_inspection


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_check_regressions(args: argparse.Namespace) -> int:
    store = ShadowEventStore(path=args.store)
    n = store.count()
    if n == 0:
        print(
            f"shadow: store '{args.store}' is empty — nothing to replay.",
            file=sys.stderr,
        )
        return 1

    print(
        f"shadow: {n} captured events  "
        f"replaying up to {args.limit} against {args.target} …"
    )

    client = httpx.Client(base_url=args.target, timeout=args.timeout)
    try:
        report = check_regressions(
            store,
            client,
            name=args.name,
            limit=args.limit,
            rules_path=args.rules,
        )
    finally:
        client.close()

    print(report.summary_table())

    if args.output:
        report.save(args.output)
        print(f"\nshadow: report saved → {args.output}")

    if report.regression_rate_pct > args.regression_threshold:
        print(
            f"\n⚠  regression_rate={report.regression_rate_pct:.2f}% "
            f"exceeds threshold={args.regression_threshold}%  [CI FAIL]",
            file=sys.stderr,
        )
        return 2

    return 0


def _cmd_shadow_report(args: argparse.Namespace) -> int:
    store = ShadowEventStore(path=args.store)
    n = store.count()
    if n == 0:
        print(
            f"shadow: store '{args.store}' is empty.",
            file=sys.stderr,
        )
        return 1

    report = store_inspection(store, since_hours=args.since_hours)
    print(report.summary_table())

    if args.output:
        import json
        from pathlib import Path
        p = Path(args.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report.to_dict(), indent=2))
        print(f"\nshadow: store report saved \u2192 {args.output}")

    return 0


def _cmd_shadow_campaign(args: argparse.Namespace) -> int:
    store = ShadowEventStore(path=args.store)
    n = store.count()
    if n == 0:
        print(
            f"shadow: store '{args.store}' is empty \u2014 nothing to replay.",
            file=sys.stderr,
        )
        return 1

    print(
        f"shadow: {n} captured events  "
        f"running {args.mode!r} campaign on last {args.last} against {args.target} \u2026"
    )

    client = httpx.Client(base_url=args.target, timeout=args.timeout)
    try:
        report = run_shadow_campaign(
            store,
            client,
            mode=args.mode,
            last=args.last,
            name=args.name,
            rules_path=args.rules,
        )
    finally:
        client.close()

    print(report.summary_table())

    if args.output:
        report.save(args.output)
        print(f"\nshadow: campaign report saved \u2192 {args.output}")

    if report.regression_rate_pct > args.regression_threshold:
        print(
            f"\n\u26a0  regression_rate={report.regression_rate_pct:.2f}% "
            f"exceeds threshold={args.regression_threshold}%  [CI FAIL]",
            file=sys.stderr,
        )
        return 2

    return 0


# ---------------------------------------------------------------------------
# Original command
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rrt",
        description="Axiom shadow replay — offline regression checker",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser(
        "check-regressions",
        help="Replay captured shadow events and report regressions",
    )
    p.add_argument(
        "--store",
        required=True,
        help="Path to the ShadowEventStore SQLite file",
    )
    p.add_argument(
        "--target",
        required=True,
        help="Base URL of the replay target  (e.g. http://localhost:8000)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum events to replay  [default: 100]",
    )
    p.add_argument(
        "--rules",
        default=None,
        metavar="PATH",
        help="JSON rules file to apply after the probe verdict  [optional]",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Save the JSON report to this path  [optional]",
    )
    p.add_argument(
        "--name",
        default="shadow",
        help="Report display name  [default: shadow]",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout per replay request in seconds  [default: 10]",
    )
    p.add_argument(
        "--regression-threshold",
        type=float,
        default=5.0,
        metavar="PCT",
        help=(
            "Exit with code 2 when regression_rate_pct exceeds this value "
            "[default: 5.0]"
        ),
    )
    # ── shadow-report ─────────────────────────────────────────────────────
    sr = sub.add_parser(
        "shadow-report",
        help="Inspect the shadow store without replaying (store snapshot)",
    )
    sr.add_argument(
        "--store",
        required=True,
        help="Path to the ShadowEventStore SQLite file",
    )
    sr.add_argument(
        "--since-hours",
        type=float,
        default=24.0,
        metavar="H",
        help="Time window for replay-candidate count in hours  [default: 24]",
    )
    sr.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Save the JSON store report to this path  [optional]",
    )

    # ── shadow-campaign ─────────────────────────────────────────────────
    sc = sub.add_parser(
        "shadow-campaign",
        help="Run a replay campaign on shadow-captured events",
    )
    sc.add_argument(
        "--store",
        required=True,
        help="Path to the ShadowEventStore SQLite file",
    )
    sc.add_argument(
        "--target",
        required=True,
        help="Base URL of the replay target (e.g. http://localhost:8000)",
    )
    sc.add_argument(
        "--mode",
        choices=["semantic", "strict"],
        default="semantic",
        help="Evaluation mode: 'semantic' (default) or 'strict'",
    )
    sc.add_argument(
        "--last",
        type=int,
        default=50,
        help="Number of most-recent events to replay  [default: 50]",
    )
    sc.add_argument(
        "--rules",
        default=None,
        metavar="PATH",
        help="JSON rules file to apply after the probe verdict  [optional]",
    )
    sc.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Save the JSON campaign report to this path  [optional]",
    )
    sc.add_argument(
        "--name",
        default="shadow-campaign",
        help="Report display name  [default: shadow-campaign]",
    )
    sc.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout per replay request in seconds  [default: 10]",
    )
    sc.add_argument(
        "--regression-threshold",
        type=float,
        default=5.0,
        metavar="PCT",
        help="Exit with code 2 when regression_rate_pct exceeds this value  [default: 5.0]",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args   = parser.parse_args(argv)

    if args.command == "check-regressions":
        return _cmd_check_regressions(args)
    if args.command == "shadow-report":
        return _cmd_shadow_report(args)
    if args.command == "shadow-campaign":
        return _cmd_shadow_campaign(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
