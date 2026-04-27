#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import datetime
from pathlib import Path

STATE_FILE = Path("data/scheduler_state.json")

# 与 scheduler.py 对齐的超时配置（用于展示）
DM_HOURLY_TIMEOUT_SEC = int(os.getenv("DM_HOURLY_TIMEOUT_SEC", "5400"))
DM_FULL_TIMEOUT_SEC = int(os.getenv("DM_FULL_TIMEOUT_SEC", "7200"))
AT_TIMEOUT_SEC = int(os.getenv("AT_TIMEOUT_SEC", "1200"))


def now_dt():
    return datetime.datetime.now()


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def fmt_duration(seconds: float):
    if seconds is None:
        return "-"
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def is_pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def load_state():
    default = {
        "last_data_hourly_run": "",
        "last_at_run": "",
        "last_data_full_run_date": "",
        "last_ok": {},
        "last_error": {},
        "running": {},
        "heartbeat": "",
    }
    if not STATE_FILE.exists():
        return default
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return default
        for k, v in default.items():
            d.setdefault(k, v)
        return d
    except Exception:
        return default


def get_timeout_for_task(task_name: str):
    if task_name == "DataManager_hourly":
        return DM_HOURLY_TIMEOUT_SEC
    if task_name == "DataManager_full":
        return DM_FULL_TIMEOUT_SEC
    if task_name == "AT3":
        return AT_TIMEOUT_SEC
    return None


def print_header(title):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def print_kv(k, v):
    print(f"{k:<28}: {v}")


def main():
    state = load_state()
    now = now_dt()

    print_header("Scheduler Status (Enhanced)")
    print_kv("Now", now.strftime("%Y-%m-%d %H:%M:%S"))
    print_kv("State file", str(STATE_FILE.resolve()))
    print_kv("Heartbeat", state.get("heartbeat", "-") or "-")
    print_kv("Last data hourly run", state.get("last_data_hourly_run", "-") or "-")
    print_kv("Last data full run date", state.get("last_data_full_run_date", "-") or "-")
    print_kv("Last AT run", state.get("last_at_run", "-") or "-")

    # running tasks
    running = state.get("running", {}) or {}
    print_header("Running Tasks")
    if not running:
        print("No running tasks.")
    else:
        for task_name, info in running.items():
            start_s = info.get("start", "")
            cmd = info.get("cmd", "")
            pid = int(info.get("pid", 0) or 0)

            st = parse_dt(start_s)
            elapsed = (now - st).total_seconds() if st else None
            timeout_sec = get_timeout_for_task(task_name)
            alive = is_pid_alive(pid)
            timed_out = (elapsed is not None and timeout_sec is not None and elapsed > timeout_sec)

            print("-" * 80)
            print_kv("Task", task_name)
            print_kv("PID", pid if pid else "-")
            print_kv("PID alive", "YES" if alive else "NO")
            print_kv("Start", start_s or "-")
            print_kv("Elapsed", fmt_duration(elapsed))
            print_kv("Timeout", fmt_duration(timeout_sec) if timeout_sec else "-")
            print_kv("Over timeout", "YES" if timed_out else "NO")
            print_kv("Command", cmd or "-")

    # last ok
    print_header("Last OK")
    last_ok = state.get("last_ok", {}) or {}
    if not last_ok:
        print("No successful task records.")
    else:
        for k in sorted(last_ok.keys()):
            print_kv(k, last_ok[k])

    # last error
    print_header("Last Error")
    last_error = state.get("last_error", {}) or {}
    if not last_error:
        print("No error records.")
    else:
        for k in sorted(last_error.keys()):
            print_kv(k, last_error[k])

    # quick health hints
    print_header("Quick Hints")
    hb = parse_dt(state.get("heartbeat", ""))
    if hb is None:
        print("- Heartbeat missing: scheduler may not have started.")
    else:
        hb_gap = (now - hb).total_seconds()
        if hb_gap > 2 * int(os.getenv("SCHEDULER_HEARTBEAT_SEC", "15")) + 10:
            print(f"- Heartbeat stale ({fmt_duration(hb_gap)}): check scheduler process/logs.")
        else:
            print(f"- Heartbeat fresh ({fmt_duration(hb_gap)} ago).")

    # Check for stale running entries
    stale = []
    for task_name, info in running.items():
        pid = int(info.get("pid", 0) or 0)
        if pid and not is_pid_alive(pid):
            stale.append(task_name)
    if stale:
        print(f"- Stale running entries found: {', '.join(stale)}")
        print("  Suggestion: restart app container or let scheduler self-clean in next loop.")
    else:
        print("- No stale running entries detected.")

    print("\nDone.\n")


if __name__ == "__main__":
    main()