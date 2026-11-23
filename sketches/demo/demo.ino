// Demo firmware for the MCP test: prints a fixed boot line the agent can check
// ("BOOT OK ..."), then a heartbeat once per second.
//
// REGION and FW_VERSION can be overridden at build time, so the agent can later
// build and flash a different "region" and verify the board reports it.

#ifndef REGION
#define REGION "EU868"
#endif

#ifndef FW_VERSION
#define FW_VERSION "1.0.0"
#endif

const int LED_PIN = 2;  // onboard blue LED on most ESP32-WROOM-32 dev boards

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
