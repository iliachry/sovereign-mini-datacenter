// Shared VE.Direct frame corpus and flavour-independent parser tests.
//
// Both firmware flavours are driven through the same tests:
//   * test_arduino_bridge.cpp  - firmware/esp32_telemetry_bridge.ino, unmodified
//   * test_esphome_lambda.cpp  - the lambdas extracted from
//                                firmware/esphome_smdc_bridge.yaml at build time
// Each provides a small static "flavour" adapter (see the Flavour requirements
// below); every test in register_shared_tests<F>() then runs against it. The
// expectations live here, once, so the two parsers cannot drift apart without
// one of the two suites failing.
//
// Every test runs in its own forked process, i.e. against freshly "booted"
// firmware state (the sketch keeps its state in globals, the ESPHome lambda in
// function-local statics).
#pragma once

#include <sys/wait.h>
#include <unistd.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace vt {

using Bytes = std::vector<uint8_t>;

// ── Minimal test harness ─────────────────────────────────────────────────────
struct TestCase {
  std::string name;
  std::function<void()> fn;
};

inline std::vector<TestCase> &registry() {
  static std::vector<TestCase> tests;
  return tests;
}

inline void add_test(const std::string &name, std::function<void()> fn) {
  registry().push_back({name, std::move(fn)});
}

[[noreturn]] inline void fail(const char *file, int line, const std::string &what) {
  fprintf(stderr, "    %s:%d: check failed: %s\n", file, line, what.c_str());
  fflush(stderr);
  _exit(1);
}

