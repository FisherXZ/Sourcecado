"""Operator commands for the preview channel: is it safe to update, and go back.

Two subcommands, both read-only about the run store.

`status` answers the question an operator actually has before quitting the app:
is anything in flight that an update would interrupt? It reports the same
assessment the updater's own gate uses, and it writes nothing -- in particular
it never quarantines or settles an external effect.

`rollback` is the manual half of criterion 4. A failed update rolls itself back;
this is for the update that installed cleanly and turned out to be wrong.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from coworker import migrations
from coworker.update_channel.apply import (
    Installation,
    UpdateStatus,
    rollback,
    running_identity,
)
from coworker.update_channel.drain import DrainStatus, assess_drain

RUN_STORE_NAME = "agent_runs.db"


def _installation(args: argparse.Namespace) -> Installation:
    return Installation(
        identity=running_identity(channel=args.channel, version=args.version),
        bundle_path=Path(args.bundle).expanduser(),
        state_root=Path(args.state_root).expanduser(),
    )


def status(args: argparse.Namespace) -> int:
    root = Path(args.state_root).expanduser()
    print(f"channel   {args.channel}")
    print(f"version   {args.version}")
    if not (root / RUN_STORE_NAME).exists():
        print("runs      no run store on this machine")
        print("update    safe to update")
        return 0

    from coworker.agent_run_repository import AgentRunRepository

    repo = AgentRunRepository(root)
    try:
        assessment = assess_drain(repo)
    finally:
        repo.close()

    print(f"drain     {assessment.status}")
    for blocker in assessment.blockers:
        detail = f" ({blocker.tool_name})" if blocker.tool_name else ""
        print(f"  {blocker.run_id}{detail}: {blocker.reason}")
    if assessment.continuable:
        print(f"continue  {len(assessment.continuable)} run(s) resume after a restart")
    if assessment.status is DrainStatus.READY:
        print("update    safe to update")
        return 0
    if assessment.status is DrainStatus.QUARANTINED_EFFECT:
        print(
            "update    settle the quarantined effect in review first. "
            "Sourcecado will not decide it for you."
        )
        return 1
    print("update    wait for the work above to finish, then try again")
    return 1


def undo(args: argparse.Namespace) -> int:
    installation = _installation(args)
    if args.list_backups:
        for entry in migrations.list_backups(installation.state_root):
            print(f"{entry['backup_id']}  {entry['created_at']}  {entry['reason']}")
        return 0
    outcome = rollback(installation, backup_id=args.backup)
    print(f"{outcome.status}: {outcome.guidance}")
    if outcome.restored:
        print(f"restored  {', '.join(outcome.restored)}")
    return 0 if outcome.status is UpdateStatus.ROLLED_BACK else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m coworker.update_channel")
    parser.add_argument("--state-root", default=str(migrations.state_root()))
    parser.add_argument("--channel", default="preview")
    parser.add_argument("--version", default="0.0.0")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("status", help="is it safe to update right now")
    check.set_defaults(handler=status)

    back = sub.add_parser("rollback", help="go back to the previous version")
    back.add_argument("--bundle", default="/Applications/Sourcecado.app")
    back.add_argument("--backup", default=None, help="state backup id to restore")
    back.add_argument(
        "--list-backups", action="store_true", help="list backups and exit"
    )
    back.set_defaults(handler=undo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
