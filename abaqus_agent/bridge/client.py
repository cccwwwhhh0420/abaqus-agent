# -*- coding: utf-8 -*-
"""Durable task client for the GUI event bridge and model state perception."""

import json
import os
import socket
import time
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple
import uuid

from abaqus_agent.bridge_server.bridge_protocol import atomic_json, cancel_queued_task, completed_result, ensure_dirs, read_json, valid_id

from abaqus_agent.config import (
    ABAQUS_COMMAND,
    ABAQUS_HOST,
    ABAQUS_PORT,
    ABAQUS_TIMEOUT,
    BASE_DIR,
    CONFIRM_BEFORE_EXEC,
    IPC_DIR,
    ROOT_DIR,
    STATE_DIR,
    WORKSPACE_DIR,
)


class AbaqusConnectionError(Exception):
    """Exception raised when connection to Abaqus bridge fails."""
    pass


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running on the system."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


def get_ipc_status(max_age_seconds: float = 10.0) -> Dict[str, Any]:
    """A fresh GUI heartbeat is evidence of a scheduler, not just a live CAE PID."""
    # Windows may transiently reject/open no file during ReplaceFile. Retry
    # briefly in this external client, never in a GUI event callback.
    for attempt in range(3):
        data = _read_ipc_status(max_age_seconds)
        if data.get("status") != "offline" or not data.get("read_error") or attempt == 2:
            return data
        time.sleep(0.01)
    return data


def _read_ipc_status(max_age_seconds: float) -> Dict[str, Any]:
    try:
        data = read_json(str(IPC_DIR / "status.json"))
        if not isinstance(data, dict):
            return {"status": "offline", "alive": False}
        age = time.time() - data.get("timestamp", 0)
        data["age"] = age
        data["alive"] = bool(
            data.get("protocol") == 2 and data.get("status") in ("idle", "blocked", "executing")
            and -1 <= age <= max_age_seconds and data.get("pid")
            and is_pid_alive(data["pid"])
            and Path(data.get("ipc_dir", "")).resolve() == IPC_DIR.resolve())
        return data
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "offline", "alive": False, "read_error": str(exc)}


def is_file_ipc_alive(max_age_seconds: float = 10.0) -> bool:
    return get_ipc_status(max_age_seconds).get("alive", False)


def describe_bridge_status(info: Optional[Dict[str, Any]] = None) -> str:
    """Keep transport connectivity distinct from scheduler readiness."""
    info = get_ipc_status() if info is None else info
    if not info.get("alive"):
        return "OFFLINE (Type /launch to start)"
    state = info.get("status", "unknown")
    label = {"blocked": "Blocked", "executing": "Executing", "idle": "Idle"}.get(state, state)
    reason = info.get("message", "")
    version = info.get("bridge_version")
    return f"ONLINE ({label})" + (f" [bridge {version}]" if version else "") + (f": {reason}" if reason else "")


