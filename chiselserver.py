
import os
import platform
import select
import subprocess

from empire.server.common.plugins import BasePlugin
from empire.server.core.plugin_service import PluginService


# REQUIRES chisel server binaries to be placed in the data/misc directory with names chiselserver_linux and chiselserver_mac
class Plugin(BasePlugin):
    def onLoad(self):
        self.main_menu = None
        self.enabled = False
        self.socks_connections = {}
        self.connection_times = {}
        self.port = None
        self.chisel_proc = None

        self.options = {
            "status": {
                "Description": "<start/stop/status>",
                "Required": True,
                "Value": "start",
                "SuggestedValues": ["start", "stop", "status"],
                "Strict": True,
            },
            "port": {"Description": "Port number.", "Required": True, "Value": "8080"},
        }

    def execute(self, command):
        """
        Any modifications made to the main menu are done here
        (meant to be overriden by child)
        """
        try:
            results = self.do_chiselserver(command)
            return results
        except Exception as e:
            print(e)
            return False

    def register(self, main_menu):
        """
        Any modifications to the main_menu go here - e.g.
        registering functions to be run by user commands
        """
        main_menu.__class__.do_chiselserver = self.do_chiselserver
        self.installPath = main_menu.installPath
        self.main_menu = main_menu
        self.plugin_service: PluginService = main_menu.pluginsv2

    def do_chiselserver(self, command):
        """
        Launch chisel server
        """

        # Used to get output lines from a subprocess pipe
        def get_output_lines(pipe):
            r, w, e = select.select([pipe], [], [], 0)
            if pipe in r:
                output = pipe.buffer.read1().decode("utf-8").split("\n")
                if output[-1] == "":
                    del output[-1]
                return output
            else:
                return []

        def register_sessions(output_lines):
            session_lines = [x for x in output_lines if "session#" in x]
            for line in session_lines:
                # Ugly string searches
                session_number = line[line.find("session#") + 8]
                time = " ".join(line.split(" ")[:2])
                try:
                    connection = line.split(": ")[3]
                    self.socks_connections[session_number] = connection
                    self.connection_times[session_number] = time
                except:
                    # Capture error message or warning
                    error_message = line[
                        line.find("session#" + session_number)
                        + len("session#" + session_number)
                        + 2 :
                    ]
                    self.plugin_service.plugin_socketio_message(
                        self.info["Name"], "[!] Warning: " + error_message
                    )

        # Check if the Chisel server is already running
        if self.chisel_proc:
            self.enabled = True
        else:
            self.enabled = False

        # API will pass arguments and still give this message.
        self.start = command["status"]
        self.port = command["port"]

        if self.start == "status":
            if self.enabled:
                register_sessions(get_output_lines(self.chisel_proc.stderr))
                self.plugin_service.plugin_socketio_message(
                    self.info["Name"],
                    "[*] Chisel server is enabled and "
                    "listening on port %s" % self.port,
                )
                if not self.socks_connections:
                    self.plugin_service.plugin_socketio_message(
                        self.info["Name"], "[*] No connected Chisel clients!"
                    )
                else:
                    self.plugin_service.plugin_socketio_message(
                        self.info["Name"],
                        "  Session ID\tConnection Time\t\tConnection"
                        + "\n  ----------\t---------------\t\t----------",
                    )
                    for session in self.connection_times.keys():
                        self.plugin_service.plugin_socketio_message(
                            self.info["Name"],
                            "  %s       \t%s  \t%s"
                            % (
                                session,
                                self.connection_times[session],
                                self.socks_connections[session],
                            )
                            + "\n",
                        )
            else:
                self.plugin_service.plugin_socketio_message(
                    self.info["Name"], "[!] Chisel server is disabled"
                )

        elif self.start == "stop":
            if self.enabled:
                self.chisel_proc.kill()
                self.socks_connections = {}
                self.connection_times = {}
                self.plugin_service.plugin_socketio_message(
                    self.info["Name"], "[!] Stopped Chisel server"
                )
            else:
                self.plugin_service.plugin_socketio_message(
                    self.info["Name"], "[!] Chisel server is already stopped"
                )

        elif self.start == "start":
            if not self.enabled:
                self.port = command["port"]
                if platform.system() == "Darwin":
                    self.binary = "chiselserver_darwin"

                elif platform.system() == "Linux":
                    self.binary = "chiselserver_linux"

                else:
                    self.plugin_service.plugin_socketio_message(
                        self.info["Name"],
                        "[!] Chisel server unsupported "
                        "platform: %s" % platform.system(),
                    )
                    return

                self.fullPath = (
                    self.installPath + "/plugins/ChiselServer-Plugin/" + self.binary
                )
                if not os.path.exists(self.fullPath):
                    self.plugin_service.plugin_socketio_message(
                        self.info["Name"],
                        "[!] Chisel server binary does not "
                        "exist: %s" % self.fullPath,
                    )
                    return
                elif not os.access(self.fullPath, os.X_OK):
                    self.plugin_service.plugin_socketio_message(
                        self.info["Name"],
                        "[*] Chisel server binary does not have"
                        " execute permission -- Setting it now",
                    )
                    mode = os.stat(self.fullPath).st_mode
                    mode += 0o100  # Octal 100
                    os.chmod(self.fullPath, mode)

                chisel_cmd = [self.fullPath, "server", "--reverse", "--port", self.port]
                self.chisel_proc = subprocess.Popen(
                    chisel_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=1,
                    universal_newlines=True,
                )
                self.plugin_service.plugin_socketio_message(
                    self.info["Name"],
                    "[+] Chisel server started and listening on http://0.0.0.0:%s"
                    % self.port,
                )
            else:
                self.plugin_service.plugin_socketio_message(
                    self.info["Name"], "[!] Chisel server is already started"
                )

        else:
            self.plugin_service.plugin_socketio_message(
                self.info["Name"], "[!] Usage: chiselserver <start|stop|status> [port]"
            )

    def shutdown(self):
        """
        Kills additional processes that were spawned
        """
        try:
            self.chisel_proc.kill()
        except:
            pass