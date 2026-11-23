"""Scripted test run through the MCP server: flash -> reset + read logs -> verdict.

Uses the same tools an LLM agent would call, so it doubles as the scripted
baseline to compare agents against.

  python run_test.py COM3 demo.ino.merged.bin "BOOT OK"
"""
import asyncio
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).parent / "esp_mcp.py"


async def main(port: str, firmware: str, expect: str, seconds: float = 6.0) -> int:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t0 = time.monotonic()

            out = (await s.call_tool("flash", {"port": port, "firmware_path": firmware})).content[0].text
            print("=== flash ===\n" + "\n".join(out.splitlines()[:1] + out.splitlines()[-6:]))
            if not out.startswith("exit code 0"):
                print(f"\nFAIL: flashing failed ({time.monotonic() - t0:.1f}s)")
                return 1

            log = (await s.call_tool("read_logs", {"port": port, "seconds": seconds, "reset": True})).content[0].text
            print("=== serial log ===\n" + log)

            ok = expect in log
            print(f"\n{'PASS' if ok else 'FAIL'}: {expect!r} {'found' if ok else 'not found'} "
                  f"in serial output ({time.monotonic() - t0:.1f}s)")
            return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3])))
