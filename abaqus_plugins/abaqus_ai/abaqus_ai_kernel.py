# -*- coding: utf-8 -*-
"""Manual one-task fallback; continuous scheduling belongs to the GUI."""
from __future__ import print_function
import os
import sys


def run_task():
    server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                             "abaqus_agent", "bridge_server"))
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    import abaqus_bridge
    if not abaqus_bridge.poll_once():
        print("[Abaqus-AI] No dispatchable task (or an unresolved task is running).")
