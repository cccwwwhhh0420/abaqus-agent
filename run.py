# -*- coding: utf-8 -*-
"""Main startup entry point for Abaqus AI Agent."""

import sys
from pathlib import Path

# Add project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    if "--launch-only" in sys.argv:
        from abaqus_agent.bridge.client import launch_abaqus_cae
        ok, message = launch_abaqus_cae()
        print(message)
        sys.exit(0 if ok else 1)
    else:
        from abaqus_agent.main import main
        main()
