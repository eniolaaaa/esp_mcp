// Fault fixture: boots normally but leaks 2 KB of heap every tick.
//
// Log: identical to the demo except free_heap drops by ~2 KB on every tick
// instead of staying flat. Left running for a few minutes it runs out of heap.
// Correct verdict: FAIL. run_test.py "BOOT OK" check: PASS (misses it).

#include <string.h>

#ifndef REGION
#define REGION "EU868"
#endif

#ifndef FW_VERSION
#define FW_VERSION "1.0.0"
#endif

const int LED_PIN = 2;
const size_t LEAK_BYTES = 2048;

// volatile so the compiler can't optimise the malloc away
static void *volatile last_buffer;

void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(LED_PIN, OUTPUT);
  Serial.println();
  Serial.printf("BOOT OK fw=%s region=%s chip=%s rev=%d\n",
                FW_VERSION, REGION, ESP.getChipModel(), ESP.getChipRevision());
}

void loop() {
  static unsigned long n = 0;
  digitalWrite(LED_PIN, n % 2);
  Serial.printf("tick %lu uptime_ms=%lu free_heap=%lu\n",
                n, millis(), (unsigned long)ESP.getFreeHeap());
  void *buf = malloc(LEAK_BYTES);  // the "bug": never freed
  if (buf) {
    memset(buf, 0xA5, LEAK_BYTES);
  }
  last_buffer = buf;
  n++;
  delay(1000);
}
