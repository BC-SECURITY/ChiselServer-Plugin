import contextlib
import os
import platform
import select
import subprocess
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


class Plugin(BasePlugin):
    @override
    def on_load(self, db):
        self.port = 0
        self.plugin_service: PluginService = self.main_menu.pluginsv2
        self._set_binary()

        self.socks_connections: dict[str, tuple[str, str]] = {}
        self.chisel_proc = None
        self.execution_enabled = False
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

        chisel_cmd = [self.full_path, "server", "--reverse", "--port", self.port]
        self.chisel_proc = subprocess.Popen(
            chisel_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            universal_newlines=True,
        )

        self.send_socketio_message(
            f"[+] Chisel server started and listening on http://0.0.0.0:{self.port}",
        )

    @override
    def on_stop(self, db):
        self.send_socketio_message("[!] Stopped Chisel server")

        with contextlib.suppress(Exception):
            self.socks_connections = {}
            self.chisel_proc.kill()
            self.chisel_proc = None

    @override
    def execute(self, command, **kwargs):
        user = kwargs["user"]
        db = kwargs["db"]
        input = "Getting connected Chisel clients..."
        plugin_task = models.PluginTask(
            plugin_id=self.info.name,
            input=input,
            input_full=input,
            user_id=user.id,
            status=PluginTaskStatus.completed,
        )
        self.register_sessions(self.get_output_lines(self.chisel_proc.stderr))
        if not self.socks_connections:
            plugin_task.output = "No connected Chisel clients!"
        else:
            output = "  Session ID\tConnection Time\t\tConnection"
            output += "\n  ----------\t---------------\t\t----------"
            for session, (connection, time) in self.socks_connections.items():
                output += f"\n  {session}       \t{connection}  \t{time}"

            plugin_task.output = output

        db.add(plugin_task)

    @staticmethod
    def get_output_lines(pipe):
        r, w, e = select.select([pipe], [], [], 0)
        if pipe in r:
            output = pipe.buffer.read1().decode("utf-8").split("\n")
            if output[-1] == "":
                del output[-1]
            return output
        return []

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
