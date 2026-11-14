#!/usr/bin/env python3
"""
CUBE full pipeline — one command for the hourly scheduled action.

1. run_1y_backtest results are read (not regenerated): backtests take minutes and
   are refreshed on demand by scripts/run_1y_backtest.py
2. 10 agents run against live data -> data/cube_report.json
3. feed posts -> data/agent_feed.json
4. dashboard HTML -> cube.html / feed.html
5. live portfolio sync -> data|docs/live_portfolio.json
6. HTML copied into the widgets/us-quant-dashboard bundle

Exit code is non-zero if any stage fails, so the scheduled action can see it.
"""
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDGET_BUNDLE = os.path.abspath(os.path.join(BASE_DIR, "..", "widgets", "us-quant-dashboard", "bundle"))


def run(name, cmd, cwd=BASE_DIR):
    print(f"\n=== {name}\n$ {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    out = (p.stdout or "").strip().splitlines()
    err = (p.stderr or "").strip().splitlines()
    for line in out[-12:]:
        print("  " + line)
    if p.returncode != 0:
        print("  STDERR:\n" + "\n".join(err[-15:]))
        return False
    return True


def sync_widget() -> bool:
    if not os.path.isdir(WIDGET_BUNDLE):
        print(f"  widget bundle 없음: {WIDGET_BUNDLE}")
        return True
    for src, dst in (("cube.html", "index.html"),):
        shutil.copyfile(os.path.join(BASE_DIR, src), os.path.join(WIDGET_BUNDLE, dst))
    cube = os.path.join(WIDGET_BUNDLE, "index.html")
    print(f"  synced → {cube} ({os.path.getsize(cube):,} bytes)")
    return True


def main():
    stages = [
        ("1/5 live portfolio sync", [sys.executable, "scripts/sync_live_portfolio.py"]),
        ("2/5 10-agent pipeline + feed", [sys.executable, "scripts/update_feed.py"]),
        ("3/5 dashboard build", [sys.executable, "scripts/build_full_cube.py"]),
    ]
    ok = True
    for name, cmd in stages:
        ok = run(name, cmd) and ok
    print("\n=== 4/5 widget sync")
    ok = sync_widget() and ok
    print("\n=== 5/5 summary")
    import json
    try:
        rep = json.load(open(os.path.join(BASE_DIR, "data", "cube_report.json"), encoding="utf-8"))
        feed = json.load(open(os.path.join(BASE_DIR, "data", "agent_feed.json"), encoding="utf-8"))
        print(f"  phase {rep.get('phase')} | agents {rep.get('agents_ok')}/{rep.get('total_agents')} | "
              f"posts {feed.get('total_posts')} | next open {rep.get('next_open_kst')}")
    except Exception as e:
        print(f"  summary read failed: {e}")
        ok = False
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
