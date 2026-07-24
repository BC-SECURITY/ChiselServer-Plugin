import collections
import contextlib
import logging
import os
import platform
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from empire.server.core.db import models
from empire.server.core.db.models import PluginTaskStatus
from empire.server.core.exceptions import (
    PluginLoadException,
    PluginValidationException,
)
from empire.server.core.plugins import BasePlugin

if TYPE_CHECKING:
    from empire.server.core.plugin_service import PluginService

log = logging.getLogger(__name__)

# A tunnel that came up, e.g.
#   2026/07/24 00:13:10 server: session#1: tun: proxy#R:127.0.0.1:1080=>socks: Listening
TUNNEL_LINE = re.compile(r"session#(\d+):\s*tun:\s*(.+?):\s*Listening\s*$")
# Any other line chisel attributes to a session -- rejections and failures.
SESSION_LINE = re.compile(r"session#(\d+)")


class Plugin(BasePlugin):
    @override
    def on_load(self, db):
        self.port = 0
        self.plugin_service: PluginService = self.main_menu.pluginsv2
        self._set_binary()

        self.socks_connections: dict[str, tuple[str, str]] = {}
        self.chisel_proc = None
        self.reader_thread = None
        # Distinguishes "the reader hit EOF because we killed chisel" from
        # "chisel died on its own", which is otherwise indistinguishable.
        self.stopping = False
        # Kept so a startup failure can quote chisel's own diagnostic, which
        # is far more useful than an exit code.
        self.recent_stderr = collections.deque(maxlen=10)
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
        self.stopping = False
        self.recent_stderr.clear()

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

        # Popen succeeding only means the binary was executable -- it says
        # nothing about whether chisel bound the port. Without this check a
        # port conflict produced a green "started and listening" banner, an
        # empty client list, and no record anywhere: chisel writes "bind:
        # address already in use" to stderr, which is not a session line.
        time.sleep(0.5)
        if self.chisel_proc.poll() is not None:
            returncode = self.chisel_proc.returncode
            detail = " | ".join(self.recent_stderr) or "no output"
            self.chisel_proc = None
            self.reader_thread = None
            raise PluginValidationException(
                f"Chisel exited immediately (code {returncode}): {detail}"
            )

        self.send_socketio_message(
            f"[+] Chisel server started and listening on http://0.0.0.0:{self.port}",
        )

    def _read_sessions(self, pipe):
        """Drain chisel's stderr, recording sessions as they are announced.

        The guard is per line rather than around the loop. An unparseable line
        has to cost that one line: if it ended the loop, stderr would stop
        being drained, the 64KB pipe buffer would fill, and chisel would block
        on write -- silently stalling every tunnel while poll() still reports
        it running and the plugin still reports enabled.
        """
        for raw_line in pipe:
            line = raw_line.rstrip("\n")
            self.recent_stderr.append(line)
            try:
                self.register_sessions([line])
            except Exception:
                log.exception("Chisel: could not parse stderr line: %r", line)

        # The loop only ends at EOF, i.e. chisel closed stderr, i.e. it exited.
        # Nothing else notices that, so a crashed server would otherwise keep
        # reporting enabled and serving a stale client list all engagement.
        if not self.stopping:
            log.error("Chisel exited unexpectedly; SOCKS tunnels are down")
            self.send_socketio_message("[!] Chisel server exited unexpectedly")

    @override
    def on_stop(self, db):
        # on_stop runs for every loaded plugin on shutdown, started or not, so
        # both handles may still be None.
        started = self.chisel_proc is not None
        self.stopping = True

        if self.chisel_proc is not None:
            self.chisel_proc.kill()
            try:
                self.chisel_proc.wait(timeout=5)
                self.chisel_proc = None
            except subprocess.TimeoutExpired:
                # SIGKILL is uncatchable, so this means chisel is wedged in
                # uninterruptible I/O. Keep the handle rather than dropping the
                # only reference to a live process that still holds the port.
                log.error(
                    "Chisel (pid %s) did not exit within 5s of SIGKILL and may "
                    "still hold port %s; kill it before re-enabling the plugin.",
                    self.chisel_proc.pid,
                    self.port,
                )
                self.send_socketio_message(
                    f"[!] Chisel (pid {self.chisel_proc.pid}) would not exit and "
                    f"may still hold port {self.port}"
                )

        if self.reader_thread is not None:
            # Close the pipe explicitly so the reader unblocks even when chisel
            # itself is wedged and never closed its end.
            if self.chisel_proc is not None and self.chisel_proc.stderr is not None:
                with contextlib.suppress(OSError):
                    self.chisel_proc.stderr.close()
            self.reader_thread.join(timeout=5)
            if self.reader_thread.is_alive():
                log.error("Chisel stderr reader did not exit within 5s")
            self.reader_thread = None

        self.socks_connections = {}
        if started:
            self.send_socketio_message("[!] Stopped Chisel server")

    @override
    def execute(self, command, **kwargs):
        user = kwargs["user"]
        db = kwargs["db"]
        input = "Getting Chisel sessions..."
        plugin_task = models.PluginTask(
            plugin_id=self.info.id,
            input=input,
            input_full=input,
            user_id=user.id if user else None,
            status=PluginTaskStatus.completed,
        )

        if self.chisel_proc is None or self.chisel_proc.poll() is not None:
            # Don't render a session list we can't vouch for -- an empty list
            # from a dead server reads exactly like a healthy server nobody has
            # connected to yet.
            plugin_task.status = PluginTaskStatus.error
            plugin_task.output = (
                "Chisel server is not running. Disable and re-enable the plugin "
                "to restart it."
            )
            db.add(plugin_task)
            return

        # Copy under the GIL so the reader thread can't mutate mid-render.
        connections = dict(self.socks_connections)
        if not connections:
            plugin_task.output = "No Chisel sessions opened yet!"
        else:
            output = "  Session ID\tConnection Time\t\tConnection"
            output += "\n  ----------\t---------------\t\t----------"
            for session, (connection, opened_at) in connections.items():
                output += f"\n  {session}       \t{connection}  \t{opened_at}"

            plugin_task.output = output

        db.add(plugin_task)

    def register_sessions(self, output_lines):
        for line in output_lines:
            tunnel = TUNNEL_LINE.search(line)
            if tunnel:
                # Multi-digit: the old code took a single character, so
                # session#10 was filed under "1", silently overwriting
                # session 1 and losing it from the listing entirely.
                session_number, connection = tunnel.group(1), tunnel.group(2)
                opened_at = " ".join(line.split(" ")[:2])
                self.socks_connections[session_number] = (connection, opened_at)
                continue

            # Any other session line is chisel rejecting or failing a client.
            # These used to be split positionally and recorded as live tunnels:
            # "Failed to handshake (websocket: bad handshake)" happens to have
            # four ": "-separated segments, so it parsed cleanly and showed up
            # in the listing with a connection string of "bad handshake)".
            # Log rather than notify -- this runs on the reader thread, and
            # send_socketio_message there emits against a foreign event loop.
            if SESSION_LINE.search(line):
                log.warning("Chisel: %s", line)
