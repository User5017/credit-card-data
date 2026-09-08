"""Task 8 check: were the last scheduled refresh runs green for every source?

For each recent run of the refresh workflow (scheduled by default; --event push for the push-triggered ones) this
finds the bot commit the run pushed (the 'refresh: <stamp>' commit whose health.json generated_at falls inside the
run's window), reads every source's status from that health.json, and says whether the newest N consecutive
scheduled runs were all green. A run that committed nothing, failed before committing, or has a source that is not
'ok' breaks the streak.

Needs `gh` (authenticated) and the repo's git history (fetch first: the bot commits live on origin/main).

    uv run python checks/verify_releases.py            # the last 5 scheduled runs, verdict on the newest 2
    uv run python checks/verify_releases.py --event push --limit 8
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys

OWNER_REPO = "User5017/credit-card-data"
WORKFLOW = "refresh.yml"
GREEN = "ok"


def sh(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def runs(event: str, limit: int) -> list[dict]:
    out = sh(
        "gh", "api", f"repos/{OWNER_REPO}/actions/workflows/{WORKFLOW}/runs",
        "-X", "GET", "-f", f"event={event}", "-f", f"per_page={limit}",
    )
    return json.loads(out)["workflow_runs"]


def bot_commits(ref: str) -> list[tuple[str, dt.datetime, dict | None]]:
    """(sha, committer time, health.json at that commit) for every 'refresh:' commit on `ref`, newest first."""
    log = sh("git", "log", ref, "--format=%H %cI %s", "--committer=carddash-bot", "-n", "200")
    out = []
    for line in log.splitlines():
        sha, when, *msg = line.split(" ", 2)
        if not msg or not msg[0].startswith("refresh:"):
            continue
        try:
            health = json.loads(sh("git", "show", f"{sha}:data/health.json"))
        except subprocess.CalledProcessError:
            health = None
        out.append((sha, dt.datetime.fromisoformat(when), health))
    return out


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def match(run: dict, commits) -> tuple[str | None, dict | None]:
    """The bot commit whose health.json generated_at lies inside the run's window, if any."""
    start, end = _ts(run["created_at"]), _ts(run["updated_at"]) + dt.timedelta(minutes=5)
    for sha, _when, health in commits:
        if not health or not health.get("generated_at"):
            continue
        stamp = _ts(health["generated_at"])
        if start <= stamp <= end:
            return sha, health
    return None, None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--event", default="schedule", help="workflow trigger to inspect (schedule, push, workflow_dispatch)")
    p.add_argument("--limit", type=int, default=5, help="how many recent runs to list")
    p.add_argument("--need", type=int, default=2, help="how many consecutive newest runs must be all green")
    p.add_argument("--ref", default="origin/main", help="git ref carrying the bot commits")
    args = p.parse_args(argv)

    commits = bot_commits(args.ref)
    rows = []
    for run in runs(args.event, args.limit):
        sha, health = match(run, commits)
        statuses = {s: d.get("status") for s, d in (health or {}).get("sources", {}).items()}
        all_green = bool(statuses) and all(v == GREEN for v in statuses.values()) and run["conclusion"] == "success"
        rows.append((run, sha, statuses, all_green))
        stamp = run["created_at"][:16].replace("T", " ")
        summary = ", ".join(f"{s}={v}" for s, v in sorted(statuses.items())) or "no bot commit found"
        print(f"{run['id']}  {stamp} UTC  {run['event']:9s} {run['conclusion'] or run['status']:9s} "
              f"{(sha or '')[:7]:7s} {'GREEN' if all_green else 'not green'}  {summary}")
    if not rows:
        print(f"no {args.event} runs of {WORKFLOW} yet")
        return 1
    newest = rows[: args.need]
    ok = len(newest) == args.need and all(r[3] for r in newest)
    print(
        f"\n{'PASS' if ok else 'NOT YET'}: newest {args.need} {args.event} run(s) all green with every source ok"
        + ("" if ok else f" (have {sum(1 for r in newest if r[3])} of {args.need})")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
