#pragma once
#include <stdint.h>

// Called when a valid {"cmd":"capture","ts":<unix>} packet arrives.
// serverTs is the Unix timestamp embedded by the Pi (0 if absent).
typedef void (*CaptureCallback)(uint32_t serverTs);

// Bind UDP socket and register callback. Returns false on failure.
bool udpListenerInit(uint16_t port, CaptureCallback cb);

// Must be called every loop() iteration to poll for incoming packets.
void udpListenerLoop();
