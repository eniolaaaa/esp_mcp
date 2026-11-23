// Fault fixture: crashes in setup() before the boot line is printed.
//
// Log: "Guru Meditation Error: Core 1 panic'ed (StoreProhibited)", a register
// dump and "Backtrace:", then "Rebooting..." and the ROM boot log again, over
// and over (crash loop). "BOOT OK" never appears.
// Correct verdict: FAIL. run_test.py "BOOT OK" check: FAIL (catches it).

#include <stdint.h>

// volatile so the compiler can't prove it's NULL and drop or rewrite the store
volatile uintptr_t config_addr = 0;

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  *(volatile int *)config_addr = 42;  // write to address 0 -> panic
  Serial.println("BOOT OK");          // never reached
}

void loop() {}
