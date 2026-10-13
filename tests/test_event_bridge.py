"""Mock GUI + real file/socket protocol. Never loads CAE or submits a job."""
import importlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "abaqus_agent" / "bridge_server"))
sys.path.insert(0, str(ROOT / "abaqus_plugins" / "abaqus_ai"))
import bridge_protocol as protocol
import abaqus_bridge as kernel
from abaqus_agent.bridge import client


class Window:
    def __init__(self, visible=True):
        self.visible = visible
        self.next = None
        self.enabled = True
        self.buttons = 0

    def shown(self):
        return self.visible

    def getNext(self):
        return self.next

    def isEnabled(self):
        return self.enabled

    def getCursorPosition(self):
        return True, 0, 0, self.buttons


class Dialog(Window):
    pass


class FileDialog(Window):
    pass


class Popup(Window):
    pass


class App:
    def __init__(self):
        self.main = Window()
        self.locked = False
        self.displayed = "PART"
        self.timer_number = 0
        self.timers = {}
        self.sockets = {}

    def getAFXMainWindow(self):
        return self.main

    def getRoot(self):
        return types.SimpleNamespace(getFirst=lambda: self.main)

    def isLocked(self):
        return self.locked

    def addTimeout(self, ms, target, selector):
        self.timer_number += 1
        self.timers[self.timer_number] = (ms, target, selector)
        return self.timer_number

    def removeTimeout(self, number):
        self.timers.pop(number, None)

    def addSocket(self, fd, mode, target, selector):
        self.sockets[fd] = (target, selector)

    def removeSocket(self, fd, mode):
        self.sockets.pop(fd, None)


APP = App()
SENT = []


def send_command(command):
    SENT.append(command)
    exec(command, {})


fake_gui = types.ModuleType("abaqusGui")
for name, value in {
    "AFXDialog": Dialog, "FXDialogBox": FileDialog, "FXPopup": Popup, "FXObject": object,
    "FXMAPFUNC": lambda *args: None, "INPUT_READ": 1, "SEL_COMMAND": 2,
    "SEL_IO_READ": 3, "SEL_TIMEOUT": 4, "getAFXApp": lambda: APP,
    "LEFTBUTTONMASK": 0x100, "MIDDLEBUTTONMASK": 0x200, "RIGHTBUTTONMASK": 0x400,
    "getDisplayedObjectType": lambda: APP.displayed, "sendCommand": send_command,
    "shutdown": lambda: None,
}.items():
    setattr(fake_gui, name, value)
