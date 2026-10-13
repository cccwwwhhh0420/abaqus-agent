# -*- coding: utf-8 -*-
"""Tool definitions and execution handlers for Abaqus AI Agent."""

import json
from typing import Any, Dict, List, Optional
from abaqus_agent.bridge.client import (
    diff_snapshots,
    describe_bridge_status,
    get_status,
    get_workspace_info,
    last_snapshots,
    list_snapshots,
    load_snapshot,
    ping,
    read_rpy_log,
    run_py,
    take_snapshot,
)
from abaqus_agent.tools.doc_search import doc_searcher


def tool_abaqus_get_workspace() -> str:
    """Get active Abaqus workspace directory, associated model files, and abaqus.rpy status."""
    info = get_workspace_info()
    if "error" in info and info["error"]:
        return f"Failed to get workspace info: {info['error']}"

    lines = [
        f"Working Directory: {info.get('working_dir')}",
        f"Latest Replay Log: {info.get('latest_rpy')}",
    ]
    rpy_files = info.get("rpy_files", [])
    if rpy_files:
        lines.append(f"Found Replay Files: {rpy_files}")
    cae_files = info.get("cae_files", [])
    if cae_files:
        lines.append(f"Found .cae Model Files: {cae_files}")
    inp_files = info.get("inp_files", [])
    if inp_files:
        lines.append(f"Found .inp Input Decks: {inp_files}")
    odb_files = info.get("odb_files", [])
    if odb_files:
        lines.append(f"Found .odb Results Databases: {odb_files}")

    return "\n".join(lines)


def tool_abaqus_read_gui_log(tail_lines: int = 30, filter_views: bool = True) -> str:
    """Read recent Python commands recorded by Abaqus when the user operates the CAE GUI."""
    return read_rpy_log(tail_lines=tail_lines, filter_views=filter_views)


def tool_abaqus_ping() -> str:
    """Check whether Abaqus bridge server is online and responding."""
    from abaqus_agent.bridge.client import get_ipc_status
    ipc_info = get_ipc_status()
    if ipc_info.get("alive"):
        pid = ipc_info.get("pid")
        return f"Abaqus bridge: {describe_bridge_status(ipc_info)} (PID: {pid})."
    if ping():
        return "Abaqus bridge is ONLINE and connected."
    return "Abaqus bridge is OFFLINE. Please launch Abaqus CAE via /launch."


def tool_abaqus_run_script(script: str) -> str:
    """Execute Python script in Abaqus CAE kernel and return output or error message."""
    if not script.strip():
        return "Error: Empty Python script provided."
    ok, res = run_py(script)
    if ok:
        return f"SUCCESS:\n{res}" if res else "SUCCESS (Script executed with no output)."
    return f"ERROR:\n{res}"


def tool_abaqus_search_docs(keyword: str, detail: bool = True, limit: int = 5) -> str:
    """Search Abaqus Python API documentation for syntax, parameters, and examples."""
    return doc_searcher.search(query=keyword, limit=limit, full_detail=detail)


def tool_abaqus_status() -> str:
    """Get current entity summary (parts, materials, sections, steps, loads, bcs, mesh) from Abaqus."""
    ok, res = get_status()
    if ok:
        return f"Abaqus Status:\n{json.dumps(res, indent=2, ensure_ascii=False)}"
    return f"Failed to get status: {res.get('error', res)}"


def tool_abaqus_snapshot(tag: Optional[str] = None) -> str:
    """Take a full snapshot of current Abaqus model entities to detect changes."""
    ok, path, data = take_snapshot(tag=tag)
    if ok:
        counts = data.get("counts", {})
        counts_str = ", ".join([f"{k.upper()}={v}" for k, v in counts.items()])
        return f"Snapshot captured successfully and saved to: {path}\nSummary: {counts_str}"
    return f"Snapshot failed: {path}"


def tool_abaqus_diff(snap_a: Optional[str] = None, snap_b: Optional[str] = None) -> str:
    """Compare two model snapshots to detect manual user edits or script modifications."""
    try:
        snaps = list_snapshots()
        if not snaps:
            return "No snapshots available. Please run abaqus_snapshot first."

        if snap_a is None or snap_b is None:
            if len(snaps) < 2:
                return f"Need at least 2 snapshots to diff. Found {len(snaps)}: {[s.name for s in snaps]}"
            path_a = str(snaps[-2])
            path_b = str(snaps[-1])
        else:
            path_a = snap_a
            path_b = snap_b

        data_a = load_snapshot(path_a)
        data_b = load_snapshot(path_b)
        res = diff_snapshots(data_a, data_b)
        return f"Snapshot Diff (A: {path_a} -> B: {path_b}):\n{res}"
    except Exception as e:
        return f"Diff failed: {e}"
