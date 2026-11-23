// Fault fixture: the sketch talks at 9600 baud instead of 115200.
// Otherwise identical to sketches/demo.
//
// Log at 115200: the ROM boot log is readable (the ROM always uses 115200),
// then only garbage characters. Reading at baud=9600 shows the normal output.
// Correct verdict: FAIL at 115200 (or "wrong baud rate" if the agent figures
// it out). run_test.py "BOOT OK" check: FAIL (catches it).

#ifndef REGION
#define REGION "EU868"
#endif

#ifndef FW_VERSION
#define FW_VERSION "1.0.0"
#endif

const int LED_PIN = 2;

void setup() {
  Serial.begin(9600);  // the "bug"
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
