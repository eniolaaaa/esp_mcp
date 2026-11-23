// Fault fixture: boots normally, then silently restarts itself every ~3 s.
//
// Log: the normal boot line and ticks 0-2, then the ROM boot log again with
// "rst:0xc (SW_CPU_RESET)" and the tick counter back at 0, repeating.
// Correct verdict: FAIL. run_test.py "BOOT OK" check: PASS (misses it, because
// "BOOT OK" is printed on every boot).

#ifndef REGION
#define REGION "EU868"
#endif

#ifndef FW_VERSION
#define FW_VERSION "1.0.0"
#endif

const int LED_PIN = 2;

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
  if (n == 3) {
    esp_restart();  // the "bug": no message, the board just reboots
  }
  digitalWrite(LED_PIN, n % 2);
  Serial.printf("tick %lu uptime_ms=%lu free_heap=%lu\n",
                n, millis(), (unsigned long)ESP.getFreeHeap());
  n++;
  delay(1000);
}