with patch.dict(sys.modules, {"abaqusGui": fake_gui}):
    gui = importlib.import_module("abaqus_ai_gui")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        global APP
        APP = App()
        SENT.clear()
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"ABAQUS_IPC_DIR": str(self.root), "ABAQUS_BRIDGE_PORT": "0"})
        self.env.start()
        self.client_root = patch.object(client, "IPC_DIR", self.root)
        self.client_root.start()
        self.bridge = gui.GuiBridge()
        with patch("builtins.print"):
            self.bridge.start()
        self.bridge.port = self.bridge.listener.getsockname()[1]
        self.bridge.write_status()
        self.counter = 0

    def tearDown(self):
        self.bridge.stop()
        self.client_root.stop()
        self.env.stop()
        self.temp.cleanup()

    def queue(self, script="print('task-output')", **values):
        import uuid
        task_id = uuid.uuid4().hex
        task = {"id": task_id, "script": script, "type": "execute",
                "session_id": self.bridge.session_id, "expires_at": time.time() + 60}
        task.update(values)
        path = self.root / "commands" / f"cmd_{task_id}.json"
        protocol.atomic_json(str(path), task)
        # Distinct mtimes make intended FIFO order independent of filesystem clock
        # granularity; equal-time production tasks use the stable directory order.
        self.counter += 1
        stamp = time.time() + self.counter
        os.utime(path, (stamp, stamp))
        self.bridge.pending = True
        return task_id

    def tick(self):
        APP.timers.pop(self.bridge.timer, None)  # one-shot timer has fired
        self.bridge.on_tick(None, None, None)

    def result(self, task_id):
        return protocol.read_json(str(self.root / "results" / f"{task_id}.json"))

    def test_one_task_per_tick_and_output(self):
        a, b = self.queue(), self.queue()
        self.tick()
        self.assertEqual(len(SENT), 1)
        self.assertEqual(self.result(a)["output"], "task-output\n")
        self.assertFalse((self.root / "results" / f"{b}.json").exists())
        self.tick()
        self.assertEqual(len(SENT), 2)
        self.assertTrue(self.result(b)["success"])
        self.assertEqual(len(APP.timers), 1)

    def test_failure_preserves_output_traceback_and_restores_stdout(self):
        task_id = self.queue("print('partial'); raise ValueError('failure')")
        before = sys.stdout
        self.tick()
        result = self.result(task_id)
        self.assertEqual(result["state"], "failed")
        self.assertIn("partial", result["output"])
        self.assertIn("ValueError: failure", result["traceback"])
        self.assertIs(sys.stdout, before)
        self.assertIn("model_before", result)
        self.assertIn("model_after", result)

    def test_unicode_output(self):
        task_id = self.queue("print(u'中文输出')")
        self.tick()
        self.assertEqual(self.result(task_id)["output"], "中文输出\n")

    def test_expired_and_previous_session_are_not_executed(self):
        a = self.queue("raise AssertionError('must not run')", expires_at=0)
        b = self.queue("raise AssertionError('must not run')", session_id="old")
        self.tick()
        self.tick()
        self.assertEqual(self.result(a)["state"], "cancelled")
        self.assertEqual(self.result(b)["state"], "cancelled")

    def test_dialog_popup_lock_sketch_and_mouse_defer(self):
        task_id = self.queue()
        APP.main.next = FileDialog()
        self.tick()
        APP.main.next = Dialog()
        self.tick()
        APP.main.next = Popup()
        self.tick()
        APP.main.next = None
        APP.locked = True
        self.tick()
        APP.locked = False
        APP.displayed = "SKETCH"
        self.tick()
        APP.displayed = "PART"
        APP.main.buttons = gui.LEFTBUTTONMASK
        self.tick()
        APP.main.buttons = 0
        self.assertEqual(SENT, [])
        self.tick()
        self.assertTrue(self.result(task_id)["success"])

    def test_top_level_input_flag_does_not_block_idle_cae(self):
        APP.main.enabled = False  # Native shell flag is not kernel readiness.
        task_id = self.queue()
        self.tick()
        self.assertTrue(self.result(task_id)["success"])

    def test_keyboard_modifiers_and_lock_keys_do_not_block_dispatch(self):
        for state in (0x1, 0x2, 0x4, 0x8, 0x10, 0x40, 0x5f):
            with self.subTest(state=state):
                APP.main.buttons = state
                task_id = self.queue()
                self.tick()
                self.assertTrue(self.result(task_id)["success"])
                info = client.get_ipc_status()
                self.assertEqual(info["gui"]["cursor_state"], state)
                self.assertEqual(info["gui"]["pressed_mouse_buttons"], 0)
                self.assertEqual(info["bridge_version"], "2.1.1")

    def test_each_mouse_button_defers_even_with_keyboard_modifiers(self):
        for button in (gui.LEFTBUTTONMASK, gui.MIDDLEBUTTONMASK, gui.RIGHTBUTTONMASK):
            with self.subTest(button=button):
                APP.main.buttons = button | 0x10
                task_id = self.queue()
                self.tick()
                self.assertEqual(client.get_task_result(task_id)["state"], "queued")
                info = client.get_ipc_status()
                self.assertEqual(info["gui"]["pressed_mouse_buttons"], button)
                self.assertIn("Mouse interaction", info["message"])
                APP.main.buttons = 0x10
                self.tick()
                self.assertTrue(self.result(task_id)["success"])

    def test_disabled_shell_still_defers_for_real_dialog(self):
        APP.main.enabled = False
        APP.main.next = FileDialog()
        task_id = self.queue()
        self.tick()
        self.assertEqual(SENT, [])
        self.assertEqual(client.get_task_result(task_id)["state"], "queued")
        self.assertIn("dialog", client.get_ipc_status()["message"])
        APP.main.next = None
        self.tick()
        self.assertTrue(self.result(task_id)["success"])

    def test_obsolete_queue_is_cancelled_without_kernel_even_during_dialog(self):
        APP.main.next = Dialog()
        expired = self.queue(expires_at=0)
        previous_session = self.queue(session_id="old")
        self.tick()
        self.tick()
        self.assertEqual(SENT, [])
        self.assertEqual(self.result(expired)["state"], "cancelled")
        self.assertEqual(self.result(previous_session)["state"], "cancelled")

    def test_idle_heartbeat_reports_dialog_even_with_no_pending_task(self):
        self.tick()
        self.assertFalse(self.bridge.pending)
        APP.main.next = Dialog()
        self.tick()
        info = client.get_ipc_status()
        self.assertEqual(info["status"], "blocked")
        self.assertIn("dialog", client.describe_bridge_status(info))
        self.assertIn("Blocked", client.describe_bridge_status(info))
        self.assertNotIn("Ready", client.describe_bridge_status(info))
        APP.main.next = None
        self.tick()
        self.assertEqual(client.get_ipc_status()["status"], "idle")

    def test_guard_error_defers(self):
        task_id = self.queue()
        with patch.object(APP, "isLocked", side_effect=RuntimeError("unavailable")):
            self.tick()
        self.assertFalse((self.root / "results" / f"{task_id}.json").exists())
        self.assertIn("Cannot verify", client.get_ipc_status()["message"])

    def test_nested_callback_cannot_dispatch_another_task(self):
        a, b = self.queue(), self.queue()
        def nested(command):
            self.bridge.on_tick(None, None, None)
            send_command(command)
        with patch.object(gui, "sendCommand", nested):
            self.tick()
        self.assertEqual(len(SENT), 1)
        self.assertFalse((self.root / "results" / f"{b}.json").exists())
        self.assertEqual(len(APP.timers), 1)

    def test_unknown_running_record_blocks_new_tasks(self):
        a = self.queue()
        (self.root / "commands" / f"cmd_{a}.json").rename(self.root / "commands" / f"{a}.running.json")
        self.queue()
        self.tick()
        self.assertEqual(SENT, [])
        self.assertEqual(client.get_ipc_status()["status"], "blocked")
        ok, output = client.run_py("raise AssertionError('must not submit')", confirm_enter=False)
        self.assertFalse(ok)
        self.assertIn("OUTCOME_UNKNOWN", output)

    def test_completed_running_record_is_reconciled(self):
        a = self.queue()
        (self.root / "commands" / f"cmd_{a}.json").rename(self.root / "commands" / f"{a}.running.json")
        protocol.atomic_json(str(self.root / "results" / f"{a}.json"), {"id": a, "state": "succeeded"})
        b = self.queue()
        self.tick()
        self.assertTrue(self.result(b)["success"])

    def test_same_task_cannot_replay(self):
        task_id = self.queue()
        self.tick()
        self.assertFalse(kernel.execute_task(task_id, str(self.root), self.bridge.session_id))

    def test_cancel_competes_with_claim(self):
        a = self.queue()
        self.assertTrue(client.cancel_pending(a, "cancel"))
        self.assertFalse(kernel.execute_task(a, str(self.root), self.bridge.session_id))
        b = self.queue()
        (self.root / "commands" / f"cmd_{b}.json").rename(self.root / "commands" / f"{b}.running.json")
        self.assertFalse(client.cancel_pending(b, "cancel"))
        self.assertEqual(client.get_task_result(b)["state"], "running")

    def test_split_socket_request_only_dispatches_on_tick(self):
        self.tick()  # clear initial pending scan
        task_id = self.queue()
        self.bridge.pending = False
        peer = socket.create_connection(("127.0.0.1", self.bridge.port), timeout=1)
        try:
            self.bridge.on_accept(None, None, None)
            frame = (json.dumps({"op": "wake", "session_id": self.bridge.session_id}) + "\n").encode()
            peer.sendall(frame[:10])
            self.bridge.on_read(None, None, None)
            self.assertFalse(self.bridge.pending)
            peer.sendall(frame[10:])
            self.bridge.on_read(None, None, None)
            self.assertTrue(json.loads(peer.recv(4096))["ok"])
            self.assertTrue(self.bridge.pending)
            self.assertEqual(SENT, [])
            self.tick()
            self.assertTrue(self.result(task_id)["success"])
        finally:
            peer.close()

    def test_external_client_native_socket_round_trip(self):
        outputs = []
        worker = threading.Thread(target=lambda: outputs.append(
            client.run_py("print('native-round-trip')", timeout=2, confirm_enter=False)))
        worker.start()  # External client only; the mocked GUI runs on this thread.
        deadline = time.monotonic() + 3
        while worker.is_alive() and time.monotonic() < deadline:
            self.bridge.on_accept(None, None, None)
            self.bridge.on_read(None, None, None)
            self.tick()
            time.sleep(0.01)
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outputs, [(True, "native-round-trip\n")])

    def test_corrupt_completion_record_does_not_unlock_unknown_task(self):
        a = self.queue()
        (self.root / "commands" / f"cmd_{a}.json").rename(self.root / "commands" / f"{a}.running.json")
        (self.root / "results" / f"{a}.json").write_text("{broken", encoding="utf-8")
        self.queue()
        self.tick()
        self.assertEqual(SENT, [])
        self.assertEqual(client.get_task_result(a)["state"], "unknown")
        ok, output = client.run_py("print('not submitted')", confirm_enter=False)
        self.assertFalse(ok)
        self.assertIn("OUTCOME_UNKNOWN", output)

    def test_stale_session_notification_rejected(self):
        self.bridge.pending = False
        with socket.create_connection(("127.0.0.1", self.bridge.port), timeout=1) as peer:
            self.bridge.on_accept(None, None, None)
            peer.sendall(b'{"op":"wake","session_id":"old"}\n')
            self.bridge.on_read(None, None, None)
        self.assertFalse(self.bridge.pending)

    def test_diagnostic_close_is_rejected_in_normal_session(self):
        with patch.dict(os.environ, {"ABAQUS_BRIDGE_DIAGNOSTIC": "0"}), \
                patch.object(gui, "shutdown") as shutdown:
            with socket.create_connection(("127.0.0.1", self.bridge.port), timeout=1) as peer:
                self.bridge.on_accept(None, None, None)
                peer.sendall((json.dumps({"op": "diagnostic_close",
                    "session_id": self.bridge.session_id}) + "\n").encode())
                self.bridge.on_read(None, None, None)
                self.assertEqual(peer.recv(4096), b"")
            shutdown.assert_not_called()
        self.assertTrue(self.bridge.active)
        self.assertEqual(SENT, [])

    def test_diagnostic_close_only_stops_explicit_test_session(self):
        with patch.dict(os.environ, {"ABAQUS_BRIDGE_DIAGNOSTIC": "1"}), \
                patch.object(gui, "shutdown") as shutdown, patch.object(gui, "sendCommand") as send:
            with socket.create_connection(("127.0.0.1", self.bridge.port), timeout=1) as peer:
                self.bridge.on_accept(None, None, None)
                peer.sendall((json.dumps({"op": "diagnostic_close",
                    "session_id": self.bridge.session_id}) + "\n").encode())
                self.bridge.on_read(None, None, None)
                self.assertTrue(json.loads(peer.recv(4096))["ok"])
            shutdown.assert_called_once()
            send.assert_called_once_with("from abaqus import mdb; mdb.close()")
        self.assertFalse(self.bridge.active)

    def test_socket_buffers_are_bounded(self):
        with socket.create_connection(("127.0.0.1", self.bridge.port), timeout=1) as peer:
            self.bridge.on_accept(None, None, None)
            peer.sendall(b"x" * 4096)
            self.bridge.on_read(None, None, None)
            peer.sendall(b"x")
            self.bridge.on_read(None, None, None)
        self.assertFalse(self.bridge.clients)

    def test_socket_registration_failure_uses_timer(self):
        self.bridge.stop()
        with patch.object(APP, "addSocket", side_effect=RuntimeError("unsupported")), patch("builtins.print"):
            self.bridge.start()
        self.assertEqual(self.bridge.transport, "timer")
        task_id = self.queue()
        self.tick()
        self.assertTrue(self.result(task_id)["success"])

    def test_client_timer_round_trip_and_durable_result(self):
        self.bridge.transport = "timer"
        self.bridge.write_status()
        with patch.object(client.time, "sleep", side_effect=lambda _: self.tick()):
            ok, output = client.run_py("print('client-output')", timeout=1, confirm_enter=False)
        self.assertTrue(ok)
        self.assertEqual(output, "client-output\n")
        self.assertEqual(len(list((self.root / "results").glob("*.json"))), 1)
        self.assertTrue(client.ping(timeout=0.05) is False)  # no callback pumped

    def test_client_wait_timeout_cancels_unclaimed_task(self):
        self.bridge.transport = "timer"
        self.bridge.write_status()
        ok, output = client.run_py("print('must-not-execute')", timeout=0.01, confirm_enter=False)
        self.assertFalse(ok)
        self.assertIn("cancelled before execution", output)
        self.assertFalse(list((self.root / "commands").glob("cmd_*.json")))
        self.tick()
        self.assertEqual(SENT, [])

    def test_claimed_timeout_preserves_evidence_and_reports_unknown(self):
        self.bridge.transport = "timer"
        self.bridge.write_status()
        def claim_without_completion(_):
            for path in (self.root / "commands").glob("cmd_*.json"):
                path.rename(self.root / "commands" / (path.name[4:-5] + ".running.json"))
        with patch.object(client.time, "sleep", side_effect=claim_without_completion):
            ok, output = client.run_py("print('unknown')", timeout=0.01, confirm_enter=False)
        self.assertFalse(ok)
        self.assertIn("OUTCOME_UNKNOWN", output)
        self.assertEqual(len(list((self.root / "commands").glob("*.running.json"))), 1)

    def test_blocked_timeout_includes_reason_and_preserves_cancelled_evidence(self):
        self.bridge.transport = "timer"
        APP.main.next = FileDialog()
        self.tick()
        with patch.object(client.time, "sleep", side_effect=lambda _: self.tick()):
            ok, output = client.run_py("print('must not execute')", timeout=0.05, confirm_enter=False)
        self.assertFalse(ok)
        self.assertIn("BRIDGE_BLOCKED: A dialog or popup is open.", output)
        self.assertIn("cancelled before execution", output)
        self.assertEqual(SENT, [])
        self.assertEqual(len(list((self.root / "cancelled").glob("*.json"))), 1)

    def test_confirmation_precedes_publication(self):
        self.bridge.transport = "timer"
        self.bridge.write_status()
        def confirm(_):
            self.assertFalse(list((self.root / "commands").glob("cmd_*.json")))
            raise KeyboardInterrupt()
        with patch("builtins.input", side_effect=confirm):
            ok, _ = client.run_py("print('cancel')", confirm_enter=True)
        self.assertFalse(ok)
        self.assertFalse(list((self.root / "commands").glob("cmd_*.json")))

    def test_heartbeat_is_not_pid_only(self):
        status = protocol.read_json(str(self.root / "status.json"))
        status["timestamp"] = time.time() - 50
        protocol.atomic_json(str(self.root / "status.json"), status)
        self.assertFalse(client.is_file_ipc_alive())
        self.bridge.write_status()
        self.assertTrue(client.is_file_ipc_alive())

    def test_external_heartbeat_read_retries_transient_replace_race(self):
        original = client.read_json
        with patch.object(client, "read_json", side_effect=[FileNotFoundError("replace race"),
                original(str(self.root / "status.json"))]) as read:
            self.assertTrue(client.get_ipc_status()["alive"])
        self.assertEqual(read.call_count, 2)

    def test_persistent_heartbeat_read_error_is_bounded_and_visible(self):
        with patch.object(client, "read_json", side_effect=PermissionError("read denied")) as read:
            info = client.get_ipc_status()
        self.assertFalse(info["alive"])
        self.assertIn("read denied", info["read_error"])
        self.assertEqual(read.call_count, 3)

    def test_invalid_identity_cannot_escape_directory(self):
        for value in ("../bad", "a" * 32 + "\n", "a" * 31, None):
            self.assertFalse(protocol.valid_id(value))
            with self.assertRaises(ValueError):
                kernel.execute_task(value, str(self.root))

    def test_atomic_publish_has_no_leftover_temporary_files(self):
        path = self.root / "unicode.json"
        protocol.atomic_json(str(path), {"中文": "内容"})
        protocol.atomic_json(str(path), {"中文": "更新"})
        self.assertEqual(protocol.read_json(str(path)), {"中文": "更新"})
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_transport_probe_works_during_dialog(self):
        self.bridge.transport = "timer"
        self.bridge.write_status()
        APP.main.next = Dialog()
        with patch.object(client.time, "sleep", side_effect=lambda _: self.tick()):
            self.assertTrue(client.ping(timeout=1))
        self.assertEqual(SENT, [])


