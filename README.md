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
- Empire >= 4.3

1. Git clone the ChiselServer-Plugin repo into the plugins folder.

![image](https://user-images.githubusercontent.com/20302208/143662717-651f0220-b4de-4bc6-832a-5444c9ace2e6.png)

## Usage
### Client
![image](https://user-images.githubusercontent.com/20302208/120249004-3c3f5600-c22e-11eb-962c-c9107c77b624.gif)

## Contributions
Plugin created by [Kevin Clark](https://gitlab.com/KevinJClark/invoke-sharpchisel/)
