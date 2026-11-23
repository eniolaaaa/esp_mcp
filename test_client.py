import asyncio, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    p = StdioServerParameters(command=sys.executable, args=["esp_mcp.py"])
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print([t.name for t in (await s.list_tools()).tools])
            print((await s.call_tool("list_ports", {})).content[0].text)
            print((await s.call_tool("flash", {"port": "COM3", "firmware_path": "../evil.bin"})).content[0].text)
asyncio.run(main())