class AgentTests(unittest.TestCase):
    def test_blocked_bridge_stops_remaining_calls_and_llm_retries(self):
        fake_llm = types.ModuleType("abaqus_agent.agent.llm")
        fake_llm.LLMClient = object
        with patch.dict(sys.modules, {"abaqus_agent.agent.llm": fake_llm}):
            core = importlib.import_module("abaqus_agent.agent.core")
        calls = [{"id": str(i), "type": "function", "function": {
            "name": "abaqus_run_script", "arguments": json.dumps({"script": f"print({i})"})}}
            for i in range(2)]
        chat = MagicMock(return_value=(iter([]), {"message": {
            "role": "assistant", "content": None, "tool_calls": calls}}))
        agent = core.AbaqusAgent(llm_client=types.SimpleNamespace(chat_stream=chat), console=MagicMock())
        with patch.object(core, "dispatch_tool_call", return_value="Failed: BRIDGE_BLOCKED: dialog open") as dispatch:
            output = agent.run_turn("test")
        self.assertIn("BRIDGE_BLOCKED", output)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(chat.call_count, 1)
        results = [m for m in agent.messages if m["role"] == "tool"]
        self.assertEqual(len(results), 2)
        self.assertTrue(results[1]["content"].startswith("NOT_EXECUTED"))

    def test_unknown_outcome_stops_remaining_tool_calls(self):
        fake_llm = types.ModuleType("abaqus_agent.agent.llm")
        fake_llm.LLMClient = object
        with patch.dict(sys.modules, {"abaqus_agent.agent.llm": fake_llm}):
            core = importlib.import_module("abaqus_agent.agent.core")
        calls = [{"id": str(i), "type": "function", "function": {
            "name": "abaqus_run_script", "arguments": json.dumps({"script": f"print({i})"})}}
            for i in range(2)]
        llm = types.SimpleNamespace(chat_stream=lambda *a, **k: (iter([]), {"message": {
            "role": "assistant", "content": None, "tool_calls": calls}}))
        agent = core.AbaqusAgent(llm_client=llm, console=MagicMock())
        with patch.object(core, "dispatch_tool_call", return_value="ERROR: OUTCOME_UNKNOWN: test") as dispatch:
            output = agent.run_turn("test")
        self.assertIn("OUTCOME_UNKNOWN", output)
        self.assertEqual(dispatch.call_count, 1)
        results = [m for m in agent.messages if m["role"] == "tool"]
        self.assertEqual(len(results), 2)
        self.assertTrue(results[1]["content"].startswith("NOT_EXECUTED"))


