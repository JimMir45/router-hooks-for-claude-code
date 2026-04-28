#!/usr/bin/env python3
"""
Cleanup tool for ~/.claude/reports/.
Usage:
  python3 cleanup-reports.py            # delete the most recent report
  python3 cleanup-reports.py --all      # delete all *.html reports
  python3 cleanup-reports.py --list     # list reports
  python3 cleanup-reports.py --older 7  # delete reports older than 7 days
"""
import sys
import time
from pathlib import Path

REPORTS = Path.home() / ".claude" / "reports"


def list_reports():
    files = sorted(REPORTS.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("(no reports)")
        return
    print(f"{len(files)} report(s):")
    for f in files:
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime))
        size_kb = f.stat().st_size / 1024
        print(f"  {mtime}  {size_kb:5.1f} KB  {f.name}")


def main():
    args = sys.argv[1:]
    if not REPORTS.exists():
        print("(reports directory does not exist)")
        return

    if "--list" in args or "-l" in args:
        list_reports()
        return

    if "--all" in args:
        files = list(REPORTS.glob("*.html"))
        for f in files:
            f.unlink()
        print(f"Deleted {len(files)} report(s)")
        return

    if "--older" in args:
        idx = args.index("--older")
        days = int(args[idx + 1]) if idx + 1 < len(args) else 7
        cutoff = time.time() - days * 86400
        deleted = 0
        for f in REPORTS.glob("*.html"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        print(f"Deleted {deleted} report(s) older than {days} days")
        return

    # Default: delete the most recent report
    files = sorted(REPORTS.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("(no reports to delete)")
        return
    target = files[0]
    target.unlink()
    print(f"Deleted most recent report: {target.name}")


if __name__ == "__main__":
    main()
