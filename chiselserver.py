import contextlib
import logging
import os
import platform
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from empire.server.core.db import models
from empire.server.core.db.models import PluginTaskStatus
from empire.server.core.exceptions import (
    PluginLoadException,
)
from empire.server.core.plugins import BasePlugin

if TYPE_CHECKING:
    from empire.server.core.plugin_service import PluginService

log = logging.getLogger(__name__)


class Plugin(BasePlugin):
    @override
    def on_load(self, db):
        self.port = 0
        self.plugin_service: PluginService = self.main_menu.pluginsv2
        self._set_binary()

        self.socks_connections: dict[str, tuple[str, str]] = {}
        self.chisel_proc = None
        self.reader_thread = None
        self.settings_options = {
            "port": {"Description": "Port number.", "Required": True, "Value": 8080},
        }

    def _set_binary(self):
        if platform.system() == "Darwin":
            self.binary = "chiselserver_darwin"
        elif platform.system() == "Linux":
            self.binary = "chiselserver_linux"
        else:
            raise PluginLoadException("Unsupported platform")

        self.full_path = Path(__file__).parent / self.binary
        if not self.full_path.exists():
            raise PluginLoadException("Chisel server binary does not exist")

        if not os.access(self.full_path, os.X_OK):
            self.full_path.chmod(self.full_path.stat().st_mode | 0o100)

    @override
    def on_settings_change(self, db, settings: dict[str, Any]):
        if settings["port"] != self.port and self.enabled:
            self.send_socketio_message(
                "Port changed, restart the plugin to apply changes"
            )

    @override
    def on_start(self, db):
        self.port = self.current_settings(db)["port"]

        chisel_cmd = [self.full_path, "server", "--reverse", "--port", str(self.port)]
        self.chisel_proc = subprocess.Popen(
            chisel_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True,
        )

        # chisel logs a line to stderr for every session it accepts. That pipe
        # has to be drained continuously or the buffer fills and chisel blocks
        # on write, which stops it servicing tunnels. Reading it here also keeps
        # socks_connections current instead of only refreshing on execute().
        self.reader_thread = threading.Thread(
            target=self._read_sessions,
            args=(self.chisel_proc.stderr,),
            daemon=True,
        )
        self.reader_thread.start()

        self.send_socketio_message(
            f"[+] Chisel server started and listening on http://0.0.0.0:{self.port}",
        )

    def _read_sessions(self, pipe):
        """Drain chisel's stderr, recording sessions as they are announced."""
        try:
            for line in pipe:
                self.register_sessions([line.rstrip("\n")])
        except Exception:
            log.warning("Chisel stderr reader stopped", exc_info=True)

    @override
    def on_stop(self, db):
        # on_stop runs for every loaded plugin on shutdown, started or not, so
        # both handles may still be None.
        if self.chisel_proc is not None:
            self.chisel_proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.chisel_proc.wait(timeout=5)
            self.chisel_proc = None

        if self.reader_thread is not None:
            # Killing chisel closes the pipe, which ends the reader's loop.
            self.reader_thread.join(timeout=5)
            if self.reader_thread.is_alive():
                log.warning("Chisel stderr reader did not exit within 5s")
            self.reader_thread = None

        self.socks_connections = {}
        self.send_socketio_message("[!] Stopped Chisel server")

    @override
    def execute(self, command, **kwargs):
        user = kwargs["user"]
        db = kwargs["db"]
        input = "Getting connected Chisel clients..."
        plugin_task = models.PluginTask(
            plugin_id=self.info.id,
            input=input,
            input_full=input,
            user_id=user.id,
            status=PluginTaskStatus.completed,
        )
        # Snapshot: the reader thread mutates this while we iterate.
        connections = dict(self.socks_connections)
        if not connections:
            plugin_task.output = "No connected Chisel clients!"
        else:
            output = "  Session ID\tConnection Time\t\tConnection"
            output += "\n  ----------\t---------------\t\t----------"
            for session, (connection, time) in connections.items():
                output += f"\n  {session}       \t{connection}  \t{time}"

            plugin_task.output = output

        db.add(plugin_task)

    def register_sessions(self, output_lines):
        session_lines = [x for x in output_lines if "session#" in x]
        for line in session_lines:
            # Ugly string searches
            session_number = line[line.find("session#") + 8]
            time = " ".join(line.split(" ")[:2])
            try:
                connection = line.split(": ")[3]
                self.socks_connections[session_number] = (connection, time)
            except Exception:
                error_message = line[
                    line.find("session#" + session_number)
                    + len("session#" + session_number)
                    + 2 :
                ]
                self.send_socketio_message("[!] Warning: " + error_message)
