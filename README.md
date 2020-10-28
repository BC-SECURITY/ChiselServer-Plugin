# SharpChisel-Plugin
The SharpChisel Plugin runs the Chisel server for Invoke-SharpChisel. The Powershell script loads SharpChisel which has the embeded Golang Chisel binary 
-- client only to save space. This plugin is entirely contained in [Empire](https://github.com/BC-SECURITY/Empire/)
and runs in the background.

`sharpchisel <start|stop> [port]'

![image](https://user-images.githubusercontent.com/20302208/97376340-5a00a300-187a-11eb-902d-ec9b75389beb.png)

Use command `chiselserver start` to configure and start the Chisel Server. You can shutdown
the socks proxy by running the command `chiselserver stop` or by exiting Empire. Check out the original [Chisel project](https://github.com/jpillora/chisel) 
and [SharpChisel](https://github.com/shantanu561993/SharpChisel).


## Getting Started
* To run the plugin, you can download it fom the releases [Releases](https://github.com/BC-SECURITY/ChiselServer-Plugin/releases) page. 

## Install
Prerequisites:
- Empire 3.6.0+

1. Add ChiselServer.py and binaries to the plugins folder of Empire. Make sure to put your compiled chisel binary in the data/misc directory, calling it chiselserver_mac or chiselserver_linux depending on your platform


![image](https://user-images.githubusercontent.com/20302208/95636534-49f85f00-0a44-11eb-87c1-754a2368febb.png)


2. Plugins are automatically loaded into Empire as of 3.4.0, otherwise run ```plugin ChiselServer```

![image](https://user-images.githubusercontent.com/20302208/95636737-b5dac780-0a44-11eb-9f82-34dcb66c24fe.png)