def _notify_gui(info: Dict[str, Any], op: str, timeout: float = 2.0) -> bool:
    """Small control messages only; scripts remain in the durable file queue."""
    if info.get("transport") != "socket":
        return False
    request = {"op": op, "session_id": info["session_id"]}
    try:
        # The plugin deliberately exposes only a loopback control endpoint.
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout) as peer:
            peer.sendall((json.dumps(request) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf and len(buf) <= 4096:
                chunk = peer.recv(4096)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        return bool(reply.get("ok") and reply.get("session_id") == info["session_id"])
    except (OSError, ValueError, KeyError):
        return False


def cancel_pending(task_id: str, reason: str) -> bool:
    """Atomic competition with the kernel claim; only unclaimed tasks can cancel."""
    return cancel_queued_task(str(IPC_DIR), task_id, reason)


def get_task_result(task_id: str) -> Dict[str, Any]:
    """Read durable evidence without submitting another kernel command."""
    if not valid_id(task_id):
        return {"state": "invalid", "error": "Expected a 32-character hexadecimal task ID."}
    result = IPC_DIR / "results" / f"{task_id}.json"
    if result.is_file():
        data = completed_result(str(result), task_id)
        return data or {"id": task_id, "state": "unknown", "error": "Invalid completion record; do not retry."}
    if (IPC_DIR / "commands" / f"{task_id}.running.json").is_file():
        return {"id": task_id, "state": "running", "message": "Claimed; completion is not yet recorded."}
    if (IPC_DIR / "commands" / f"cmd_{task_id}.json").is_file():
        return {"id": task_id, "state": "queued"}
    return {"id": task_id, "state": "unknown", "message": "No durable result found. Do not assume failure."}


def ping(host: str = ABAQUS_HOST, port: int = ABAQUS_PORT, timeout: float = 5.0) -> bool:
    """Challenge the GUI transport. This does not prove the kernel is idle."""
    info = get_ipc_status()
    if not info.get("alive"):
        return False
    if info.get("transport") == "socket":
        return _notify_gui(info, "ping", timeout)
    ensure_dirs(str(IPC_DIR))
    task_id = uuid.uuid4().hex
    atomic_json(str(IPC_DIR / "commands" / f"cmd_{task_id}.json"), {
        "id": task_id, "type": "ping", "session_id": info["session_id"],
        "timestamp": time.time(), "expires_at": time.time() + timeout})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = get_task_result(task_id)
        if data.get("state") == "succeeded":
            return data.get("session_id") == info["session_id"] and data.get("output") == "pong"
        time.sleep(0.05)
    cancel_pending(task_id, "GUI transport probe timed out.")
    return False


def _read_execution_result(task_id: str) -> Optional[Tuple[bool, str]]:
    data = get_task_result(task_id)
    if data.get("state") not in ("succeeded", "failed", "cancelled"):
        return None
    output = data.get("output", "")
    if data.get("traceback"):
        output += "\n" + data["traceback"]
    if not data.get("success"):
        output = f"Task {task_id} ({data['state']}):\n" + output
    return bool(data.get("success")), output


def run_py_file_ipc(
    py_code: str, timeout: float = ABAQUS_TIMEOUT,
    confirm_enter: Optional[bool] = None
) -> Tuple[bool, str]:
    """Publish once, notify the GUI, and wait for the matching durable result."""
    from abaqus_agent.tools.api_precheck import precheck_script
    precheck_error = precheck_script(py_code)
    if precheck_error:
        return False, precheck_error
    ensure_dirs(str(IPC_DIR))
    for running in (IPC_DIR / "commands").glob("*.running.json"):
        task_id = running.name[:-13]
        if not completed_result(str(IPC_DIR / "results" / f"{task_id}.json"), task_id):
            return False, f"OUTCOME_UNKNOWN: Task {task_id} has no completion record. Inspect /task {task_id}; no new task submitted."
    if confirm_enter is None:
        confirm_enter = CONFIRM_BEFORE_EXEC
    if confirm_enter:
        try:
            input("[Abaqus-AI] Script ready. Press Enter to submit (Ctrl+C to cancel): ")
        except (KeyboardInterrupt, EOFError):
            return False, "Submission cancelled; no task published."
    info = get_ipc_status()
    if not info.get("alive"):
        return False, "GUI event bridge is offline. Load the Abaqus AI plugin at CAE startup or use /launch."
    task_id = uuid.uuid4().hex
    now = time.time()
    atomic_json(str(IPC_DIR / "commands" / f"cmd_{task_id}.json"), {
        "id": task_id, "type": "execute", "script": py_code,
        "session_id": info["session_id"], "timestamp": now, "expires_at": now + timeout})
    deadline = time.monotonic() + timeout
    if info.get("transport") == "socket" and not _notify_gui(info, "wake", min(2.0, timeout)):
        # An acknowledgement can be lost after the GUI received the notification.
        cancel_pending(task_id, "GUI notification was not acknowledged.")
    try:
        while time.monotonic() < deadline:
            result = _read_execution_result(task_id)
            if result is not None:
                return result
            time.sleep(0.05)
    except KeyboardInterrupt:
        cancel_pending(task_id, "Client wait interrupted.")
    else:
        latest = get_ipc_status()
        reason = f"Client wait timed out after {timeout}s."
        if (latest.get("alive") and latest.get("session_id") == info["session_id"]
                and latest.get("status") == "blocked"):
            reason += " BRIDGE_BLOCKED: " + latest.get("message", "Unknown GUI guard.")
        cancel_pending(task_id, reason)
    result = _read_execution_result(task_id)
    if result is not None:
        return result
    return False, f"OUTCOME_UNKNOWN: Task {task_id} was claimed, but completion is not confirmed. It may still be running or may have changed the model. Do not retry automatically. Inspect /task {task_id}."


def run_py(
    py_code: str, host: str = ABAQUS_HOST, port: int = ABAQUS_PORT,
    timeout: float = ABAQUS_TIMEOUT, confirm_enter: Optional[bool] = None
) -> Tuple[bool, str]:
    """Execute through the configured GUI session; no simulated menu input."""
    return run_py_file_ipc(py_code, timeout=timeout, confirm_enter=confirm_enter)


STATUS_SCRIPT = """
import json
import os

res = {}
try:
    from abaqus import mdb
    model_names = list(mdb.models.keys())
    res["models"] = model_names
    m = mdb.models[model_names[0]] if model_names else None
    if m:
        res["active_model"] = m.name
        res["parts"] = list(m.parts.keys())
        res["materials"] = list(m.materials.keys())
        res["sections"] = list(m.sections.keys())
        res["instances"] = list(m.rootAssembly.instances.keys())
        res["steps"] = list(m.steps.keys())
        res["loads"] = list(m.loads.keys())
        res["bcs"] = list(m.boundaryConditions.keys())
        res["jobs"] = list(mdb.jobs.keys())

        total_nodes = sum([len(p.nodes) for p in m.parts.values()])
        total_elems = sum([len(p.elements) for p in m.parts.values()])
        res["nodes_count"] = total_nodes
        res["elems_count"] = total_elems
        res["counts"] = {
            "parts": len(res["parts"]),
            "materials": len(res["materials"]),
            "sections": len(res["sections"]),
            "instances": len(res["instances"]),
            "steps": len(res["steps"]),
            "loads": len(res["loads"]),
            "bcs": len(res["bcs"]),
            "nodes": total_nodes,
            "elements": total_elems,
            "jobs": len(res["jobs"])
        }
except Exception as e:
    res["error"] = str(e)

print("__STATUS_JSON__" + json.dumps(res))
"""


def get_status(host: str = ABAQUS_HOST, port: int = ABAQUS_PORT, timeout: float = 30.0) -> Tuple[bool, Dict[str, Any]]:
    """Retrieve entity counts summary from active Abaqus session."""
    ok, res = run_py(STATUS_SCRIPT, host=host, port=port, timeout=timeout, confirm_enter=False)
    if not ok:
        return False, {"error": res}
    for line in res.splitlines():
        if line.startswith("__STATUS_JSON__"):
            try:
                return True, json.loads(line[15:])
            except Exception as e:
                return False, {"error": f"Failed to parse status JSON: {e}"}
    return False, {"raw_output": res}


SNAPSHOT_SCRIPT = """
import json

snap = {
    "counts": {},
    "models": [],
    "parts": [],
    "materials": [],
    "sections": [],
    "steps": [],
    "loads": [],
    "bcs": [],
    "jobs": []
}

try:
    from abaqus import mdb
    snap["models"] = list(mdb.models.keys())
    m = mdb.models[snap["models"][0]] if snap["models"] else None
    if m:
        for p_name, p in m.parts.items():
            snap["parts"].append({
                "name": p_name,
                "nodes": len(p.nodes),
                "elements": len(p.elements)
            })

        for mat_name, mat in m.materials.items():
            mat_info = {"name": mat_name}
            if hasattr(mat, 'elastic') and mat.elastic:
                mat_info["elastic"] = str(mat.elastic.table)
            if hasattr(mat, 'density') and mat.density:
                mat_info["density"] = str(mat.density.table)
            if hasattr(mat, 'plastic') and mat.plastic:
                mat_info["plastic"] = str(mat.plastic.table)
            snap["materials"].append(mat_info)

        for sec_name, sec in m.sections.items():
            sec_info = {"name": sec_name, "type": type(sec).__name__}
            if hasattr(sec, 'material'):
                sec_info["material"] = str(sec.material)
            if hasattr(sec, 'thickness'):
                sec_info["thickness"] = str(sec.thickness)
            snap["sections"].append(sec_info)

        for step_name, stp in m.steps.items():
            snap["steps"].append({
                "name": step_name,
                "type": type(stp).__name__,
                "timePeriod": getattr(stp, 'timePeriod', None)
            })

        for load_name, ld in m.loads.items():
            snap["loads"].append({
                "name": load_name,
                "type": type(ld).__name__
            })

        for bc_name, bc in m.boundaryConditions.items():
            snap["bcs"].append({
                "name": bc_name,
                "type": type(bc).__name__
            })

        for j_name, j in mdb.jobs.items():
            snap["jobs"].append({
                "name": j_name,
                "status": getattr(j, 'status', 'UNKNOWN')
            })

        tot_nodes = sum([p["nodes"] for p in snap["parts"]])
        tot_elems = sum([p["elements"] for p in snap["parts"]])
        snap["counts"] = {
            "parts": len(snap["parts"]),
            "materials": len(snap["materials"]),
            "sections": len(snap["sections"]),
            "steps": len(snap["steps"]),
            "loads": len(snap["loads"]),
            "bcs": len(snap["bcs"]),
            "nodes": tot_nodes,
            "elements": tot_elems,
            "jobs": len(snap["jobs"])
        }
except Exception as e:
    snap["error"] = str(e)

print("__SNAPSHOT_JSON__" + json.dumps(snap))
"""


def take_snapshot(tag: Optional[str] = None, host: str = ABAQUS_HOST, port: int = ABAQUS_PORT, timeout: float = 120.0) -> Tuple[bool, str, Dict[str, Any]]:
    """Capture full model state snapshot and persist as JSON."""
    ok, res = run_py(SNAPSHOT_SCRIPT, host=host, port=port, timeout=timeout, confirm_enter=False)
    if not ok:
        return False, f"Snapshot capture failed: {res}", {}

    data: Dict[str, Any] = {}
    for line in res.splitlines():
        if line.startswith("__SNAPSHOT_JSON__"):
            try:
                data = json.loads(line[17:])
                break
            except Exception as e:
                return False, f"Failed to parse snapshot output: {e}", {}

    if not data:
        return False, f"Empty snapshot data returned: {res}", {}

    ts = time.strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_{tag}.json" if tag else f"{ts}.json"
    file_path = STATE_DIR / fname
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True, str(file_path), data


def load_snapshot(path_or_tag: str) -> Dict[str, Any]:
    """Load snapshot JSON file by absolute path, filename or tag match."""
    p = Path(path_or_tag)
    if p.is_file():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    target = STATE_DIR / (path_or_tag if path_or_tag.endswith(".json") else f"{path_or_tag}.json")
    if target.is_file():
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)

    files = sorted([f for f in STATE_DIR.glob("*.json") if path_or_tag in f.name])
    if files:
        with open(files[-1], "r", encoding="utf-8") as f:
            return json.load(f)

    raise FileNotFoundError(f"No snapshot found matching: {path_or_tag}")


def list_snapshots() -> List[Path]:
    """List all saved snapshot files sorted chronologically."""
    if not STATE_DIR.is_dir():
        return []
    return sorted(list(STATE_DIR.glob("*.json")))


def last_snapshots(n: int = 2) -> List[Path]:
    """Retrieve the most recent n snapshot files."""
    snaps = list_snapshots()
    return snaps[-n:] if len(snaps) >= n else snaps


def diff_snapshots(snap_a: Dict[str, Any], snap_b: Dict[str, Any]) -> str:
    """Compare two snapshots and return human/AI readable diff summary."""
    lines: List[str] = []

    # Entity count differences
    counts_a = snap_a.get("counts", {})
    counts_b = snap_b.get("counts", {})
    keys = sorted(set(counts_a.keys()) | set(counts_b.keys()))
    count_diffs = []
    for k in keys:
        va = counts_a.get(k, 0)
        vb = counts_b.get(k, 0)
        if va != vb:
            delta = vb - va
            sign = f"+{delta}" if delta > 0 else str(delta)
            count_diffs.append(f"{k.upper()}: {va} -> {vb} ({sign})")
    if count_diffs:
        lines.append("Entity Count Changes:\n  " + ", ".join(count_diffs))
    else:
        lines.append("Entity Counts: Unchanged")

    # Part changes
    parts_a = {p.get("name"): p for p in snap_a.get("parts", [])}
    parts_b = {p.get("name"): p for p in snap_b.get("parts", [])}
    added_parts = set(parts_b.keys()) - set(parts_a.keys())
    removed_parts = set(parts_a.keys()) - set(parts_b.keys())
    if added_parts:
        lines.append(f"Added Parts: {list(added_parts)}")
    if removed_parts:
        lines.append(f"Removed Parts: {list(removed_parts)}")

    # Material changes
    mats_a = {m.get("name"): m for m in snap_a.get("materials", [])}
    mats_b = {m.get("name"): m for m in snap_b.get("materials", [])}
    added_m = set(mats_b.keys()) - set(mats_a.keys())
    removed_m = set(mats_a.keys()) - set(mats_b.keys())
    if added_m:
        lines.append(f"Added Materials: {list(added_m)}")
    if removed_m:
        lines.append(f"Removed Materials: {list(removed_m)}")
    for mname in set(mats_a.keys()) & set(mats_b.keys()):
        ma, mb = mats_a[mname], mats_b[mname]
        diff_props = []
        for prop in ["elastic", "density", "plastic"]:
            if ma.get(prop) != mb.get(prop):
                diff_props.append(f"{prop}: {ma.get(prop)} -> {mb.get(prop)}")
        if diff_props:
            lines.append(f"Modified Material '{mname}': {', '.join(diff_props)}")

    # Step changes
    steps_a = {s.get("name"): s for s in snap_a.get("steps", [])}
    steps_b = {s.get("name"): s for s in snap_b.get("steps", [])}
    added_steps = set(steps_b.keys()) - set(steps_a.keys())
    removed_steps = set(steps_a.keys()) - set(steps_b.keys())
    if added_steps:
        lines.append(f"Added Steps: {list(added_steps)}")
    if removed_steps:
        lines.append(f"Removed Steps: {list(removed_steps)}")

    # Load and BC changes
    loads_a = {l.get("name"): l for l in snap_a.get("loads", [])}
    loads_b = {l.get("name"): l for l in snap_b.get("loads", [])}
    added_loads = set(loads_b.keys()) - set(loads_a.keys())
    removed_loads = set(loads_a.keys()) - set(loads_b.keys())
    if added_loads:
        lines.append(f"Added Loads: {list(added_loads)}")
    if removed_loads:
        lines.append(f"Removed Loads: {list(removed_loads)}")

    bcs_a = {b.get("name"): b for b in snap_a.get("bcs", [])}
    bcs_b = {b.get("name"): b for b in snap_b.get("bcs", [])}
    added_bcs = set(bcs_b.keys()) - set(bcs_a.keys())
    removed_bcs = set(bcs_a.keys()) - set(bcs_b.keys())
    if added_bcs:
        lines.append(f"Added Boundary Conditions: {list(added_bcs)}")
    if removed_bcs:
        lines.append(f"Removed Boundary Conditions: {list(removed_bcs)}")

    # Jobs
    jobs_a = {j.get("name"): j for j in snap_a.get("jobs", [])}
    jobs_b = {j.get("name"): j for j in snap_b.get("jobs", [])}
    added_jobs = set(jobs_b.keys()) - set(jobs_a.keys())
    if added_jobs:
        lines.append(f"Added Jobs: {list(added_jobs)}")

    return "\n".join(lines) if lines else "No detectable differences between snapshots."


WORKSPACE_SCRIPT = """
import os
import glob
import json

wdir = os.getcwd()
rpy_files = sorted(glob.glob(os.path.join(wdir, "abaqus.rpy*")), key=os.path.getmtime)
cae_files = glob.glob(os.path.join(wdir, "*.cae"))
inp_files = glob.glob(os.path.join(wdir, "*.inp"))
odb_files = glob.glob(os.path.join(wdir, "*.odb"))

info = {
    "working_dir": wdir,
    "rpy_files": [os.path.basename(f) for f in rpy_files],
    "latest_rpy": rpy_files[-1] if rpy_files else None,
    "cae_files": [os.path.basename(f) for f in cae_files],
    "inp_files": [os.path.basename(f) for f in inp_files],
    "odb_files": [os.path.basename(f) for f in odb_files]
}
print("__WORKSPACE_JSON__" + json.dumps(info))
"""


def get_workspace_info(host: str = ABAQUS_HOST, port: int = ABAQUS_PORT, timeout: float = 30.0) -> Dict[str, Any]:
    """Query active Abaqus instance working directory and file status directly from disk."""
    if WORKSPACE_DIR.is_dir():
        rpy_names = sorted([f.name for f in WORKSPACE_DIR.glob("abaqus.rpy*")])
        latest_rpy = str(WORKSPACE_DIR / rpy_names[-1]) if rpy_names else None
        return {
            "working_dir": str(WORKSPACE_DIR),
            "rpy_files": rpy_names,
            "latest_rpy": latest_rpy,
            "cae_files": [f.name for f in WORKSPACE_DIR.glob("*.cae")],
            "inp_files": [f.name for f in WORKSPACE_DIR.glob("*.inp")],
            "odb_files": [f.name for f in WORKSPACE_DIR.glob("*.odb")]
        }

    ok, res = run_py(WORKSPACE_SCRIPT, host=host, port=port, timeout=timeout)
    if not ok:
        return {"error": res, "working_dir": None, "latest_rpy": None}

    for line in res.splitlines():
        if line.startswith("__WORKSPACE_JSON__"):
            try:
                return json.loads(line[18:])
            except Exception as e:
                return {"error": f"Failed to parse workspace JSON: {e}"}
    return {"error": res, "working_dir": None, "latest_rpy": None}


def read_rpy_log(tail_lines: int = 30, filter_views: bool = True, host: str = ABAQUS_HOST, port: int = ABAQUS_PORT) -> str:
    """Read recent GUI commands from active Abaqus instance's abaqus.rpy file."""
    ws = get_workspace_info(host=host, port=port)
    if "error" in ws and ws["error"]:
        return f"Error connecting to Abaqus: {ws['error']}"

    rpy_path = ws.get("latest_rpy")
    if not rpy_path or not Path(rpy_path).is_file():
        return f"Abaqus working directory is '{ws.get('working_dir')}', but no abaqus.rpy file was found."

    p = Path(rpy_path)
    try:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            with open(p, "r", encoding="gbk", errors="replace") as f:
                lines = f.readlines()

        if not lines:
            return f"Log file ({p.name}) is currently empty."

        # Filter out repetitive viewport rotation / zoom commands if requested
        if filter_views:
            filtered = []
            for ln in lines:
                if any(x in ln for x in [".view.rotate(", ".view.setValues(", ".view.pan(", "onCaeGraphicsStartup"]):
                    continue
                filtered.append(ln)
            lines = filtered

        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        header = f"Recent Abaqus GUI Action Log ({p.name}) - Showing last {len(tail)} lines:\n"
        return header + "".join(tail).strip()
    except Exception as e:
        return f"Failed to read rpy log from {p}: {e}"


def find_abaqus_executable(custom_cmd: Optional[str] = None) -> Optional[str]:
    """Find available Abaqus command or executable path."""
    cmd = custom_cmd or ABAQUS_COMMAND
    if cmd:
        if shutil.which(cmd):
            return cmd
        if Path(cmd).is_file():
            return str(Path(cmd).resolve())

    # Fallback checks on Windows
    common_paths = [
        r"C:\SIMULIA\Commands\abaqus.bat",
        r"D:\SIMULIA\Commands\abaqus.bat",
        r"C:\SIMULIA\EstProducts\2022\win_b64\code\bin\ABQLauncher.exe",
        r"C:\SIMULIA\EstProducts\2021\win_b64\code\bin\ABQLauncher.exe",
    ]
    for p in common_paths:
        if Path(p).is_file():
            return p

    return None


def setup_abaqus_env(target_dir: Optional[Path] = None) -> None:
    """Append a managed plugin path block, preserving other workspace settings."""
    work_dir = target_dir or WORKSPACE_DIR
    work_dir.mkdir(parents=True, exist_ok=True)
    env_file = work_dir / "abaqus_v6.env"
    begin, end = "# BEGIN ABAQUS AI BRIDGE", "# END ABAQUS AI BRIDGE"
    existing = env_file.read_text(encoding="utf-8-sig") if env_file.exists() else ""
    if begin in existing and end in existing:
        a, b = existing.index(begin), existing.index(end) + len(end)
        existing = existing[:a] + existing[b:]
    block = f"{begin}\nplugin_central_dir = {str(ROOT_DIR / 'abaqus_plugins')!r}\n{end}\n"
    env_file.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")


def launch_abaqus_cae(
    workspace_dir: Optional[str] = None, gui: bool = True,
    timeout: float = 60.0, custom_cmd: Optional[str] = None
) -> Tuple[bool, str]:
    """Launch a GUI session with the plugin loaded before accepting commands."""
    if not gui:
        return False, "The event bridge requires a CAE GUI. Use a separate noGUI workflow for batch jobs."
    if ping(timeout=1.0):
        return True, "Abaqus GUI bridge is already responding."
    info = get_ipc_status()
    if info.get("pid") and is_pid_alive(info["pid"]) and info.get("status") != "stopped":
        return False, "An existing GUI session is recorded but not responding. It may be busy; inspect it before launching another session."
    abaqus_cmd = find_abaqus_executable(custom_cmd)
    if not abaqus_cmd:
        return False, "Cannot find 'abaqus'. Set ABAQUS_COMMAND in .env."
    target_cwd = Path(workspace_dir).resolve() if workspace_dir else WORKSPACE_DIR
    setup_abaqus_env(target_cwd)
    ensure_dirs(str(IPC_DIR))
    # Propagate resolved configuration to BOTH Abaqus processes. Do not copy API keys
    # into bootstrap files, discard task evidence, or kill unrelated CAE sessions.
    child_env = os.environ.copy()
    child_env.update(PROJECT_ROOT=str(ROOT_DIR), ABAQUS_IPC_DIR=str(IPC_DIR),
                     ABAQUS_BRIDGE_PORT=str(ABAQUS_PORT))
    mount_script = BASE_DIR / "bridge_server" / "mount_bridge.py"
    try:
        subprocess.Popen([abaqus_cmd, "cae", f"script={mount_script}"],
                         cwd=str(target_cwd), env=child_env)
    except OSError as exc:
        return False, f"Failed to launch Abaqus: {exc}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ping(timeout=1.0):
            return True, f"Abaqus GUI event bridge is connected in '{target_cwd}'."
        time.sleep(0.5)
    return False, f"Abaqus launched, but its GUI event bridge did not respond within {timeout}s. Inspect plugin startup messages."
