компиляция в 64 битной командной строке
cl /EHsc /LD /Fe:bluetooth_transfer.dll bluetoothtransfer.cpp /link ws2_32.lib bthprops.lib
cl /EHsc /LD /Fe:serverthread.dll serverthread.cpp /link ws2_32.lib bthprops.lib
