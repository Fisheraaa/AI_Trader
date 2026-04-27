import subprocess
import time
import datetime
import json
import os
from pathlib import Path

STATE_FILE = Path("data/scheduler_state.json")
LOG_DIR = Path("data/logs")
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

HEARTBEAT_SEC = int(os.getenv("SCHEDULER_HEARTBEAT_SEC", "15"))

# Intervals (seconds)
DATA_HOURLY_INTERVAL = int(os.getenv("DATA_INTERVAL_SEC", "3600"))   # DataManager2.py hourly_only
AT_INTERVAL = int(os.getenv("AT_INTERVAL_SEC", "1800"))              # AT3.py

# Daily full sync time (Beijing time)
DAILY_FULL_HOUR = int(os.getenv("DAILY_FULL_HOUR", "16"))
DAILY_FULL_MIN = int(os.getenv("DAILY_FULL_MIN", "10"))

# Anti-reentry/timeout
DM_HOURLY_TIMEOUT_SEC = int(os.getenv("DM_HOURLY_TIMEOUT_SEC", "5400"))   # 1.5h
DM_FULL_TIMEOUT_SEC = int(os.getenv("DM_FULL_TIMEOUT_SEC", "7200"))       # 2h
AT_TIMEOUT_SEC = int(os.getenv("AT_TIMEOUT_SEC", "1200"))                 # 20min


def now_dt():
    return datetime.datetime.now()


def now_str():
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(s: str):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


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


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def is_pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def terminate_pid(pid: int):
    try:
        os.kill(pid, 15)
    except Exception:
        pass


def clean_stale_running(state):
    running = state.get("running", {})
    to_del = []
    for task_name, info in running.items():
        pid = int(info.get("pid", 0) or 0)
        if pid <= 0 or not is_pid_alive(pid):
            to_del.append(task_name)

    for t in to_del:
        running.pop(t, None)

    if to_del:
        state["heartbeat"] = now_str()
        save_state(state)


def is_task_running(task_name: str):
    state = load_state()
    clean_stale_running(state)
    state = load_state()
    info = state.get("running", {}).get(task_name)
    if not info:
        return False, None
    pid = int(info.get("pid", 0) or 0)
    if pid > 0 and is_pid_alive(pid):
        return True, info
    return False, None


def is_task_timed_out(task_info: dict, timeout_sec: int):
    st = parse_dt(task_info.get("start", ""))
    if st is None:
        return False
    return (now_dt() - st).total_seconds() > timeout_sec


def run_script_blocking(task_name: str, script_cmd: str, timeout_sec: int):
    state = load_state()
    clean_stale_running(state)
    state = load_state()

    already_running, info = is_task_running(task_name)
    if already_running:
        print(f"[{now_str()}] ⏭️ Skip {task_name}: already running pid={info.get('pid')}")
        return False

    log_file = LOG_DIR / f"{task_name}.log"
    cmd = f"python3 {script_cmd}"

    try:
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"\n\n===== [{now_str()}] START {task_name}: {cmd} =====\n")
            proc = subprocess.Popen(
                ["bash", "-lc", cmd],
                cwd="/app",
                stdout=lf,
                stderr=lf
            )

            state = load_state()
            state["running"][task_name] = {
                "start": now_str(),
                "cmd": script_cmd,
                "pid": proc.pid
            }
            state["heartbeat"] = now_str()
            save_state(state)

            try:
                ret = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                lf.write(f"===== [{now_str()}] TIMEOUT {task_name}, kill pid={proc.pid} =====\n")
                try:
                    proc.terminate()
                    time.sleep(2)
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass
                ret = 124

            lf.write(f"===== [{now_str()}] END {task_name}, ret={ret} =====\n")

        state = load_state()
        state["running"].pop(task_name, None)
        if ret == 0:
            state["last_ok"][task_name] = now_str()
            state["last_error"].pop(task_name, None)
        else:
            state["last_error"][task_name] = f"{now_str()} | exit_code={ret}"
        state["heartbeat"] = now_str()
        save_state(state)
        return ret == 0

    except Exception as e:
        state = load_state()
        state["running"].pop(task_name, None)
        state["last_error"][task_name] = f"{now_str()} | exception={repr(e)}"
        state["heartbeat"] = now_str()
        save_state(state)
        return False


def should_run_by_interval(last_run_str: str, interval_sec: int):
    if not last_run_str:
        return True
    last_dt = parse_dt(last_run_str)
    if last_dt is None:
        return True
    return (now_dt() - last_dt).total_seconds() >= interval_sec


def should_run_daily_full(last_date_str: str, target_hour: int, target_min: int):
    now = now_dt()
    today = now.strftime("%Y-%m-%d")
    if last_date_str == today:
        return False
    target = now.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
    return now >= target


