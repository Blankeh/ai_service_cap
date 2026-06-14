#pragma once
#include <stdint.h>
#include <IPAddress.h>

// Called when a valid {"cmd":"capture","ts":<unix>} packet arrives.
// serverTs is the Unix timestamp embedded by the Pi (0 if absent).
// serverIp is the packet's source address — the Pi — so the camera can upload
// back to it without a hardcoded server IP.
typedef void (*CaptureCallback)(uint32_t serverTs, IPAddress serverIp);

// Bind UDP socket and register callback. Returns false on failure.
bool udpListenerInit(uint16_t port, CaptureCallback cb);

// Must be called every loop() iteration to poll for incoming packets.
void udpListenerLoop();
