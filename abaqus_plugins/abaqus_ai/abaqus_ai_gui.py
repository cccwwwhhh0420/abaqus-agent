# -*- coding: utf-8 -*-
"""GUI-owned event bridge. No worker thread touches Abaqus model objects."""
from __future__ import print_function

import json
import os
import socket
import sys
import time
import uuid

from abaqusGui import (AFXDialog, FXDialogBox, FXObject, FXPopup, FXMAPFUNC, INPUT_READ,
                       LEFTBUTTONMASK, MIDDLEBUTTONMASK, RIGHTBUTTONMASK,
                       SEL_COMMAND, SEL_IO_READ, SEL_TIMEOUT, getAFXApp,
                       getDisplayedObjectType, sendCommand, shutdown)

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                         "abaqus_agent", "bridge_server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
from bridge_protocol import (PROTOCOL_VERSION, atomic_json, ensure_dirs,
                             cancel_queued_task, completed_result, read_json, resolve_ipc_dir, valid_id)

BRIDGE_VERSION = "2.1.1"
MOUSE_BUTTON_MASK = LEFTBUTTONMASK | MIDDLEBUTTONMASK | RIGHTBUTTONMASK


class GuiBridge(FXObject):
    ID_TICK, ID_ACCEPT, ID_READ, ID_START, ID_STOP = range(1001, 1006)

    def __init__(self):
        FXObject.__init__(self)
        FXMAPFUNC(self, SEL_TIMEOUT, self.ID_TICK, GuiBridge.on_tick)
        FXMAPFUNC(self, SEL_IO_READ, self.ID_ACCEPT, GuiBridge.on_accept)
        FXMAPFUNC(self, SEL_IO_READ, self.ID_READ, GuiBridge.on_read)
        FXMAPFUNC(self, SEL_COMMAND, self.ID_START, GuiBridge.on_start)
        FXMAPFUNC(self, SEL_COMMAND, self.ID_STOP, GuiBridge.on_stop)
        self.app = getAFXApp()
        self.ipc_dir = resolve_ipc_dir()
        self.commands_dir = os.path.join(self.ipc_dir, "commands")
        self.results_dir = os.path.join(self.ipc_dir, "results")
        self.timer = None
        self.listener = None
        self.clients = {}
        self.active = False
        self.executing = False
        self.pending = True
        self.session_id = uuid.uuid4().hex
        self.transport = "timer"
        self.port = int(os.environ.get("ABAQUS_BRIDGE_PORT", "23457"))
        self.reason = ""
        self.gui_state = {}

    def write_status(self, state="idle", message=""):
        atomic_json(os.path.join(self.ipc_dir, "status.json"), {
            "protocol": PROTOCOL_VERSION, "status": state, "message": message,
            "timestamp": time.time(), "pid": os.getpid(),
            "session_id": self.session_id, "transport": self.transport,
            "host": "127.0.0.1", "port": self.port, "ipc_dir": self.ipc_dir,
            "bridge_version": BRIDGE_VERSION, "gui": self.gui_state,
        })

    def start(self):
        if self.active:
            return
        ensure_dirs(self.ipc_dir)
        # An IPC directory belongs to one GUI session, not every open CAE window.
        status_path = os.path.join(self.ipc_dir, "status.json")
        if os.path.isfile(status_path):
            old = read_json(status_path)
            if old.get("session_id") != self.session_id and old.get("status") in (
                    "idle", "blocked", "executing") and time.time() - old.get("timestamp", 0) < 10:
                raise RuntimeError("Another GUI owns this IPC directory; use a separate workspace.")
        self.transport = "timer"
        self.reason = ""
        if hasattr(self.app, "addSocket"):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.bind(("127.0.0.1", self.port))
                listener.listen(8)
                listener.setblocking(False)
                self.app.addSocket(listener.fileno(), INPUT_READ, self, self.ID_ACCEPT)
                self.listener = listener
                self.port = listener.getsockname()[1]
                self.transport = "socket"
            except Exception as exc:
                listener.close()
                self.reason = "Native socket unavailable; timer fallback: " + str(exc)
        self.active = True
        self.pending = True
        stop_file = os.path.join(self.ipc_dir, "stop.flag")
        if os.path.exists(stop_file):
            os.remove(stop_file)
        self.write_status("idle", self.reason)
        self.arm()
        print("[Abaqus-AI] GUI event bridge ready (" + self.transport + ").")

    def arm(self):
        if self.active:
            self.timer = self.app.addTimeout(500, self, self.ID_TICK)

    def close_client(self, fd):
        entry = self.clients.pop(fd, None)
        if entry:
            try:
                self.app.removeSocket(fd, INPUT_READ)
            finally:
                entry["socket"].close()

    def stop(self):
        self.active = False
        if self.timer is not None:
            self.app.removeTimeout(self.timer)
            self.timer = None
        for fd in list(self.clients):
            self.close_client(fd)
        if self.listener:
            self.app.removeSocket(self.listener.fileno(), INPUT_READ)
            self.listener.close()
            self.listener = None
        self.write_status("stopped", "GUI scheduler stopped; running tasks are not interrupted.")

    def on_start(self, sender, sel, ptr):
        self.start()
        return 1

    def on_stop(self, sender, sel, ptr):
        self.stop()
        return 1

    def on_accept(self, sender, sel, ptr):
        # Bounded nonblocking accept: never wait for the external client.
        if not self.active or not self.listener:
            return 1
        try:
            client, _ = self.listener.accept()
        except socket.error:
            return 1
        if len(self.clients) >= 16:
            client.close()
            return 1
        client.setblocking(False)
        fd = client.fileno()
        self.clients[fd] = {"socket": client, "buffer": b"", "created": time.time()}
        self.app.addSocket(fd, INPUT_READ, self, self.ID_READ)
        return 1

    def on_read(self, sender, sel, ptr):
        # FOX bindings differ in the callback data they expose; at most 16 peers.
        for fd, entry in list(self.clients.items()):
            try:
                chunk = entry["socket"].recv(4096)
            except socket.error as exc:
                if getattr(exc, "errno", None) in (11, 35, 10035):
                    continue
                self.close_client(fd)
                continue
            if not chunk:
                self.close_client(fd)
                continue
            entry["buffer"] += chunk
            if len(entry["buffer"]) > 4096:
                self.close_client(fd)
                continue
            if b"\n" not in entry["buffer"]:
                continue
            try:
                request = json.loads(entry["buffer"].split(b"\n", 1)[0].decode("utf-8"))
                if request.get("session_id") != self.session_id:
                    raise ValueError("GUI session changed")
                if request.get("op") == "wake":
                    self.pending = True
                    reply = {"ok": True, "session_id": self.session_id}
                elif request.get("op") == "ping":
                    reply = {"ok": True, "session_id": self.session_id, "status": "executing" if self.executing else "idle"}
                elif (request.get("op") == "diagnostic_close"
                      and os.environ.get("ABAQUS_BRIDGE_DIAGNOSTIC") == "1"):
                    # Used only by the isolated native test, never enabled by
                    # the normal launcher. Close its disposable model and use
                    # CAE's documented File -> Exit equivalent.
                    reply = {"ok": True, "session_id": self.session_id}
                else:
                    raise ValueError("Unknown operation")
                # Small reply, nonblocking. Failed acknowledgement never implies
                # task failure; the client reconciles against the durable queue.
                entry["socket"].send((json.dumps(reply) + "\n").encode("utf-8"))
                if request.get("op") == "diagnostic_close":
                    sendCommand("from abaqus import mdb; mdb.close()")
                    self.stop()
                    shutdown()
            except Exception:
                pass
            finally:
                self.close_client(fd)
        return 1

    def blocked_reason(self):
        """Conservative public GUI checks; unknown guard failures defer execution."""
        self.gui_state = {"mouse_button_mask": int(MOUSE_BUTTON_MASK)}
        try:
            self.gui_state["locked"] = bool(self.app.isLocked())
            if self.gui_state["locked"]:
                return "GUI is locked by another command or mode."
            main = self.app.getAFXMainWindow()
            # FXWindow.isEnabled describes input delivery to this widget, not
            # CAE readiness. Top-level shells can report False while their child
            # widgets remain usable. Use the application lock and dialog checks.
            self.gui_state["displayed_object_type"] = str(getDisplayedObjectType()).upper()
            if self.gui_state["displayed_object_type"] == "SKETCH":
                return "Sketch editing is active."
            status, x, y, input_state = main.getCursorPosition()
            # FOX returns an input bitfield including keyboard modifiers and
            # lock keys. NumLock/CapsLock/Shift alone are not mouse interaction.
            pressed = int(input_state) & MOUSE_BUTTON_MASK
            self.gui_state.update(cursor_query_ok=bool(status),
                                  cursor_state=int(input_state),
                                  pressed_mouse_buttons=int(pressed))
            if status and pressed:
                return "Mouse interaction is active (state=%d, pressed=%d)." % (input_state, pressed)
            shell = self.app.getRoot().getFirst()
            while shell is not None:
                if shell != main and shell.shown() and isinstance(shell, (AFXDialog, FXDialogBox, FXPopup)):
                    self.gui_state["dialog_type"] = type(shell).__name__
                    return "A dialog or popup is open."
                shell = shell.getNext()
            return ""
        except Exception as exc:
            return "Cannot verify GUI readiness: " + str(exc)

    def dispatch_one(self):
        names = os.listdir(self.commands_dir)
        for name in names:
            if name.endswith(".running.json"):
                task_id = name[:-13]
                # A durable result proves completion even if cleanup was interrupted.
                if valid_id(task_id) and completed_result(os.path.join(self.results_dir, task_id + ".json"), task_id):
                    os.remove(os.path.join(self.commands_dir, name))
                else:
                    return "Unresolved running task; inspect /task " + task_id
        queued = sorted((name for name in names if name.startswith("cmd_") and name.endswith(".json")),
                        key=lambda name: os.path.getmtime(os.path.join(self.commands_dir, name)))
        if not queued:
            self.pending = False
            return ""
        name = queued[0]
        task_id = name[4:-5]
        if not valid_id(task_id):
            return "Invalid queued filename; inspect " + name
        task_path = os.path.join(self.commands_dir, name)
        if os.path.isfile(os.path.join(self.results_dir, task_id + ".json")):
            if not completed_result(os.path.join(self.results_dir, task_id + ".json"), task_id):
                return "Invalid completion record; inspect /task " + task_id
            os.remove(task_path)
            return ""
        task = read_json(task_path)
        # Reject obsolete work without invoking the kernel or waiting for a
        # dialog to close. Compete atomically with cancellation/kernel claims.
        if task.get("session_id") != self.session_id:
            cancel_queued_task(self.ipc_dir, task_id,
                               "Task belongs to a different GUI session; not executed.")
            return ""
        if task.get("expires_at", 0) < time.time():
            reason = "Task expired before execution; not executed."
            guard = self.blocked_reason()
            if guard:
                reason += " BRIDGE_BLOCKED: " + guard
            cancel_queued_task(self.ipc_dir, task_id, reason)
            return ""
        # Transport probes do not mutate or query the kernel during modal editing.
        if task.get("type") == "ping":
            atomic_json(os.path.join(self.results_dir, task_id + ".json"), {
                "id": task_id, "session_id": self.session_id, "success": True,
                "state": "succeeded", "output": "pong", "timestamp": time.time()})
            os.remove(task_path)
            return ""
        reason = self.blocked_reason()
        if reason:
            return reason
        self.executing = True
        self.write_status("executing", task_id)
        try:
            # Pass only validated identity and quoted paths, never raw script text
            # through GUI commands. The executor claims the durable task file.
            command = "import sys; sys.path.insert(0, %r); import abaqus_bridge; abaqus_bridge.execute_task(%r, %r, %r)" % (
                SERVER_DIR, task_id, self.ipc_dir, self.session_id)
            sendCommand(command)
        finally:
            self.executing = False
        return ""

    def on_tick(self, sender, sel, ptr):
        if self.executing:
            return 1
        self.timer = None
        if not self.active:
            return 1
        # Do not re-arm while sendCommand runs: nested event loops cannot dispatch
        # a second mutation through this scheduler.
        try:
            if os.path.exists(os.path.join(self.ipc_dir, "stop.flag")):
                self.stop()
                return 1
            for fd, entry in list(self.clients.items()):
                if time.time() - entry["created"] > 3:
                    self.close_client(fd)
            reason = self.dispatch_one() if (self.pending or self.transport == "timer") and not self.executing else ""
            if not reason:
                reason = self.blocked_reason()
            self.write_status("blocked" if reason else "idle", reason or self.reason)
        except Exception as exc:
            self.write_status("blocked", "Dispatch error: " + str(exc))
        finally:
            self.arm()
        return 1
