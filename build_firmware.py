"""Build sketches with arduino-cli and copy each merged image into firmware/.

  python build_firmware.py              # every sketch in sketches/
  python build_firmware.py demo stall   # only these
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
FQBN = "esp32:esp32:esp32"


def arduino_cli() -> str:
    found = shutil.which("arduino-cli")
    if found:
        return found
    default = Path(r"C:\Program Files\Arduino CLI\arduino-cli.exe")
    if default.is_file():
        return str(default)
    sys.exit("arduino-cli not found: install it or add it to PATH")


def main(names: list[str]) -> None:
    cli = arduino_cli()
    names = names or sorted(p.name for p in (ROOT / "sketches").iterdir() if p.is_dir())
    (ROOT / "firmware").mkdir(exist_ok=True)
    for name in names:
        out = ROOT / "build" / name
        print(f"=== {name} ===", flush=True)
        r = subprocess.run([cli, "compile", "--fqbn", FQBN, "--output-dir", str(out),
                            str(ROOT / "sketches" / name)])
        if r.returncode != 0:
            sys.exit(f"build failed: {name}")
        shutil.copy2(out / f"{name}.ino.merged.bin", ROOT / "firmware")
        print(f"-> firmware/{name}.ino.merged.bin", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
