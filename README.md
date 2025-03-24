# SharpChisel-Plugin
The SharpChisel Plugin runs the Chisel server for Invoke-SharpChisel. The Powershell script loads SharpChisel which has the embeded Golang Chisel binary 
-- client only to save space. This plugin is entirely contained in [Empire](https://github.com/BC-SECURITY/Empire/)
and runs in the background. Use command `start` to configure and start the Chisel Server. You can shutdown
the socks proxy by running the command `stop` or by exiting Empire. Check out the original [Chisel project](https://github.com/jpillora/chisel) 
and [SharpChisel](https://github.com/shantanu561993/SharpChisel).

## Install
Prerequisites:
- Empire >= 6.0

1. Install via Starkiller plugin marketplace

![image](![image](https://github.com/user-attachments/assets/3160a771-fd12-42f0-86d2-b52776f269e0))

## Usage

1. Once installed go to the installed plugin page
2. Set the port for the server to listen on. It defaults to 1080
3. Enable the server with the toggle on the top right
4. Once running deploy the Invoke-SharpChisel module on the agent
5. Ensure that the socksproxy4.conf file is properly configured for port 1080
   ![image](https://github.com/user-attachments/assets/2ff14f2c-e5e4-4387-ab3c-272a43044c8f)
6. You should now be able to run tooling through proxychains 

## Contributions
Plugin created by [Kevin Clark](https://gitlab.com/KevinJClark/invoke-sharpchisel/)
