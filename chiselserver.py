from __future__ import print_function

from lib.common.plugins import Plugin
import lib.common.helpers as helpers

import subprocess
import platform


# REQUIRES chisel server binaries to be placed in the data/misc directory with names chiselserver_linux and chiselserver_mac
class Plugin(Plugin):
    description = "Chisel server plugin."

    def onLoad(self):
        print(helpers.color("[*] Loading Chisel server plugin"))

        self.chisel_proc = None
        self.info = {
                        'Name': 'chiselserver',

                        'Author': ['@kevin'],

                        'Description': ('Chisel server for invoke_sharpchisel module.'),

                        'Software': '',

                        'Techniques': [''],

                        'Comments': []
                    },

        self.options = {
                        'status': {
                            'Description': 'Start/stop the Chisel server. Specify a port or default to 8080.',
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

    def get_commands(self):
        return self.commands

    def register(self, mainMenu):
        """ any modifications to the mainMenu go here - e.g.
        registering functions to be run by user commands """
        mainMenu.__class__.do_chiselserver = self.do_chiselserver
        self.installPath = mainMenu.installPath

    def do_chiselserver(self, *args):
        "Check if the Chisel server is already running."

        if len(args[0]) > 0:
            self.start = args[0]
            try:
                self.port = args[1]
            except:
                self.port = self.options['port']['Value']
        else:
            self.start = self.options['status']['Value']
            self.port = self.options['port']['Value']

        if (not self.chisel_proc or self.chisel_proc.poll()):
            self.status = "OFF"
        else:
            self.status = "ON"

        if not args:
            print(helpers.color("[*] Chisel server is currently: %s" % self.status))
            print("[!] chiselserver <start|stop> <port>")

        elif (self.start == "stop"):
            if (self.status == "ON"):
                self.chisel_proc.kill()
                print(helpers.color("[*] Stopping Chisel server"))
            else:
                print(helpers.color("[!] Chisel server is already stopped"))

        elif (self.start == "start"):
            if (self.status == "OFF"):
                print(helpers.color("[*] Starting Chisel server"))
                if (platform.system() == "Darwin"):
                    self.binary = "chiselserver_mac"
                elif (platform.system() == "Linux"):
                    self.binary = "chiselserver_linux"
                else:
                    print(helpers.color("[!] Chisel server not supported platform: %s" % platform.system()))
                    return

                chisel_cmd = [self.installPath + "/data/misc/" + self.binary, "server", "--reverse", "--port",
                              self.port]
                self.chisel_proc = subprocess.Popen(chisel_cmd)
            else:
                print(helpers.color("[!] Chisel server is already started"))

    def shutdown(self):
        try:
            self.chisel_proc.kill()
        except:
            pass
        return
