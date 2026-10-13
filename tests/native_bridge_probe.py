"""Explicit native CAE smoke test; not part of unittest discovery.

Launches an isolated GUI session, executes a trivial model mutation, records
evidence, and closes only CAE processes whose working directory is this test's.
Never calls an LLM or submits an analysis.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--import-inp", type=Path, help="Import this input into the disposable CAE session.")
arguments = parser.parse_args()
if arguments.import_inp and not arguments.import_inp.is_file():
    parser.error("Input file does not exist.")
directory = ROOT / "tests" / "native_runs" / uuid.uuid4().hex
directory.mkdir(parents=True)
os.environ.update(PROJECT_ROOT=str(ROOT), WORKSPACE_DIR=str(directory),
                  ABAQUS_IPC_DIR=str(directory / ".ipc"), ABAQUS_BRIDGE_PORT="0",
                  ABAQUS_BRIDGE_DIAGNOSTIC="1")

import psutil
from abaqus_agent.bridge import client

report = {"directory": str(directory), "started_at": time.time()}
exit_code = 1
try:
    ok, message = client.launch_abaqus_cae(workspace_dir=str(directory), timeout=60)
    report["launch"] = {"ok": ok, "message": message}
    print(message, flush=True)
    if not ok:
        raise RuntimeError(message)
    time.sleep(1)
    report["gui_before"] = client.get_ipc_status()
    ok, output = client.run_py(
        "from abaqus import mdb, session\n"
        "mdb.Model(name='MustNotExist')\n"
        "session.processUpdates()", timeout=5, confirm_enter=False)
    report["api_precheck"] = {"rejected": not ok and "API_PRECHECK:" in output,
                              "output": output}
    if not report["api_precheck"]["rejected"]:
        raise RuntimeError("Known invalid API was not rejected before mutation.")
    ok, output = client.run_py(
        "from abaqus import mdb\n"
        "mdb.Model(name='BridgeDiagnostic')\n"
        "print('NATIVE_BRIDGE_PROBE:' + ','.join(sorted(mdb.models.keys())))",
        timeout=20, confirm_enter=False)
    report["execution"] = {"ok": ok, "output": output}
    print(output, flush=True)
    if not ok or "BridgeDiagnostic" not in output:
        raise RuntimeError("Native model mutation was not confirmed.")
    if "MustNotExist" in output:
        raise RuntimeError("Rejected script unexpectedly changed the model.")
    if arguments.import_inp:
        source = str(arguments.import_inp.resolve())
        script = (
            "from abaqus import mdb, session\n"
            "m = mdb.ModelFromInputFile(name='schwarzd', inputFileName=%r)\n"
            "del mdb.models['Model-1']\n"
            "del mdb.models['BridgeDiagnostic']\n"
            "vp = session.viewports[session.currentViewportName]\n"
            "vp.setValues(displayedObject=m.rootAssembly)\n"
            "vp.view.fitView()\n"
            "vp.forceRefresh()\n"
            "vp.bringToFront()\n"
            "print('NATIVE_INP_PROBE:' + ','.join(mdb.models.keys()))\n"
        ) % source
        ok, output = client.run_py(script, timeout=90, confirm_enter=False)
        report["inp_import"] = {"source": source, "ok": ok, "output": output}
        print(output, flush=True)
        if not ok or "NATIVE_INP_PROBE:schwarzd" not in output:
            raise RuntimeError("Native INP import was not confirmed.")
    report["model_status"] = client.get_status(timeout=20)
    print("Model status: " + repr(report["model_status"]), flush=True)
    time.sleep(0.6)
    report["gui_after"] = client.get_ipc_status()
    print(json.dumps(report["gui_after"], ensure_ascii=False, indent=2), flush=True)
    if not report["model_status"][0]:
        raise RuntimeError("Native model status query failed.")
    exit_code = 0
except Exception as exc:
    report["error"] = str(exc)
    print("Native probe failed: " + str(exc), flush=True)
finally:
    info = client.get_ipc_status()
    if info.get("alive"):
        client._notify_gui(info, "diagnostic_close", timeout=1)
        time.sleep(1)
    # Native CAE launchers can detach. Match the unique test cwd, executable
    # name and creation time before terminating any disposable test process.
    closed = []
    processes = list(psutil.process_iter(["pid", "name", "create_time"]))
    processes.sort(key=lambda p: (p.info["name"] or "").lower() != "abqcaeg.exe")
    for process in processes:
        try:
            name = (process.info["name"] or "").lower()
            if name not in ("abqcaeg.exe", "abqcaek.exe"):
                continue
            if process.info["create_time"] < report["started_at"]:
                continue
            if Path(process.cwd()).resolve() != directory.resolve():
                continue
            process.terminate()
            closed.append(process.pid)
        except (psutil.Error, OSError):
            pass
    report["closed_test_pids"] = closed
    report["finished_at"] = time.time()
    report_path = directory / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence: " + str(report_path), flush=True)
sys.exit(exit_code)
