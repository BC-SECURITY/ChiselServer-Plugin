import collections
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
#   2024/01/01 00:00:00 server: session#1: tun: proxy#R:127.0.0.1:1080=>socks: Listening
TUNNEL_LINE = re.compile(r"session#(\d+):\s*tun:\s*(.+?):\s*Listening\s*$")
# Any other line chisel scopes to a session: status and per-client failures.
SESSION_LINE = re.compile(r"session#\d+")


class Plugin(BasePlugin):
    @override
    def on_load(self, db):
        self.port = 0
        self.plugin_service: PluginService = self.main_menu.pluginsv2
        self._set_binary()

        self.socks_connections: dict[str, tuple[str, str]] = {}
        self.chisel_proc = None
        self.reader_thread = None
        # Set whenever EOF on chisel's stderr is expected -- during startup and
        # teardown. Outside those windows EOF means chisel died on its own.
        self.stopping = True
        # So a startup failure can quote chisel's diagnostic, not an exit code.
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
        # Stays set until the liveness check vouches for the process. Clearing
        # it earlier races the reader, which hits EOF within milliseconds of a
        # failed bind and would announce a crash for a server that never came
        # up -- a startup failure this method reports by raising instead.
        self.stopping = True
        self.recent_stderr.clear()

        chisel_cmd = [self.full_path, "server", "--reverse", "--port", str(self.port)]
        # errors="replace": decoding happens in the reader's `for` header,
        # outside its per-line guard, so one bad byte would kill the drain.
        self.chisel_proc = subprocess.Popen(
            chisel_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )

        # The only thing that records a session; execute() just renders what it
        # collected. Draining has to be continuous -- let the pipe buffer fill
        # and chisel blocks on write, which stops it servicing tunnels.
        self.reader_thread = threading.Thread(
            target=self._read_sessions,
            args=(self.chisel_proc.stderr, self.chisel_proc),
            daemon=True,
        )
        self.reader_thread.start()

        # Popen succeeding only means the binary was executable, not that
        # chisel bound the port -- a conflict otherwise gave a green "started
        # and listening" banner and an empty client list. Best effort: a slower
        # failure still gets the banner, and the reader's EOF branch catches it.
        time.sleep(0.5)
        if self.chisel_proc.poll() is not None:
            returncode = self.chisel_proc.returncode
            detail = " | ".join(self.recent_stderr) or "no output"
            self.chisel_proc = None
            self.reader_thread = None
            raise PluginValidationException(
                f"Chisel exited immediately (code {returncode}): {detail}"
            )

        self.stopping = False
        self.send_socketio_message(
            f"[+] Chisel server started and listening on http://0.0.0.0:{self.port}",
        )

    def _read_sessions(self, pipe, proc):
        """Drain chisel's stderr, recording sessions as they are announced.

        The guard is per line, not around the loop: an unparseable line must
        cost only that line, or the drain stops and chisel blocks on write
        while poll() still reports it running. The outer guard is the backstop
        for faults the per-line one cannot see, since the read and decode
        happen in the ``for`` header rather than the body.
        """
        reader_failed = False
        try:
            for raw_line in pipe:
                line = raw_line.rstrip("\n")
                # A reader orphaned by a chisel that outlived SIGKILL is still
                # parked on the old pipe. Keep draining, but don't file the old
                # server's sessions against its replacement's listing.
                if self.chisel_proc is not proc:
                    continue
                self.recent_stderr.append(line)
                try:
                    self.register_sessions([line])
                except Exception:
                    log.exception("Chisel: could not parse stderr line: %r", line)
        except Exception:
            reader_failed = True
            log.exception("Chisel: stderr reader failed; sessions are now untracked")

        # Same reason as above: a superseded chisel must not announce a crash
        # against its replacement.
        if self.chisel_proc is not proc or self.stopping:
            return

        # Nothing else notices a dead server until the operator next runs the
        # plugin. The two exits need different words: a failed reader leaves
        # chisel running but undrained, which stalls it rather than killing it,
        # and execute() will keep reporting it healthy.
        if reader_failed:
            log.error("Chisel is running undrained and will stall; restart it")
            message = (
                "[!] Chisel's log reader failed -- the server will stall. "
                "Disable and re-enable the plugin."
            )
        else:
            log.error("Chisel exited unexpectedly; SOCKS tunnels are down")
            message = "[!] Chisel server exited unexpectedly"

        try:
            self.send_socketio_message(message)
        except Exception:
            # Last statement of an unsupervised daemon thread: unhandled, this
            # prints a bare traceback in place of the diagnosis logged above.
            log.exception("Chisel: could not notify operators")

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
                # SIGKILL can't be caught, so a timeout means chisel is stuck in
                # the kernel. The pid goes to the operator because nothing here
                # can reclaim the port -- only killing that process will.
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
            # Deliberately not closing the pipe from this side: closing a
            # BufferedReader another thread is blocked reading waits on the
            # buffer lock that reader holds, hanging on_stop instead of freeing
            # it. A wedged reader just stays parked on a daemon thread.
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

        # Bound once: plugin.enabled is lowered only after on_stop returns, so
        # an execute already past that gate can watch this go None mid-check.
        proc = self.chisel_proc
        if proc is None or proc.poll() is not None:
            # An empty list from a dead server reads exactly like a healthy
            # server nobody has connected to yet.
            message = (
                "Chisel server is not running. Disable and re-enable the plugin "
                "to restart it."
            )
            plugin_task.status = PluginTaskStatus.error
            plugin_task.output = message
            db.add(plugin_task)
            # Returned, not just recorded: the route renders None as
            # "Plugin executed successfully", the opposite of what happened.
            return message

        # Snapshot: the reader thread writes socks_connections, and iterating
        # it live can raise "dictionary changed size during iteration".
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
        # The listing goes in the task, not the response: the route would
        # render a returned string as the operator's whole result.
        return None

    def register_sessions(self, output_lines):
        for line in output_lines:
            tunnel = TUNNEL_LINE.search(line)
            if tunnel:
                session_number, connection = tunnel.group(1), tunnel.group(2)
                opened_at = " ".join(line.split(" ")[:2])
                self.socks_connections[session_number] = (connection, opened_at)
                continue

            # Only a tunnel announcement opens a session -- parsing the rest
            # positionally is what listed fragments of error messages as live
            # connections. Logged, not notified: these are per-client and would
            # flood the operator's notification panel.
            if SESSION_LINE.search(line):
                log.warning("Chisel: %s", line)
