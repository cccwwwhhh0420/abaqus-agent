# -*- coding: utf-8 -*-
"""Finite, serial task execution in the Abaqus kernel (Python 2.7/3.x)."""
from __future__ import print_function

import io
import os
import sys
import time
import traceback

from bridge_protocol import atomic_json, ensure_dirs, read_json, resolve_ipc_dir, valid_id

IPC_DIR = resolve_ipc_dir()
_executing = False


def model_summary():
    """Best-effort evidence; a summary is not an engineering validation."""
    try:
        from abaqus import mdb
        return {"models": dict((name, {
            "parts": list(model.parts.keys()),
            "materials": list(model.materials.keys()),
            "sections": list(model.sections.keys()),
            "steps": list(model.steps.keys()),
            "loads": list(model.loads.keys()),
            "bcs": list(model.boundaryConditions.keys()),
            "instances": list(model.rootAssembly.instances.keys()),
        }) for name, model in mdb.models.items()), "jobs": list(mdb.jobs.keys())}
    except Exception as exc:
        return {"unavailable": str(exc)}


def execute_script(code_str):
    """Preserve the interactive namespace and capture output and traceback."""
    import __main__
    captured = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr

    class Capture(object):
        def write(self, value):
            if not isinstance(value, type(u"")):
                value = value.decode("utf-8", "replace")
            return captured.write(value)

        def flush(self):
            pass

    sys.stdout = sys.stderr = Capture()
    ok = False
    error = ""
    try:
        # An exec-mode code object evaluated this way works in Python 2 and 3.
        source = (u"# -*- coding: utf-8 -*-\n" + code_str).encode("utf-8")
        eval(compile(source, "<abaqus_ai_task>", "exec"),
             __main__.__dict__)
        ok = True
    except BaseException:
        error = traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return ok, captured.getvalue(), error


def execute_task(task_id, ipc_dir=None, session_id=None):
    """Claim one queued task. Never replay a claimed task after a crash."""
    global _executing
    if not valid_id(task_id):
        raise ValueError("Invalid task ID")
    root = os.path.abspath(ipc_dir or IPC_DIR)
    ensure_dirs(root)
    commands = os.path.join(root, "commands")
    result_path = os.path.join(root, "results", task_id + ".json")
    queued = os.path.join(commands, "cmd_" + task_id + ".json")
    running = os.path.join(commands, task_id + ".running.json")
    if _executing or os.path.exists(result_path):
        return False
    if any(name.endswith(".running.json") for name in os.listdir(commands)):
        return False
    try:
        os.rename(queued, running)
    except OSError:
        return False
    _executing = True
    started = time.time()
    result = {"id": task_id, "state": "failed", "success": False,
              "started_at": started, "kernel_pid": os.getpid(), "output": ""}
    try:
        task = read_json(running)
        if task.get("id") != task_id:
            raise ValueError("Task ID does not match filename")
        if session_id is not None and task.get("session_id") != session_id:
            result["state"] = "cancelled"
            result["output"] = "Task belongs to a different GUI session; not executed."
        elif task.get("expires_at", 0) < time.time():
            result["state"] = "cancelled"
            result["output"] = "Task expired before execution; not executed."
        else:
            before = model_summary()
            ok, output, error = execute_script(task["script"])
            result.update(success=ok, state="succeeded" if ok else "failed",
                          output=output, traceback=error,
                          model_before=before, model_after=model_summary())
    except BaseException:
        result["traceback"] = traceback.format_exc()
    finally:
        result["timestamp"] = time.time()
        try:
            atomic_json(result_path, result)
            os.remove(running)
        finally:
            _executing = False
    return True


def poll_once():
    """Manual fallback: process at most one task and return immediately."""
    ensure_dirs(IPC_DIR)
    status_path = os.path.join(IPC_DIR, "status.json")
    session_id = read_json(status_path).get("session_id") if os.path.isfile(status_path) else None
    commands = os.path.join(IPC_DIR, "commands")
    names = sorted((name for name in os.listdir(commands)
                    if name.startswith("cmd_") and name.endswith(".json")),
                   key=lambda name: os.path.getmtime(os.path.join(commands, name)))
    for name in names:
        task_id = name[4:-5]
        if valid_id(task_id):
            return execute_task(task_id, IPC_DIR, session_id)
    return False


def bridge_once(*args, **kwargs):
    """Compatibility entry point: no waiting loop remains."""
    return poll_once()


def bridge_coop_loop(*args, **kwargs):
    raise RuntimeError("Kernel polling was removed. Use Plug-ins -> Abaqus AI -> Start Bridge.")