class LaunchTests(unittest.TestCase):
    def test_workspace_environment_has_no_unknown_helper_keywords(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            client.setup_abaqus_env(Path(directory))
            namespace = {}
            path = Path(directory) / "abaqus_v6.env"
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
            names = set(namespace) - {"__builtins__"}
            self.assertEqual(names, {"plugin_central_dir"})

    def test_environment_setup_preserves_other_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            root = Path(directory)
            env = root / "abaqus_v6.env"
            env.write_text("cpus = 8\n", encoding="utf-8")
            client.setup_abaqus_env(root)
            client.setup_abaqus_env(root)
            content = env.read_text(encoding="utf-8")
            self.assertIn("cpus = 8", content)
            self.assertEqual(content.count("# BEGIN ABAQUS AI BRIDGE"), 1)
            self.assertIn(str(ROOT / "abaqus_plugins").replace("\\", "\\\\"), content)

    def test_busy_existing_gui_is_not_killed_or_relaunched(self):
        with patch.object(client, "ping", return_value=False), patch.object(client, "get_ipc_status", return_value={
            "pid": 123, "status": "executing"}), patch.object(client, "is_pid_alive", return_value=True), \
                patch.object(client.subprocess, "Popen") as spawn:
            ok, _ = client.launch_abaqus_cae()
        self.assertFalse(ok)
        spawn.assert_not_called()

    def test_launch_propagates_resolved_configuration_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            root = Path(directory)
            ipc = root / ".ipc"
            protocol.ensure_dirs(str(ipc))
            evidence = ipc / "results" / "historical.json"
            evidence.write_text('{"evidence":true}', encoding="utf-8")
            with patch.object(client, "IPC_DIR", ipc), patch.object(client, "ping", side_effect=[False, True]), \
                    patch.object(client, "get_ipc_status", return_value={}), \
                    patch.object(client, "find_abaqus_executable", return_value="abaqus"), \
                    patch.object(client.subprocess, "Popen") as spawn:
                ok, _ = client.launch_abaqus_cae(workspace_dir=directory)
            self.assertTrue(ok)
            self.assertTrue(evidence.is_file())
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["ABAQUS_IPC_DIR"], str(ipc))
            self.assertEqual(env["PROJECT_ROOT"], str(ROOT))
            self.assertEqual(spawn.call_args.kwargs["cwd"], directory)


if __name__ == "__main__":
    unittest.main()
