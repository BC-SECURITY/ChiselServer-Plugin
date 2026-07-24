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
# Any other line chisel scopes to a session: status notices at the default
# verbosity, plus the per-client failures it only logs under -v.
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
        # Set whenever EOF on chisel's stderr is expected: while on_start is
        # still deciding whether the process came up, and through teardown.
        # Outside those windows EOF means chisel died on its own, which is
        # otherwise indistinguishable.
        self.stopping = True
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
        # Stays set until the liveness check below vouches for the process. An
        # exit inside that window is a startup failure, which this method
        # reports by raising; clearing the flag any earlier would race the
        # reader, which reaches EOF within milliseconds of a failed bind --
        # long before the sleep below returns -- and would announce a crash for
        # a server that never started.
        self.stopping = True
        self.recent_stderr.clear()

        chisel_cmd = [self.full_path, "server", "--reverse", "--port", str(self.port)]
        # errors="replace" rather than the default strict: decoding happens in
        # the reader's `for` header, outside its per-line guard, so a single
        # undecodable byte would otherwise kill the drain outright.
        self.chisel_proc = subprocess.Popen(
            chisel_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )

        # chisel announces each reverse tunnel on stderr as it comes up, so
        # this thread is the only thing that records a session -- execute()
        # renders what it collected and no longer touches the pipe. Draining
        # also has to be continuous: let the buffer fill and chisel blocks on
        # write, which stops it servicing tunnels.
        self.reader_thread = threading.Thread(
            target=self._read_sessions,
            args=(self.chisel_proc.stderr, self.chisel_proc),
            daemon=True,
        )
        self.reader_thread.start()

        # Popen succeeding only means the binary was executable -- it says
        # nothing about whether chisel bound the port. Without this check a
        # port conflict produced a green "started and listening" banner, an
        # empty client list, and no record anywhere: chisel writes "bind:
        # address already in use" to stderr, which is not a session line.
        # Best-effort: a chisel that takes longer than this to fail still gets
        # a success banner here, and is caught by the reader's EOF branch.
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

        The guard is per line rather than around the loop. An unparseable line
        has to cost that one line: if it ended the loop, stderr would stop
        being drained, the pipe buffer would fill, and chisel would block on
        write -- silently stalling every tunnel while poll() still reports it
        running and the plugin still reports enabled. The outer guard is the
        backstop for the faults the per-line one cannot see, since the read
        and decode happen in the ``for`` header rather than the body.
        """
        try:
            for raw_line in pipe:
                line = raw_line.rstrip("\n")
                self.recent_stderr.append(line)
                try:
                    self.register_sessions([line])
                except Exception:
                    log.exception("Chisel: could not parse stderr line: %r", line)
        except Exception:
            log.exception("Chisel: stderr reader failed; sessions are now untracked")

        # Otherwise the loop ends only at EOF, i.e. chisel closed stderr, i.e.
        # it exited. Nothing notices that proactively -- execute() does report
        # a dead server, but not until the operator next runs the plugin, so
        # without this a crashed server just sits there looking enabled.
        #
        # `is proc` because a reader orphaned by a chisel that outlived SIGKILL
        # is still parked on the old pipe; when that process finally dies it
        # must not announce a crash against its healthy replacement.
        if self.chisel_proc is proc and not self.stopping:
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
                # SIGKILL can't be caught or ignored, so a timeout means chisel
                # is stuck in the kernel -- typically uninterruptible I/O. The
                # pid goes to the operator because nothing here can reclaim the
                # port; only killing that process will.
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
            # Nothing closes the pipe from this side. Closing a BufferedReader
            # that another thread is blocked reading waits on the buffer lock
            # that reader holds, so it hangs on_stop rather than freeing it --
            # and it would only ever run in the wedged case, since a chisel
            # that died closes its own end. A wedged one leaves the reader
            # parked on a daemon thread, which costs nothing at exit.
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
            message = (
                "Chisel server is not running. Disable and re-enable the plugin "
                "to restart it."
            )
            plugin_task.status = PluginTaskStatus.error
            plugin_task.output = message
            db.add(plugin_task)
            # Returned, not just recorded: the route renders a None result as
            # {"detail": "Plugin executed successfully"}, which is the opposite
            # of what happened.
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

            # Only a tunnel announcement opens a session. Everything else
            # chisel scopes to one is a status or failure notice, and splitting
            # those positionally is what lists fragments of error messages as
            # live connections. Log rather than notify: they are per-client and
            # would flood the operator's notification panel, and the server log
            # already has them.
            if SESSION_LINE.search(line):
                log.warning("Chisel: %s", line)
