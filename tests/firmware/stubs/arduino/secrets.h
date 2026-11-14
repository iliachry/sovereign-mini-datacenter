// Test-only stand-in for firmware/secrets.h (see firmware/secrets.h.example).
// These are not credentials. A real, git-ignored firmware/secrets.h, if one
// exists next to the sketch, takes precedence over this file.
#pragma once

#define WIFI_SSID "host-test-ssid"
#define WIFI_PASSWORD "host-test-not-a-password"
