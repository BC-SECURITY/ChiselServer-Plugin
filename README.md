# SharpChisel-Plugin
The SharpChisel Plugin runs the Chisel server for Invoke-SharpChisel. The Powershell script loads SharpChisel which has the embeded Golang Chisel binary 
-- client only to save space. This plugin is entirely contained in [Empire](https://github.com/BC-SECURITY/Empire/)
and runs in the background. Use command `start` to configure and start the Chisel Server. You can shutdown
the socks proxy by running the command `stop` or by exiting Empire. Check out the original [Chisel project](https://github.com/jpillora/chisel) 
and [SharpChisel](https://github.com/shantanu561993/SharpChisel).

## Getting Started
* To run the plugin, you can download it fom the releases [Releases](https://github.com/BC-SECURITY/ChiselServer-Plugin/releases) page. 

## Install
Prerequisites:
- Empire >= 4.0.0

1. Add chiselserver.py to plugins folder of Empire.

![image](https://user-images.githubusercontent.com/20302208/120246969-b4098280-c226-11eb-9345-4ff994ee5312.png)

2. Add [Chisel binaries](https://github.com/jpillora/chisel/releases) to the /empire/server/data/misc folder. Files must be labeled as chiselserver_mac or chiselserver_linux 
   depending on your platform.

![image](https://user-images.githubusercontent.com/20302208/120248850-a277a900-c22d-11eb-87e6-df3220089444.png)

## Usage
### Client
![image](https://user-images.githubusercontent.com/20302208/120249004-3c3f5600-c22e-11eb-962c-c9107c77b624.gif)

## Contributions
Plugin created by [Kevin Clark](https://gitlab.com/KevinJClark/invoke-sharpchisel/)