def any_data_task_running():
    r1, _ = is_task_running("DataManager_hourly")
    r2, _ = is_task_running("DataManager_full")
    return r1 or r2


def protect_running_timeouts():
    state = load_state()
    clean_stale_running(state)
    state = load_state()

    running = state.get("running", {})
    changed = False

    info = running.get("DataManager_hourly")
    if info and is_task_timed_out(info, DM_HOURLY_TIMEOUT_SEC):
        pid = int(info.get("pid", 0) or 0)
        if pid > 0:
            print(f"[{now_str()}] ⚠️ DataManager_hourly timeout, terminate pid={pid}")
            terminate_pid(pid)
        running.pop("DataManager_hourly", None)
        state["last_error"]["DataManager_hourly"] = f"{now_str()} | timeout>{DM_HOURLY_TIMEOUT_SEC}s"
        changed = True

    info = running.get("DataManager_full")
    if info and is_task_timed_out(info, DM_FULL_TIMEOUT_SEC):
        pid = int(info.get("pid", 0) or 0)
        if pid > 0:
            print(f"[{now_str()}] ⚠️ DataManager_full timeout, terminate pid={pid}")
            terminate_pid(pid)
        running.pop("DataManager_full", None)
        state["last_error"]["DataManager_full"] = f"{now_str()} | timeout>{DM_FULL_TIMEOUT_SEC}s"
        changed = True

    info = running.get("AT3")
    if info and is_task_timed_out(info, AT_TIMEOUT_SEC):
        pid = int(info.get("pid", 0) or 0)
        if pid > 0:
            print(f"[{now_str()}] ⚠️ AT3 timeout, terminate pid={pid}")
            terminate_pid(pid)
        running.pop("AT3", None)
        state["last_error"]["AT3"] = f"{now_str()} | timeout>{AT_TIMEOUT_SEC}s"
        changed = True

    if changed:
        state["running"] = running
        state["heartbeat"] = now_str()
        save_state(state)


def scheduler_loop():
    print(f"[{now_str()}] 🚀 Scheduler started (anti-reentry enabled)")
    print(f"[{now_str()}] config: DATA_HOURLY_INTERVAL={DATA_HOURLY_INTERVAL}, AT_INTERVAL={AT_INTERVAL}, "
          f"DAILY_FULL={DAILY_FULL_HOUR:02d}:{DAILY_FULL_MIN:02d}")

    while True:
        st = load_state()
        st["heartbeat"] = now_str()
        save_state(st)

        protect_running_timeouts()

        data_running = any_data_task_running()

        st = load_state()
        if (not data_running) and should_run_daily_full(st.get("last_data_full_run_date", ""), DAILY_FULL_HOUR, DAILY_FULL_MIN):
            print(f"[{now_str()}] ▶ RUN DataManager_full")
            ok = run_script_blocking("DataManager_full", "DataManager2.py full", DM_FULL_TIMEOUT_SEC)
            st = load_state()
            if ok:
                st["last_data_full_run_date"] = now_dt().strftime("%Y-%m-%d")
            st["last_data_hourly_run"] = now_str()
            st["heartbeat"] = now_str()
            save_state(st)

        st = load_state()
        data_running = any_data_task_running()
        if (not data_running) and should_run_by_interval(st.get("last_data_hourly_run", ""), DATA_HOURLY_INTERVAL):
            print(f"[{now_str()}] ▶ RUN DataManager_hourly")
            run_script_blocking("DataManager_hourly", "DataManager2.py hourly_only", DM_HOURLY_TIMEOUT_SEC)
            st = load_state()
            st["last_data_hourly_run"] = now_str()
            st["heartbeat"] = now_str()
            save_state(st)
        elif data_running:
            print(f"[{now_str()}] ⏭️ skip DataManager_hourly (data task running)")

        st = load_state()
        at_running, _ = is_task_running("AT3")
        data_running = any_data_task_running()
        if (not at_running) and (not data_running) and should_run_by_interval(st.get("last_at_run", ""), AT_INTERVAL):
            print(f"[{now_str()}] ▶ RUN AT3")
            run_script_blocking("AT3", "AT3.py", AT_TIMEOUT_SEC)
            st = load_state()
            st["last_at_run"] = now_str()
            st["heartbeat"] = now_str()
            save_state(st)
        elif data_running:
            print(f"[{now_str()}] ⏭️ skip AT3 (data task running)")
        elif at_running:
            print(f"[{now_str()}] ⏭️ skip AT3 (already running)")

        time.sleep(HEARTBEAT_SEC)


if __name__ == "__main__":
    scheduler_loop()