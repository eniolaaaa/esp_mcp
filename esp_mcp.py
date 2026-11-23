"""Minimal MCP server: let an LLM list, flash, reset and read logs from an ESP32
(or ESP8266) dev board over USB.

Tools:
  list_ports()                         -> serial ports found
  flash(port, firmware_path, address)  -> write a .bin with esptool
  read_logs(port, seconds, reset)      -> capture serial output
  reset_board(port)                    -> pulse RTS (wired to EN/RST) to reboot

Safety: flashing only accepts .bin files inside FIRMWARE_DIR
(env ESP_FIRMWARE_DIR, default: firmware/ next to this file).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import serial
import serial.tools.list_ports
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("esp")

FIRMWARE_DIR = Path(os.environ.get(
    "ESP_FIRMWARE_DIR", Path(__file__).parent / "firmware")).resolve()
MAX_LOG_CHARS = 20_000


def _reset(ser: serial.Serial) -> None:
    """Hardware reset via RTS (-> EN/RST), keeping DTR (-> GPIO0) released so it
    boots the sketch instead of entering the bootloader."""
    ser.dtr = False
    ser.rts = True
    time.sleep(0.1)
    ser.rts = False


def _default_address(fw: Path, chip: str) -> str:
    """Where an Arduino .bin belongs: merged images and ESP8266 sketches start at
    0x0; a plain ESP32 app image goes after the bootloader/partitions at 0x10000."""
    if chip == "esp8266" or fw.name.endswith(".merged.bin"):
        return "0x0"
    return "0x10000"


@mcp.tool()
def list_ports() -> str:
    """List available serial ports. ESP boards usually show up as a CH340,
    CP210x or 'USB JTAG/serial' device."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        return "No serial ports found."
    return "\n".join(f"{p.device}: {p.description} [{p.hwid}]" for p in ports)


@mcp.tool()
def flash(port: str, firmware_path: str, address: str = "auto",
          chip: str = "auto", baud: int = 460800) -> str:
    """Flash a .bin file to the board, then hard-reset it so the new sketch runs.

    firmware_path: a .bin inside the firmware directory.
    address: leave as "auto" -> 0x0 for '*.merged.bin' (ESP32) or when
      chip="esp8266", otherwise 0x10000 (plain ESP32 app image, sketch.ino.bin).
    chip: "auto" detects the chip; pass "esp8266" for ESP8266 boards.
    If flashing fails partway, retry with baud=115200.
    """
    fw = (FIRMWARE_DIR / firmware_path).resolve()
    if FIRMWARE_DIR not in fw.parents or fw.suffix != ".bin" or not fw.is_file():
        return f"Refused: {firmware_path!r} must be an existing .bin inside {FIRMWARE_DIR}"
    if address == "auto":
        address = _default_address(fw, chip)
    cmd = [sys.executable, "-m", "esptool", "--chip", chip, "--port", port,
           "--baud", str(baud), "write-flash", address, str(fw)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return "Flash timed out after 180s."
    out = (r.stdout + r.stderr)[-MAX_LOG_CHARS:]
    return f"exit code {r.returncode} (wrote {fw.name} at {address})\n{out}"


@mcp.tool()
def read_logs(port: str, seconds: float = 5.0, baud: int = 115200,
              reset: bool = False) -> str:
    """Capture serial output for N seconds (max 60). Set reset=True to reboot
    first so you see the boot log and the output from setup().

    baud must match the sketch's Serial.begin(). The ESP32 boot log ('ets ...',
    'rst:0x1 (POWERON_RESET)', 'entry 0x...') is normal. Real problems look
    like 'Guru Meditation Error', 'Backtrace:', 'Brownout detector was
    triggered', a watchdog reset, or a boot loop (the boot log repeating).
    ESP8266 only: its ROM prints at 74880 baud, so a short burst of garbage
    right after reset is normal there; crashes look like 'Exception (N)' or
    'Soft WDT reset'.
    """
    seconds = max(0.5, min(seconds, 60.0))
    try:
        with serial.Serial(port, baud, timeout=0.2) as ser:
            if reset:
                ser.reset_input_buffer()
                _reset(ser)
            deadline = time.monotonic() + seconds
            buf = bytearray()
            while time.monotonic() < deadline:
                buf += ser.read(4096)
    except serial.SerialException as e:
        return f"Serial error: {e}"
    text = buf.decode("utf-8", errors="replace")
    if len(text) > MAX_LOG_CHARS:
        text = "...[truncated]...\n" + text[-MAX_LOG_CHARS:]
    return text or "(no output)"


@mcp.tool()
def reset_board(port: str) -> str:
    """Reboot the board by pulsing RTS (wired to EN on ESP32, RST on ESP8266)."""
    try:
        with serial.Serial(port, 115200) as ser:
            _reset(ser)
    except serial.SerialException as e:
        return f"Serial error: {e}"
    return "Reset sent."


if __name__ == "__main__":
    FIRMWARE_DIR.mkdir(exist_ok=True)
    mcp.run()
