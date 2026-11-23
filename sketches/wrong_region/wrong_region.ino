// Fault fixture: a healthy build for the wrong region (US915 instead of EU868).
// Otherwise identical to sketches/demo.
//
// Log: a clean boot with "region=US915".
// Correct verdict: FAIL when the expected region is EU868 (the prompt has to say
// so). run_test.py "BOOT OK" check: PASS (misses it).

#define REGION "US915"

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
  digitalWrite(LED_PIN, n % 2);
  Serial.printf("tick %lu uptime_ms=%lu free_heap=%lu\n",
                n, millis(), (unsigned long)ESP.getFreeHeap());
  n++;
  delay(1000);
}
