from __future__ import print_function
from lib.common.plugins import Plugin
import lib.common.helpers as helpers

import subprocess
import platform
import os
import select


# REQUIRES chisel server binaries to be placed in the data/misc directory with names chiselserver_linux and chiselserver_mac
class Plugin(Plugin):
    description = "Chisel server plugin."

    def onLoad(self):
        print(helpers.color("[*] Loading Chisel server plugin"))

        self.enabled = False
        self.socks_connections = {}
        self.connection_times = {}
        self.port = None
        self.chisel_proc = None

        self.info = {
                        # At the moment this much match the do_ command
                        'Name': 'chiselserver',

                        'Author': ['@kevin'],

                        'Description': ('Chisel server for invoke_sharpchisel module.'),

                        'Software': '',

                        'Techniques': ['T1090'],

                        'Comments': ['https://gitlab.com/KevinJClark/invoke-sharpchisel/']
                    },

        self.options = {
                        'status': {
                            'Description': '<start/stop/status>',
                            'Required': True,
                            'Value': 'start'
                        },
                        'port': {
                            'Description': 'Port number.',
                            'Required': True,
                            'Value': '8080'
                        },
                    }

    def execute(self, command):
        # This is for parsing commands through the api
        try:
            # essentially switches to parse the proper command to execute
            self.options['status']['Value'] = command['status']
            self.options['port']['Value'] = command['port']
            results = self.do_chiselserver('')
            return results
        except:
            return False

    def register(self, mainMenu):
        """
        Any modifications to the mainMenu go here - e.g.
        registering functions to be run by user commands
        """

        mainMenu.__class__.do_chiselserver = self.do_chiselserver
        self.installPath = mainMenu.installPath

    def do_chiselserver(self, args):
        """
        Check if the Chisel server is already running.

        Usage: chiselserver <start|stop> <port>
        """

        # Used to get output lines from a subprocess pipe
        def get_output_lines(pipe):
            r, w, e = select.select([pipe], [], [], 0)
            if pipe in r:
                output = pipe.buffer.read1().decode('utf-8').split("\n")
                if output[-1] == '':
                    del output[-1]
                return output
            else:
                return []

        def register_sessions(output_lines):
            session_lines = [x for x in output_lines if 'session#' in x]
            for line in session_lines:
                # Ugly string searches
                session_number = line[line.find('session#') + 8]
                time = " ".join(line.split(" ")[:2])
                try:
                    connection = line.split(": ")[3]
                    self.socks_connections[session_number] = connection
                    self.connection_times[session_number] = time
                except:
                    # Capture error message or warning
                    error_message = line[line.find('session#' + session_number) + len('session#' + session_number) + 2:]
                    print(helpers.color("[!] Warning: " + error_message))

        # Check if the Chisel server is already running
        if not self.chisel_proc or self.chisel_proc.poll():
            self.enabled = False
        else:
            self.enabled = True

        # If no arguments then run with defaults. API will pass arguments and still give this message.
        if not args:
            print(helpers.color("[!] Usage: chiselserver <start|stop|status> [port]"))
            self.start = self.options['status']['Value']
            self.port = self.options['port']['Value']
            print(helpers.color("[+] Defaults: chiselserver " + self.start + " " + self.port))
        else:
            self.start = args.split(" ")[0]

        if self.start == "status":
            if self.enabled:
                register_sessions(get_output_lines(self.chisel_proc.stderr))
                print(helpers.color("[*] Chisel server is enabled and listening on port %s" % self.port))
                if not self.socks_connections:
                    print(helpers.color("[*] No connected Chisel clients!"))
                else:
                    print("  Session ID\tConnection Time\t\tConnection")
                    print("  ----------\t---------------\t\t----------")
                    for session in self.connection_times.keys():
                        print("  %s       \t%s  \t%s" % (
                            session, self.connection_times[session], self.socks_connections[session]))
                    print()

            else:
                print(helpers.color("[*] Chisel server is disabled"))

        elif self.start == "stop":
            if self.enabled:
                self.chisel_proc.kill()
                self.socks_connections = {}
                self.connection_times = {}
                print(helpers.color("[*] Stopped Chisel server"))
            else:
                print(helpers.color("[!] Chisel server is already stopped"))

        elif self.start == "start":
            if not self.enabled:
                try:
                    self.port = args.split(" ")[1]
                except:
                    self.port = self.options['port']['Value']

                if platform.system() == "Darwin":
                    self.binary = "chiselserver_mac"

                elif platform.system() == "Linux":
                    self.binary = "chiselserver_linux"

                else:
                    print(helpers.color("[!] Chisel server unsupported platform: %s" % platform.system()))
                    return

                self.fullPath = self.installPath + "/data/misc/" + self.binary
                if not os.path.exists(self.fullPath):
                    print(helpers.color("[!] Chisel server binary does not exist: %s" % self.fullPath))
                    return
                elif not os.access(self.fullPath, os.X_OK):
                    print(helpers.color("[*] Chisel server binary does not have execute permission -- Setting it now"))
                    mode = os.stat(self.fullPath).st_mode
                    mode += 0o100  # Octal 100
                    os.chmod(self.fullPath, mode)

                chisel_cmd = [self.fullPath, "server", "--reverse", "--port", self.port]
                self.chisel_proc = subprocess.Popen(chisel_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                    bufsize=1, universal_newlines=True)
                print(helpers.color("[+] Chisel server started and listening on http://0.0.0.0:%s" % self.port))
            else:
                print(helpers.color("[!] Chisel server is already started"))

        else:
            print(helpers.color("[!] Usage: chiselserver <start|stop|status> [port]"))

    def shutdown(self):
        """
        Kills additional processes that were spawned
        """

        try:
            self.chisel_proc.kill()
        except:
            pass
