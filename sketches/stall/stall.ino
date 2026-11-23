// Fault fixture: boots normally, then stops doing anything after 3 ticks.
//
// Log: the normal boot line and ticks 0-2, then silence. No crash, no reset.
// Correct verdict: FAIL. run_test.py "BOOT OK" check: PASS (misses it).

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
    while (true) {  // the "bug": waits forever for something that never happens
      delay(100);
    }
  }
  digitalWrite(LED_PIN, n % 2);
  Serial.printf("tick %lu uptime_ms=%lu free_heap=%lu\n",
                n, millis(), (unsigned long)ESP.getFreeHeap());
  n++;
  delay(1000);
}
