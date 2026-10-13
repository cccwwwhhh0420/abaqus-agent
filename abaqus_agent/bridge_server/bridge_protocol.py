# -*- coding: utf-8 -*-
"""File protocol shared by external client, GUI and kernel; Python 2.7/3.x."""
import io
import json
import os
import re
import time
import uuid

PROTOCOL_VERSION = 2


def valid_id(value):
    return bool(isinstance(value, (str, type(u""))) and
                re.match(r"^[a-f0-9]{32}\Z", value))


def resolve_ipc_dir():
    explicit = os.environ.get("ABAQUS_IPC_DIR", "").strip()
    if explicit:
        return os.path.abspath(explicit)
    repo = os.environ.get("PROJECT_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    workspace = os.environ.get("WORKSPACE_DIR", "workspace")
    return os.path.abspath(os.path.join(repo, workspace, ".ipc"))


def ensure_dirs(root):
    for name in ("commands", "results", "cancelled"):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            try:
                os.makedirs(path)
            except OSError:
                if not os.path.isdir(path):
                    raise


def cancel_queued_task(root, task_id, reason):
    """Only cancel an unclaimed file; safe against concurrent kernel claims."""
    if not valid_id(task_id):
        raise ValueError("Invalid task ID")
    queued = os.path.join(root, "commands", "cmd_" + task_id + ".json")
    cancelled = os.path.join(root, "cancelled", task_id + ".json")
    try:
        os.rename(queued, cancelled)
    except OSError:
        return False
    atomic_json(os.path.join(root, "results", task_id + ".json"), {
        "id": task_id, "state": "cancelled", "success": False,
        "output": reason + " Task was cancelled before execution.", "timestamp": time.time()})
    return True


def read_json(path):
    if os.name == "nt":
        # Python's normal file handles can deny replacement while another process
        # reads a heartbeat. Share DELETE as well as READ/WRITE to preserve atomic
        # publication without sleeping inside a GUI callback.
        import ctypes
        import msvcrt
        create = ctypes.windll.kernel32.CreateFileW
        create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint,
                           ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        create.restype = ctypes.c_void_p
        handle = create(type(u"")(path), 0x80000000, 0x1 | 0x2 | 0x4,
                        None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError()
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            close = ctypes.windll.kernel32.CloseHandle
            close.argtypes = [ctypes.c_void_p]
            close(handle)
            raise
        stream = io.open(descriptor, "r", encoding="utf-8", closefd=True)
    else:
        stream = io.open(path, "r", encoding="utf-8")
    with stream:
        return json.load(stream)


def completed_result(path, task_id):
    """Only a matching, valid terminal record proves task completion."""
    try:
        data = read_json(path)
        if isinstance(data, dict) and data.get("id") == task_id and data.get("state") in (
                "succeeded", "failed", "cancelled"):
            return data
    except (IOError, OSError, ValueError):
        pass
    return None


def atomic_json(path, payload):
    """Publish complete JSON, including replacement on Python 2 Windows."""
    temp = path + "." + uuid.uuid4().hex + ".tmp"
    try:
        with io.open(temp, "w", encoding="utf-8") as stream:
            data = json.dumps(payload, ensure_ascii=False, indent=2)
            if not isinstance(data, type(u"")):
                data = data.decode("utf-8")
            stream.write(data)
            stream.flush()
        if os.name == "nt":
            import ctypes
            if os.path.exists(path):
                # MoveFileEx/os.replace rejects an open destination even when it
                # shares DELETE. ReplaceFile supports our shared reader handles.
                replace = ctypes.windll.kernel32.ReplaceFileW
                replace.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                    ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
                replace.restype = ctypes.c_int
                if not replace(type(u"")(path), type(u"")(temp), None, 0, None, None):
                    raise ctypes.WinError()
            else:
                move = ctypes.windll.kernel32.MoveFileExW
                move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
                move.restype = ctypes.c_int
                if not move(type(u"")(temp), type(u"")(path), 0x8):
                    raise ctypes.WinError()
        else:
            os.rename(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)
