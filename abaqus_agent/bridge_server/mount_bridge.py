# -*- coding: utf-8 -*-
"""Abaqus AI Bridge Bootstrap Script.

Can be run directly inside Abaqus CAE via File -> Run Script.
"""

import inspect
import os
import sys


def _get_script_dir():
    """Determine directory of current script safely across Abaqus CAE run modes."""
    # 1. Try __file__ if defined in environment
    try:
        if "__file__" in globals() and globals()["__file__"]:
            return os.path.dirname(os.path.abspath(globals()["__file__"]))
    except Exception:
        pass

    # 2. Inspect code object filename (works during execfile in Abaqus CAE)
    try:
        frame = inspect.currentframe()
        if frame and frame.f_code.co_filename and os.path.isfile(frame.f_code.co_filename):
            return os.path.dirname(os.path.abspath(frame.f_code.co_filename))
    except Exception:
        pass

    # 3. Check PROJECT_ROOT from environment variable
    env_root = os.environ.get("PROJECT_ROOT", "").strip()
    if env_root and os.path.isdir(env_root):
        target = os.path.join(env_root, "abaqus_agent", "bridge_server")
        if os.path.isfile(os.path.join(target, "abaqus_bridge.py")):
            return target

    # 4. Search relative paths from current working directory
    cwd = os.getcwd()
    candidates = [
        os.path.join(cwd, "abaqus_agent", "bridge_server"),
        os.path.join(os.path.dirname(cwd), "abaqus_agent", "bridge_server"),
        r"D:\pycharm_wenjian\abaqus-agent\abaqus_agent\bridge_server",
    ]
    for c in candidates:
        if os.path.isfile(os.path.join(c, "abaqus_bridge.py")):
            return c

    return cwd


# Locate bridge server script directory
script_dir = _get_script_dir()
bridge_file = os.path.join(script_dir, "abaqus_bridge.py")

if not os.path.isfile(bridge_file):
    # Try relative to abaqus_agent package
    alt_file = os.path.join(script_dir, "bridge_server", "abaqus_bridge.py")
    if os.path.isfile(alt_file):
        bridge_file = alt_file

if os.path.isfile(bridge_file):
    server_dir = os.path.dirname(bridge_file)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    import abaqus_bridge
    # File -> Run Script executes in the kernel. It cannot install a GUI event
    # handler into the separate GUI process. The plugin must load at CAE startup.
    print("[Abaqus-Bridge] Kernel executor loaded; no polling loop started.")
    print("[Abaqus-Bridge] Automatic dispatch requires the Abaqus AI GUI plugin loaded at startup.")
else:
    print("[Abaqus-Bridge] ERROR: Cannot locate abaqus_bridge.py in " + script_dir)
