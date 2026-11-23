# esp-mcp

A small MCP server that lets an LLM agent flash, reset and read the serial log of a real ESP32 over USB.

I built this as a mini version of a hardware-in-the-loop test lab. Instead of a person flashing a board and watching the serial monitor, an agent calls the tools and decides from the output whether the firmware works. It only drives one board on my desk for now, but the tool interface is the part I wanted to get right.

## Results

I built 7 test firmwares: the working demo and 6 that are broken on purpose but still print the normal `BOOT OK` line (details in [Fault firmware](#fault-firmware)). On my board, a scripted check that looks for `BOOT OK` gets **3 of 7 right**. It passes 4 of the broken builds: a boot loop, a memory leak, a stalled heartbeat and the wrong region setting.

That's the gap an agent should close by actually reading the log. `run_eval.py` measures this blind: the agent never sees which firmware it's testing (see [Agent eval](#agent-eval)). The harness works on real hardware. I haven't published an agent's score yet because that run needs API access.

## Tools

| Tool | What it does |
|------|--------------|
| `list_ports()` | Lists serial ports so the agent can find the board |
| `flash(port, firmware_path, address="auto", chip="auto", baud=460800)` | Writes a `.bin` with esptool, verifies it and hard-resets the board |
| `read_logs(port, seconds=5, baud=115200, reset=False)` | Captures serial output for up to 60 s, optionally after a reset so you also get the boot log |
| `reset_board(port)` | Reboots the board by pulsing RTS (wired to EN) |

The tool descriptions also tell the agent what a normal ESP32 boot log looks like and what a crash looks like (`Guru Meditation Error`, `Backtrace:`, brownout, watchdog reset, a boot loop). I added this because the ROM boot messages look a lot like errors if you don't know them.

## Limits

- `flash` only accepts existing `.bin` files inside `firmware/` (or the folder in `ESP_FIRMWARE_DIR`). A path like `../something.bin` is refused, so the agent can't flash random files from the PC.
- Flashing times out after 180 s. Log capture is capped at 60 s and 20,000 characters, so a stuck board or very chatty firmware can't hang the agent or flood it with text.

## Hardware

Tested on an ESP32-WROOM-32 dev board (ESP32-D0WD-V3, 4 MB flash, CH340 USB-serial chip) on Windows 11.
An ESP8266 should also work with `chip="esp8266"`, but I haven't tested that yet.

## Setup

Python 3.13:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Building the firmware

`sketches/demo` prints one line at startup, `BOOT OK fw=... region=...`, and then a heartbeat every second. The fixed boot line makes pass/fail easy to check.

With [arduino-cli](https://arduino.github.io/arduino-cli/) and the esp32 core installed:

```
python build_firmware.py              # every sketch in sketches/
python build_firmware.py demo         # just one
```

For each sketch it runs `arduino-cli compile --fqbn esp32:esp32:esp32 --output-dir build\<name> sketches\<name>` and copies `<name>.ino.merged.bin` into `firmware/`. If `arduino-cli` isn't on PATH it falls back to `C:\Program Files\Arduino CLI\arduino-cli.exe`.

About flash addresses: Arduino produces two images for the ESP32. `demo.ino.bin` is only the application and goes at `0x10000`. `demo.ino.merged.bin` also contains the bootloader and partition table and goes at `0x0`. With `address="auto"` the server picks the address from the file name.

## Fault firmware

The demo always works, so on its own it can't show whether an agent's verdict means anything. The other sketches are broken on purpose. They print the same `BOOT OK` and `tick` lines as the demo, so they look healthy at first glance.

| Sketch | Fault | What the log shows | `run_test.py` (`"BOOT OK"`) |
|--------|-------|--------------------|-----------------------------|
| `demo` | none | clean boot, a tick every second, flat `free_heap` | PASS (right) |
| `crash_setup` | writes to a NULL pointer in `setup()` | `Guru Meditation Error ... (StoreProhibited)`, backtrace, reboot, repeat | FAIL (right) |
| `boot_loop` | calls `esp_restart()` after 3 ticks | `BOOT OK` and ticks 0-2, then the boot log again with `rst:0xc (SW_CPU_RESET)` | PASS (wrong) |
| `heap_leak` | leaks 2 KB per tick | `free_heap` drops about 2.2 KB on every tick | PASS (wrong) |
| `stall` | waits forever after 3 ticks | ticks 0-2, then nothing | PASS (wrong) |
| `wrong_region` | built for US915 | clean boot with `region=US915` | PASS (wrong if EU868 is expected) |
| `wrong_baud` | `Serial.begin(9600)` | ROM boot log, then garbage at 115200 | FAIL (right) |

These are the results from my board, using `run_test.py` with a 6 s capture. The substring check gets 3 of the 7 right. To beat it, an agent has to notice the tick counter going back to 0, the heap trend, the missing heartbeats and the region value.

When measuring an agent, don't let it see the sketch names or sources, because they give the answer away. `run_eval.py` takes care of that (see [Agent eval](#agent-eval)).

## Scripted test

`run_test.py` goes through the MCP server and uses the same tools an agent would: flash, reset and read the log, then check for an expected string.

```
python run_test.py COM3 demo.ino.merged.bin "BOOT OK"
```

Output from my board:

```
=== flash ===
exit code 0 (wrote demo.ino.merged.bin at 0x0)
Wrote 4194304 bytes (190730 compressed) at 0x00000000 in 13.4 seconds (2501.2 kbit/s).
Hash of data verified.
=== serial log ===
ets Jul 29 2019 12:21:46
rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
...
BOOT OK fw=1.0.0 region=EU868 chip=ESP32-D0WD-V3 rev=301
tick 0 uptime_ms=211 free_heap=337412
tick 1 uptime_ms=1210 free_heap=337412
...
PASS: 'BOOT OK' found in serial output (24.2s)
```

This is the baseline in the agent eval below: `run_eval.py --agent baseline` runs the same check.

`test_client.py` is a quicker check: it starts the server, lists the tools and makes sure a path outside `firmware/` is refused.

## Agent eval

`run_eval.py` checks whether an agent can tell the good firmware from the broken builds without knowing which one it's looking at.

For each trial it starts a fresh copy of the MCP server whose firmware folder holds a single image, always named `candidate.merged.bin`. It gives the agent the same request every time:

> Flash candidate.merged.bin to the ESP32 on COM3, reset it, read 10 seconds of serial output and tell me if it booted correctly.
>
> The firmware should print `BOOT OK fw=1.0.0 region=EU868 ...` once at startup, then a `tick` line every second, and keep running.

The agent only gets the four MCP tools plus `report_verdict(verdict, reason)`. It can't read files, so the sketch names and comments can't give the answer away. Trials run one at a time in random order. The seed is printed, so a run can be repeated.

```
python run_eval.py --agent baseline     # the run_test.py check, no API calls
python run_eval.py --agent claude       # Claude through the Claude API
python run_eval.py --agent claude --reps 3 --cases boot_loop,heap_leak,stall
```

`--agent claude` needs an Anthropic API key in `ANTHROPIC_API_KEY`. API usage is billed separately from a Claude subscription. `--model` and `--effort` choose the model and effort level (defaults: `claude-opus-5-5`, `high`).

Each run writes `eval_runs/<timestamp>_<agent>/`:

- `results.jsonl`: one row per trial with the verdict, the expected verdict, the firmware SHA-256, tool and API call counts, token usage and time
- `traces/<case>_rep<k>.json`: the prompt, a summary of the model's reasoning, and every tool call with its arguments and full result, so you can check why the agent decided what it did
- `summary.txt`: the table printed at the end

A trial only counts as answered if the agent flashed the board and called `report_verdict`. If it didn't (no verdict, never flashed, ran out of turns), that's a wrong answer. Problems that aren't the agent's fault (API errors, a failed flash, a timeout) are left out of the score and listed separately.

Baseline result from my board:

```
case          expected  verdicts  correct
demo          PASS      PASS      1/1
crash_setup   FAIL      FAIL      1/1
boot_loop     FAIL      PASS      0/1
heap_leak     FAIL      PASS      0/1
stall         FAIL      PASS      0/1
wrong_region  FAIL      PASS      0/1
wrong_baud    FAIL      FAIL      1/1

Accuracy: 3/7 = 43% (95% CI 16%-75%)
Broken firmware caught: 2/6
Good firmware passed:   1/1
```

With 7 cases a single run is noisy, as the confidence interval shows. Use `--reps 3` or more before comparing two agents or models.

## Connecting an LLM

Any MCP client that can start a stdio server works. Most of them use a config like this:

```json
{
  "mcpServers": {
    "esp": {
      "command": "C:\\path\\to\\esp-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\esp-mcp\\esp_mcp.py"]
    }
  }
}
```

Then ask something like: "Flash demo.ino.merged.bin to the ESP32 on COM3, reset it, read 10 seconds of serial output and tell me if it booted correctly."

## Things I ran into

- esptool 5 renamed its commands, so it's `write-flash` now instead of `write_flash`.
- The reset uses the normal auto-reset circuit on dev boards: RTS pulls EN low while DTR (GPIO0) stays released, so the board boots the application instead of the bootloader.
- A merged image is the full 4 MB because of padding, but it compresses to about 190 KB, so flashing it still only takes around 13 s.
- On the ESP8266 the ROM prints its boot message at 74880 baud, so at 115200 you get some garbage right after a reset. That's normal. The ESP32 ROM prints at 115200, so its boot log is readable.
- CH340 boards can fail at high flash speeds. 460800 baud works on mine; 115200 is the fallback.

## Next steps

- A lock per board, so two agent calls can't use the same port at the same time
- A `recover` tool: reset, and if the board keeps crashing, reflash a known-good image
- Log every tool call in the server itself, the way `run_eval.py` does for eval runs, so normal agent sessions leave an evidence trail too
- A build tool, so the agent can build firmware with different settings (for example `REGION`) and check that the board reports the new value
- A debug probe backend (SWD through OpenOCD, pyOCD or probe-rs) next to esptool, for boards without a serial bootloader

## Layout

```
esp_mcp.py         the MCP server
run_test.py        scripted flash + check (baseline)
run_eval.py        blind agent eval (see Agent eval)
test_client.py     quick check of the server
build_firmware.py  builds sketches/ into firmware/
sketches/demo/     demo firmware
sketches/<fault>/  deliberately broken firmware (see Fault firmware)
firmware/          .bin files the server may flash (not in git)
eval_runs/         eval results (not in git)
requirements.txt
```