#define CHECK(cond)                           \
  do {                                        \
    if (!(cond)) {                            \
      ::vt::fail(__FILE__, __LINE__, #cond);  \
    }                                         \
  } while (0)

#define CHECK_MSG(cond, msg)                                              \
  do {                                                                    \
    if (!(cond)) {                                                        \
      ::vt::fail(__FILE__, __LINE__, std::string(#cond) + " - " + (msg)); \
    }                                                                     \
  } while (0)

#define CHECK_EQ_STR(got, want)                                                             \
  do {                                                                                      \
    const std::string got_ = (got);                                                         \
    const std::string want_ = (want);                                                       \
    if (got_ != want_) {                                                                    \
      ::vt::fail(__FILE__, __LINE__,                                                        \
                 std::string(#got) + "\n--- got ---\n" + got_ + "\n--- want ---\n" + want_); \
    }                                                                                       \
  } while (0)

// Runs every registered test (or only those whose name contains argv[1]) in a
// child process. Prints one line per test; returns non-zero if any failed.
inline int run_all(const char *suite, int argc, char **argv) {
  const char *filter = argc > 1 ? argv[1] : nullptr;
  int ran = 0;
  int failed = 0;
  for (const TestCase &t : registry()) {
    if (filter != nullptr && t.name.find(filter) == std::string::npos) {
      continue;
    }
    ran++;
    fflush(stdout);
    fflush(stderr);
    const pid_t pid = fork();
    if (pid < 0) {
      perror("fork");
      return 2;
    }
    if (pid == 0) {
      t.fn();
      fflush(stdout);
      fflush(stderr);
      exit(0);  // normal exit so sanitizer end-of-process checks run
    }
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
      perror("waitpid");
      return 2;
    }
    const bool ok = WIFEXITED(status) && WEXITSTATUS(status) == 0;
    if (!ok) {
      failed++;
    }
    printf("%s %s :: %s\n", ok ? "ok  " : "FAIL", suite, t.name.c_str());
  }
  printf("%s: %d test(s), %d failed\n", suite, ran, failed);
  if (ran == 0) {
    printf("%s: no test matched\n", suite);
    return 2;
  }
  return failed == 0 ? 0 : 1;
}

// ── VE.Direct frame construction ─────────────────────────────────────────────
struct Field {
  std::string label;
  std::string value;
};

inline void append(Bytes &dst, const Bytes &src) { dst.insert(dst.end(), src.begin(), src.end()); }
inline void append(Bytes &dst, const std::string &src) { dst.insert(dst.end(), src.begin(), src.end()); }

inline uint8_t sum_mod256(const Bytes &b) {
  unsigned sum = 0;
  for (uint8_t x : b) {
    sum = (sum + x) & 0xFFu;
  }
  return (uint8_t)sum;
}

// A text-protocol block exactly as the device puts it on the wire:
//   "\r\n<Label>\t<Value>" per field, then "\r\nChecksum\t<byte>", where <byte>
// makes the modulo-256 sum of the whole block 0.
inline Bytes ve_frame(const std::vector<Field> &fields) {
  Bytes out;
  for (const Field &f : fields) {
    append(out, std::string("\r\n"));
    append(out, f.label);
    out.push_back('\t');
    append(out, f.value);
  }
  append(out, std::string("\r\nChecksum\t"));
  out.push_back((uint8_t)((256u - sum_mod256(out)) & 0xFFu));
  return out;
}

// BYTE-EXACT example frame (BMV-700, 112 bytes, checksum byte 0xD8) from
// Victron's public "VE.Direct Protocol FAQ", Q8 "How do I calculate the TEXT
// checksum?", transcribed from the raw data dump on that page:
//   https://www.victronenergy.com/live/vedirect_protocol:faq
// It is NOT built with ve_frame(): the checksum byte below is Victron's.
inline Bytes victron_faq_bmv700_frame() {
  static const uint8_t raw[] = {
      0x0d, 0x0a, 0x50, 0x49, 0x44, 0x09, 0x30, 0x78, 0x32, 0x30, 0x33, 0x0d, 0x0a, 0x56, 0x09, 0x32,  // ..PID.0x203..V.2
      0x36, 0x32, 0x30, 0x31, 0x0d, 0x0a, 0x49, 0x09, 0x30, 0x0d, 0x0a, 0x50, 0x09, 0x30, 0x0d, 0x0a,  // 6201..I.0..P.0..
      0x43, 0x45, 0x09, 0x30, 0x0d, 0x0a, 0x53, 0x4f, 0x43, 0x09, 0x31, 0x30, 0x30, 0x30, 0x0d, 0x0a,  // CE.0..SOC.1000..
      0x54, 0x54, 0x47, 0x09, 0x2d, 0x31, 0x0d, 0x0a, 0x41, 0x6c, 0x61, 0x72, 0x6d, 0x09, 0x4f, 0x46,  // TTG.-1..Alarm.OF
      0x46, 0x0d, 0x0a, 0x52, 0x65, 0x6c, 0x61, 0x79, 0x09, 0x4f, 0x46, 0x46, 0x0d, 0x0a, 0x41, 0x52,  // F..Relay.OFF..AR
      0x09, 0x30, 0x0d, 0x0a, 0x42, 0x4d, 0x56, 0x09, 0x37, 0x30, 0x30, 0x0d, 0x0a, 0x46, 0x57, 0x09,  // .0..BMV.700..FW.
      0x30, 0x33, 0x30, 0x37, 0x0d, 0x0a, 0x43, 0x68, 0x65, 0x63, 0x6b, 0x73, 0x75, 0x6d, 0x09, 0xd8,  // 0307..Checksum..
  };
  return Bytes(raw, raw + sizeof(raw));
}

// Synthetic frames (field sets follow the VE.Direct text-protocol field list;
// the values are made up, the checksum is computed by ve_frame()).
// Battery monitor: reports V, I, SOC - and "P"/"VS", which must not be
// mistaken for "PPV"/"V".
inline Bytes shunt_frame(long v_mV, long i_mA, long soc_permille) {
  return ve_frame({
      {"PID", "0xA389"},
      {"V", std::to_string(v_mV)},
      {"VS", "11"},
      {"I", std::to_string(i_mA)},
      {"P", "-40"},
      {"CE", "-12345"},
      {"SOC", std::to_string(soc_permille)},
      {"TTG", "1432"},
      {"Alarm", "OFF"},
      {"AR", "0"},
      {"BMV", "SmartShunt 500A/50mV"},
      {"FW", "0413"},
      {"MON", "0"},
  });
}

// Solar charger: reports V, I, VPV, PPV, H20... and never SOC. "IL" (load
// current) must not be mistaken for "I", "H2x" not for one another.
inline Bytes mppt_frame(long v_mV, long i_mA, long ppv_W, long vpv_mV, long h20_10Wh) {
  return ve_frame({
      {"PID", "0xA060"},
      {"FW", "161"},
      {"SER#", "HQ2132ABCDE"},
      {"V", std::to_string(v_mV)},
      {"I", std::to_string(i_mA)},
      {"VPV", std::to_string(vpv_mV)},
      {"PPV", std::to_string(ppv_W)},
      {"CS", "3"},
      {"MPPT", "2"},
      {"OR", "0x00000000"},
      {"ERR", "0"},
      {"LOAD", "ON"},
      {"IL", "700"},
      {"H19", "1234"},
      {"H20", std::to_string(h20_10Wh)},
      {"H21", "410"},
      {"H22", "201"},
      {"H23", "395"},
      {"HSDS", "42"},
  });
}

// A VE.Direct HEX-protocol message as it appears between text frames.
inline Bytes hex_message() {
  Bytes b;
  append(b, std::string(":A0002000148\n"));
  return b;
}

// A valid frame whose checksum byte is exactly `target` (found by varying a
// padding field). Exercises checksum bytes that look like protocol syntax.
inline Bytes frame_with_checksum_byte(uint8_t target, long v_mV) {
  static const char alphabet[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
  for (const char *a = alphabet; *a != '\0'; a++) {
    for (const char *b = alphabet; *b != '\0'; b++) {
      for (const char *c = alphabet; *c != '\0'; c++) {
        const Bytes f = ve_frame({{"V", std::to_string(v_mV)}, {"PAD", std::string{*a, *b, *c}}});
        if (f.back() == target) {
          return f;
        }
      }
    }
  }
  fail(__FILE__, __LINE__, "no padding found for the requested checksum byte");
}

// Deterministic pseudo-random bytes (LCG), reproducible on every platform.
struct Lcg {
  uint32_t state;
  explicit Lcg(uint32_t seed) : state(seed) {}
  uint32_t next() {
    state = state * 1664525u + 1013904223u;
    return state >> 8;
  }
};

// ── Flavour-independent helpers ──────────────────────────────────────────────
// A Flavour F provides (all static):
//   void boot();                               bring the firmware up
//   void rx(const uint8_t *, size_t);          bytes arrive on the UART
//   void service();                            one pass of the firmware loop
//   size_t pending();                          bytes still unread in the UART
//   unsigned long valid_frames();              checksum-valid frames so far
//   unsigned long bad_frames();                frames dropped for bad checksum
//   std::optional<double> volts(), amps(), solar_watts(), soc_percent();
//                                              what the flavour currently
//                                              reports (nullopt = not reported)
//   double tolerance();                        output resolution

// Delivers `data` in chunks; `sizes` is cycled, an empty list means "all at
// once". The firmware loop runs after every chunk, like on the device.
template <class F>
void deliver(const Bytes &data, const std::vector<size_t> &sizes = {}) {
  if (sizes.empty()) {
    F::rx(data.data(), data.size());
    F::service();
    return;
  }
  size_t pos = 0;
  size_t k = 0;
  while (pos < data.size()) {
    size_t n = sizes[k++ % sizes.size()];
    if (n == 0) {
      F::service();  // a loop pass with nothing new on the wire
      continue;
    }
    if (n > data.size() - pos) {
      n = data.size() - pos;
    }
    F::rx(data.data() + pos, n);
    F::service();
    pos += n;
  }
}

template <class F>
bool near(const std::optional<double> &got, double want) {
  return got.has_value() && std::fabs(*got - want) <= F::tolerance();
}

template <class F>
void check_nothing_reported() {
  CHECK(!F::volts().has_value());
  CHECK(!F::amps().has_value());
  CHECK(!F::solar_watts().has_value());
  CHECK(!F::soc_percent().has_value());
}

// ── Flavour-independent tests ────────────────────────────────────────────────
template <class F>
void register_shared_tests() {
  add_test("victron FAQ BMV-700 frame (byte-exact, checksum 0xD8) is accepted", [] {
    const Bytes f = victron_faq_bmv700_frame();
    CHECK(f.size() == 112);
    CHECK(f.back() == 0xD8);
    CHECK(sum_mod256(f) == 0);  // the property the FAQ states for this dump
    F::boot();
    check_nothing_reported<F>();
    deliver<F>(f);
    CHECK(F::valid_frames() == 1);
    CHECK(F::bad_frames() == 0);
    CHECK(near<F>(F::volts(), 26.201));      // V   26201 mV
    CHECK(near<F>(F::amps(), 0.0));          // I   0 mA
    CHECK(near<F>(F::soc_percent(), 100.0)); // SOC 1000 per mille
    CHECK(!F::solar_watts().has_value());    // a BMV has no PPV
  });

  add_test("battery-monitor frame: mV->V, mA->A, per mille->%", [] {
    F::boot();
    deliver<F>(shunt_frame(26412, -1530, 876));
    CHECK(F::valid_frames() == 1);
    CHECK(F::bad_frames() == 0);
    CHECK(near<F>(F::volts(), 26.412));
    CHECK(near<F>(F::amps(), -1.530));  // negative = discharging
    CHECK(near<F>(F::soc_percent(), 87.6));
    CHECK(!F::solar_watts().has_value());  // "P" is not "PPV"
  });

  add_test("MPPT frame: V, I, PPV accepted and SOC stays absent", [] {
    F::boot();
    deliver<F>(mppt_frame(27540, 12300, 345, 78120, 187));
    CHECK(F::valid_frames() == 1);
    CHECK(F::bad_frames() == 0);
    CHECK(near<F>(F::volts(), 27.540));
    CHECK(near<F>(F::amps(), 12.300));  // "IL 700" is not "I"
    CHECK(near<F>(F::solar_watts(), 345.0));
    CHECK(!F::soc_percent().has_value());
  });

  add_test("later frames replace earlier values", [] {
    F::boot();
    deliver<F>(shunt_frame(26412, -1530, 876));
    deliver<F>(shunt_frame(25990, 4200, 431));
    CHECK(F::valid_frames() == 2);
    CHECK(near<F>(F::volts(), 25.990));
    CHECK(near<F>(F::amps(), 4.200));
    CHECK(near<F>(F::soc_percent(), 43.1));
  });

  add_test("one corrupted value byte: frame rejected, nothing published, error counted", [] {
    for (int which = 0; which < 3; which++) {
      // Three fresh processes would be cleaner, but nothing is ever published
      // here, so the "nothing reported" check stays meaningful throughout.
      Bytes f = which == 0   ? shunt_frame(26412, -1530, 876)
                : which == 1 ? mppt_frame(27540, 12300, 345, 78120, 187)
                             : victron_faq_bmv700_frame();
      if (which == 0) {
        F::boot();
      }
      // Corrupt one digit of the "V" value: "\r\nV\t2...." -> flip a bit.
      const std::string needle = "\r\nV\t";
      size_t at = std::string(f.begin(), f.end()).find(needle);
      CHECK(at != std::string::npos);
      f[at + needle.size() + 1] ^= 0x01;
      const unsigned long bad_before = F::bad_frames();
      deliver<F>(f);
      CHECK(F::valid_frames() == 0);
      CHECK(F::bad_frames() == bad_before + 1);
      check_nothing_reported<F>();
    }
  });

  add_test("corrupted checksum byte: frame rejected, nothing published, error counted", [] {
    F::boot();
    Bytes f = mppt_frame(27540, 12300, 345, 78120, 187);
    f.back() ^= 0x80;
    deliver<F>(f);
    CHECK(F::valid_frames() == 0);
    CHECK(F::bad_frames() == 1);
    check_nothing_reported<F>();
  });

  add_test("every single-byte corruption of every frame is rejected, then the parser recovers", [] {
    F::boot();
    const Bytes good = shunt_frame(24001, 777, 555);  // values no corrupted frame carries
    const std::vector<Bytes> victims = {
        shunt_frame(26412, -1530, 876),
        mppt_frame(27540, 12300, 345, 78120, 187),
        victron_faq_bmv700_frame(),
    };
    unsigned long cases = 0;
    for (const Bytes &victim : victims) {
      for (size_t pos = 0; pos < victim.size(); pos++) {
        // Bit flips, a full inversion, and the two bytes with protocol
        // meaning (HEX marker, NUL).
        std::vector<uint8_t> replacements;
        for (uint8_t mask : {0x01, 0x04, 0x20, 0x80, 0xFF}) {
          replacements.push_back((uint8_t)(victim[pos] ^ mask));
        }
        replacements.push_back(':');
        replacements.push_back(0x00);
        for (uint8_t r : replacements) {
          if (r == victim[pos]) {
            continue;
          }
          Bytes f = victim;
          f[pos] = r;
          const unsigned long valid_before = F::valid_frames();
          deliver<F>(f);
          CHECK_MSG(F::valid_frames() == valid_before,
                    "corrupted frame accepted: byte " + std::to_string(pos) + " -> " + std::to_string(r));
          // Whatever state the damage left behind, at most ONE following
          // frame may be lost.
          deliver<F>(good);
          deliver<F>(good);
          const unsigned long recovered = F::valid_frames() - valid_before;
          CHECK_MSG(recovered == 1 || recovered == 2,
                    "no recovery after corrupting byte " + std::to_string(pos) + " -> " + std::to_string(r));
          CHECK(near<F>(F::volts(), 24.001));
          CHECK(near<F>(F::amps(), 0.777));
          CHECK(near<F>(F::soc_percent(), 55.5));
          CHECK(!F::solar_watts().has_value());  // never leaked from a rejected MPPT frame
          cases++;
        }
      }
    }
    CHECK(cases > 2000);
  });

  add_test("a frame split across reads parses identically (1 byte at a time, arbitrary chunking)", [] {
    F::boot();
    Bytes stream;
    append(stream, shunt_frame(26412, -1530, 876));
    append(stream, hex_message());
    append(stream, victron_faq_bmv700_frame());
    append(stream, shunt_frame(25990, 4200, 431));

    std::vector<std::vector<size_t>> chunkings = {
        {},           // everything in one read
        {1},          // one byte per loop pass
        {1, 0, 0},    // one byte, then loop passes with an empty UART
        {2},
        {3},
        {7},
        {64},
        {1, 13, 2, 40, 5},
    };
    Lcg rng(0x5eed1234u);
    for (int i = 0; i < 6; i++) {
      std::vector<size_t> sizes;
      for (int j = 0; j < 37; j++) {
        sizes.push_back(rng.next() % 12);  // 0..11, 0 = idle loop pass
      }
      sizes.push_back(1);  // guarantee progress
      chunkings.push_back(sizes);
    }

    unsigned long expected_valid = 0;
    for (const auto &sizes : chunkings) {
      deliver<F>(stream, sizes);
      expected_valid += 3;
      CHECK(F::pending() == 0);
      CHECK(F::valid_frames() == expected_valid);
      CHECK(F::bad_frames() == 0);
      CHECK(near<F>(F::volts(), 25.990));
      CHECK(near<F>(F::amps(), 4.200));
      CHECK(near<F>(F::soc_percent(), 43.1));
    }
  });

  add_test("an incomplete frame publishes nothing until its checksum arrives", [] {
    F::boot();
    const Bytes f = shunt_frame(26412, -1530, 876);
    // Everything except the final checksum byte.
    for (size_t i = 0; i + 1 < f.size(); i++) {
      F::rx(&f[i], 1);
      F::service();
      CHECK(F::pending() == 0);  // consumed straight away, not left waiting
      CHECK(F::valid_frames() == 0);
    }
    check_nothing_reported<F>();
    F::rx(&f[f.size() - 1], 1);
    F::service();
    CHECK(F::valid_frames() == 1);
    CHECK(near<F>(F::volts(), 26.412));
  });

  add_test("HEX message between frames is skipped; the next frame still parses", [] {
    F::boot();
    deliver<F>(hex_message());  // also before the very first frame
    deliver<F>(shunt_frame(26412, -1530, 876));
    deliver<F>(hex_message());
    deliver<F>(hex_message());
    deliver<F>(shunt_frame(25990, 4200, 431));
    CHECK(F::valid_frames() == 2);
    CHECK(F::bad_frames() == 0);
    CHECK(near<F>(F::volts(), 25.990));
    CHECK(near<F>(F::amps(), 4.200));
    CHECK(near<F>(F::soc_percent(), 43.1));
  });

  add_test("HEX message inside a frame discards that frame only", [] {
    F::boot();
    const Bytes a = shunt_frame(26412, -1530, 876);
    const std::string as(a.begin(), a.end());
    const size_t cut = as.find("\r\nI\t");  // after the V field
    CHECK(cut != std::string::npos);
    deliver<F>(Bytes(a.begin(), a.begin() + (long)cut));
    deliver<F>(hex_message());
    deliver<F>(Bytes(a.begin() + (long)cut, a.end()));
    CHECK(F::valid_frames() == 0);  // the interrupted frame is not half-published
    check_nothing_reported<F>();
    deliver<F>(shunt_frame(25990, 4200, 431));
    CHECK(F::valid_frames() == 1);
    CHECK(F::bad_frames() <= 1);
    CHECK(near<F>(F::volts(), 25.990));
    CHECK(near<F>(F::soc_percent(), 43.1));
  });

  add_test("checksum bytes that look like syntax (':' LF CR TAB NUL 0xFF) are still checksums", [] {
    F::boot();
    unsigned long expected = 0;
    long mv = 20000;
    for (uint8_t target : {(uint8_t)':', (uint8_t)'\n', (uint8_t)'\r', (uint8_t)'\t', (uint8_t)0x00, (uint8_t)0xFF}) {
      mv += 111;
      const Bytes f = frame_with_checksum_byte(target, mv);
      CHECK(f.back() == target);
      deliver<F>(f);
      expected++;
      CHECK_MSG(F::valid_frames() == expected, "frame with checksum byte " + std::to_string(target));
      CHECK(near<F>(F::volts(), (double)mv / 1000.0));
      // ...and it must not have swallowed the start of the following frame.
      deliver<F>(shunt_frame(26412, -1530, 876));
      expected++;
      CHECK_MSG(F::valid_frames() == expected, "frame after checksum byte " + std::to_string(target));
      CHECK(near<F>(F::volts(), 26.412));
    }
    CHECK(F::bad_frames() == 0);
  });

  add_test("'---' and other non-integer values are rejected for numeric fields", [] {
    F::boot();
    // The frame itself is intact, so it counts as valid; only the unusable
    // fields are left out.
    deliver<F>(ve_frame({{"V", "---"}, {"I", "-1530"}, {"SOC", "---"}, {"PPV", "---"}}));
    CHECK(F::valid_frames() == 1);
    CHECK(F::bad_frames() == 0);
    CHECK(!F::volts().has_value());
    CHECK(near<F>(F::amps(), -1.530));
    CHECK(!F::soc_percent().has_value());
    CHECK(!F::solar_watts().has_value());

    unsigned long expected = 1;
    for (const char *junk : {"", "-", "---", "12a", "1.5", "0x1F", "1e3", "ON", "12 34"}) {
      deliver<F>(ve_frame({{"PPV", junk}, {"I", "250"}}));
      expected++;
      CHECK(F::valid_frames() == expected);
      CHECK_MSG(!F::solar_watts().has_value(), std::string("PPV value accepted: '") + junk + "'");
      CHECK(near<F>(F::amps(), 0.250));
    }
    deliver<F>(ve_frame({{"PPV", "345"}}));
    CHECK(near<F>(F::solar_watts(), 345.0));
  });

  add_test("starting mid-frame loses that frame only", [] {
    F::boot();
    const Bytes a = shunt_frame(26412, -1530, 876);
    const std::string as(a.begin(), a.end());
    const size_t cut = as.find("\r\nI\t") + 3;  // power-up in the middle of a line
    deliver<F>(Bytes(a.begin() + (long)cut, a.end()));
    CHECK(F::valid_frames() == 0);
    CHECK(F::bad_frames() == 1);
    check_nothing_reported<F>();  // the SOC of the half-seen frame is not trusted
    deliver<F>(shunt_frame(25990, 4200, 431));
    CHECK(F::valid_frames() == 1);
    CHECK(near<F>(F::volts(), 25.990));
  });

  add_test("garbage, 0xFF, NULs and over-long lines cannot overflow; parser recovers", [] {
    F::boot();
    Bytes junk;
    Lcg rng(0xC0FFEEu);
    for (int i = 0; i < 8192; i++) {
      junk.push_back((uint8_t)(rng.next() & 0xFF));
    }
    junk.insert(junk.end(), 700, 0xFF);
    junk.insert(junk.end(), 700, 0x00);
    junk.insert(junk.end(), 3000, 'A');             // one endless line, no terminator
    append(junk, std::string("\r\n"));
    junk.insert(junk.end(), 600, 'L');              // endless label...
    junk.push_back('\t');
    junk.insert(junk.end(), 600, '9');              // ...with an endless value
    append(junk, std::string("\r\nV\t"));
    junk.insert(junk.end(), 600, '7');              // endless value for a field we do parse
    append(junk, std::string("\r\n\t\t\t\r\n\r\n\n\n\t"));
    junk.insert(junk.end(), 300, '\t');
    junk.insert(junk.end(), 300, '\n');

    // Delivered both in one burst and byte by byte.
    deliver<F>(junk);
    deliver<F>(junk, {1});
    CHECK(F::valid_frames() == 0);
    check_nothing_reported<F>();

    // At most one frame is lost resynchronising.
    deliver<F>(shunt_frame(26412, -1530, 876));
    deliver<F>(shunt_frame(25990, 4200, 431));
    CHECK(F::valid_frames() >= 1);
    CHECK(near<F>(F::volts(), 25.990));
    CHECK(near<F>(F::amps(), 4.200));
    CHECK(near<F>(F::soc_percent(), 43.1));

    // Over-long label / value INSIDE an otherwise valid frame: the frame is
    // still good and the fields we care about are unaffected.
    const unsigned long before = F::valid_frames();
    deliver<F>(ve_frame({
        {"V", "26412"},
        {std::string(300, 'L'), "1"},
        {"X", std::string(300, 'z')},
        {"I", "-1530"},
    }));
    CHECK(F::valid_frames() == before + 1);
    CHECK(near<F>(F::volts(), 26.412));
    CHECK(near<F>(F::amps(), -1.530));
  });
}

}  // namespace vt
