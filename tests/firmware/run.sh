#!/usr/bin/env bash
# Host-side firmware tests: compiles the UNMODIFIED Arduino sketch and the
# lambdas extracted from the ESPHome YAML against stub headers and runs the
# VE.Direct parser tests. Needs a C++17 compiler and python3 - no Arduino,
# PlatformIO or ESPHome toolchain.
#
#   tests/firmware/run.sh              # build + run both suites
#   tests/firmware/run.sh checksum     # only tests whose name contains "checksum"
#
# Environment:
#   CXX                  compiler (default: c++)
#   SMDC_FIRMWARE_INO    sketch under test   (default: firmware/esp32_telemetry_bridge.ino)
#   SMDC_ESPHOME_YAML    ESPHome YAML under test (default: firmware/esphome_smdc_bridge.yaml)
#   SMDC_BUILD_DIR       where binaries go   (default: tests/firmware/build, git-ignored)
#   SMDC_NO_SANITIZE=1   build without AddressSanitizer / UBSan
#   SMDC_REQUIRE_SANITIZE=1  fail if the sanitizers are unavailable (used in CI)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

cxx="${CXX:-c++}"
ino="${SMDC_FIRMWARE_INO:-$repo/firmware/esp32_telemetry_bridge.ino}"
yaml="${SMDC_ESPHOME_YAML:-$repo/firmware/esphome_smdc_bridge.yaml}"
build="${SMDC_BUILD_DIR:-$here/build}"
filter="${1:-}"

[ -s "$ino" ] || { echo "error: sketch not found: $ino" >&2; exit 2; }
[ -s "$yaml" ] || { echo "error: ESPHome YAML not found: $yaml" >&2; exit 2; }
command -v "$cxx" >/dev/null || { echo "error: C++ compiler '$cxx' not found (set CXX)" >&2; exit 2; }
command -v python3 >/dev/null || { echo "error: python3 not found" >&2; exit 2; }

mkdir -p "$build"
ino="$(cd "$(dirname "$ino")" && pwd)/$(basename "$ino")"

flags=(-std=c++17 -O1 -g -Wall -Wextra)

# macOS only: when the Command Line Tools ship a newer SDK than the active
# linker understands, a plain `c++` cannot link anything. Fall back to the SDK
# of the selected Xcode instead of failing with an unrelated-looking ld error.
echo 'int main() { return 0; }' >"$build/probe.cpp"
if [ "$(uname -s)" = "Darwin" ] && [ -z "${SDKROOT:-}" ] &&
  ! "$cxx" "${flags[@]}" "$build/probe.cpp" -o "$build/probe" 2>/dev/null; then
  SDKROOT="$(xcrun --sdk macosx --show-sdk-path)"
  export SDKROOT
  echo "== default macOS SDK does not link; using SDKROOT=$SDKROOT"
fi

# Runs "$@" and fails if it is still running after 10 s (a sanitizer runtime
# that does not match the OS can hang at start-up instead of failing).
runs_promptly() {
  "$@" >/dev/null 2>&1 &
  local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge 50 ]; then
      kill -9 "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 1
    fi
    sleep 0.2
    waited=$((waited + 1))
  done
  wait "$pid"
}

# Use ASan + UBSan when this compiler can build AND run a sanitized binary
# (set SMDC_REQUIRE_SANITIZE=1 to fail instead of falling back, as CI does).
sanitize=("-fsanitize=address,undefined" -fno-sanitize-recover=all -fno-omit-frame-pointer)
if [ "${SMDC_NO_SANITIZE:-0}" != "1" ] &&
  "$cxx" "${flags[@]}" "${sanitize[@]}" "$build/probe.cpp" -o "$build/probe" 2>/dev/null &&
  runs_promptly "$build/probe"; then
  flags+=("${sanitize[@]}")
  echo "== $("$cxx" --version | head -n 1) with AddressSanitizer + UBSan"
else
  if [ "${SMDC_REQUIRE_SANITIZE:-0}" = "1" ]; then
    echo "error: SMDC_REQUIRE_SANITIZE=1 but '$cxx' cannot build and run an ASan/UBSan binary" >&2
    exit 2
  fi
  echo "== $("$cxx" --version | head -n 1) WITHOUT sanitizers"
fi
rm -f "$build/probe" "$build/probe.cpp"

echo "== Arduino flavour: $ino"
# The test .cpp #includes the sketch by path (SMDC_FIRMWARE_INO), after the stubs.
"$cxx" "${flags[@]}" -I "$here/stubs/arduino" -I "$here" \
  "-DSMDC_FIRMWARE_INO=\"$ino\"" \
  "$here/test_arduino_bridge.cpp" -o "$build/test_arduino_bridge"

echo "== ESPHome flavour: $yaml"
python3 "$here/extract_esphome_lambdas.py" "$yaml" "$build/esphome_lambdas.generated.h"
"$cxx" "${flags[@]}" -I "$here/stubs/esphome" -I "$here" -I "$build" \
  "$here/test_esphome_lambda.cpp" -o "$build/test_esphome_lambda"

status=0
"$build/test_arduino_bridge" ${filter:+"$filter"} || status=1
"$build/test_esphome_lambda" ${filter:+"$filter"} || status=1

if [ "$status" -eq 0 ]; then
  echo "== firmware host tests: PASS"
else
  echo "== firmware host tests: FAIL" >&2
fi
exit "$status"
