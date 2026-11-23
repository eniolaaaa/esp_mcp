"""Blind eval: can an agent tell good firmware from broken firmware on a real board?

Each trial starts a fresh esp_mcp.py whose firmware folder holds one image from
firmware/, renamed to candidate.merged.bin, and asks the agent whether it booted
correctly. The agent only has the MCP tools plus report_verdict, so it can't tell
which case it is looking at. Trials run one at a time (one board), in random order.

  python run_eval.py --agent baseline                 # run_test.py's "BOOT OK" check, no API calls
  python run_eval.py --agent llm                      # an LLM agent through the API
  python run_eval.py --agent llm --reps 3 --cases boot_loop,stall

Each run writes eval_runs/<timestamp>_<agent>/:
  results.jsonl               one row per trial: verdict, expected, correct, status, usage, ...
  traces/<case>_rep<k>.json   the prompt and every tool call, with arguments and results
  summary.txt                 the table printed at the end
"""
import argparse
import asyncio
import hashlib
import json
import math
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).parent
SERVER = ROOT / "esp_mcp.py"
FIRMWARE = ROOT / "firmware"
RUNS = ROOT / "eval_runs"
# Same name for every case, so the name gives nothing away. Keep ".merged.bin":
# the server flashes files with that suffix at 0x0.
CANDIDATE = "candidate.merged.bin"

# Answer key: case -> (expected verdict, what is wrong). The agent never sees this.
CASES = {
    "demo": ("PASS", "healthy"),
    "crash_setup": ("FAIL", "panics in setup(), crash loop"),
    "boot_loop": ("FAIL", "esp_restart() every ~3 s"),
    "heap_leak": ("FAIL", "leaks ~2 KB of heap per tick"),
    "stall": ("FAIL", "heartbeat stops after 3 ticks"),
    "wrong_region": ("FAIL", "reports region=US915, spec says EU868"),
    "wrong_baud": ("FAIL", "prints at 9600 baud, not 115200"),
}

SYSTEM = "You test firmware on a physical ESP32 dev board using the tools provided."

PROMPT = """Flash {firmware} to the ESP32 on {port}, reset it, read 10 seconds of serial output and tell me if it booted correctly.

The firmware should print `BOOT OK fw=1.0.0 region=EU868 ...` once at startup, then a `tick` line every second, and keep running.

When you have decided, call report_verdict."""

VERDICT_TOOL = {
    "name": "report_verdict",
    "description": "Report your final verdict on the firmware. Call it once, after checking the board.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["PASS", "FAIL"],
                        "description": "PASS if the firmware booted and runs as specified, otherwise FAIL."},
            "reason": {"type": "string",
                       "description": "One or two sentences, citing the serial output."},
        },
        "required": ["verdict", "reason"],
        "additionalProperties": False,
    },
}

# "ok" means the agent flashed the board and gave a verdict. Agent failures count
# as wrong answers; harness failures (API, hardware, timeouts) are left out of the score.
AGENT_FAILURES = {"no_verdict", "not_flashed", "max_turns", "truncated", "refusal"}
HARNESS_FAILURES = {"flash_failed", "api_error", "harness_error", "timeout", "model_mismatch"}

# $ per million tokens (input, output), only for the estimate in the summary.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens")


class Trial:
    def __init__(self, case: str, rep: int, prompt: str):
        self.case, self.rep, self.prompt = case, rep, prompt
        self.expected = CASES[case][0]
        self.verdict = self.reason = self.error = None
        self.status = "ok"
        self.flash_attempted = self.flashed = False
        self.tool_calls = self.api_calls = 0
        self.usage = dict.fromkeys(USAGE_KEYS, 0)
        self.models: set[str] = set()
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.t0 = time.monotonic()
        self.trace: list[dict] = []

    def log(self, role: str, content: str, **extra) -> None:
        self.trace.append({"role": role, "content": content,
                           "t": round(time.monotonic() - self.t0, 1), **extra})

    def finish(self) -> None:
        if self.status == "ok" and self.verdict is None:
            self.status = "no_verdict"
        if self.status == "ok" and not self.flashed:
            self.status = "flash_failed" if self.flash_attempted else "not_flashed"
        self.correct = self.status == "ok" and self.verdict == self.expected
        self.duration = round(time.monotonic() - self.t0, 1)


async def call_tool(session: ClientSession, trial: Trial, name: str, args: dict) -> tuple[str, bool]:
    trial.log("tool_call", json.dumps(args, indent=2), name=name)
    start = time.monotonic()
    res = await session.call_tool(name, args)
    text = "\n".join(c.text for c in res.content if c.type == "text")
    trial.log("tool_result", text, name=name, is_error=bool(res.is_error),
              duration_s=round(time.monotonic() - start, 1))
    trial.tool_calls += 1
    if name == "flash":
        trial.flash_attempted = True
        # The folder holds only the candidate, so any successful flash flashed it.
        trial.flashed = trial.flashed or text.startswith("exit code 0")
    return text, bool(res.is_error)


async def baseline_agent(session: ClientSession, trial: Trial, port: str) -> None:
    """What run_test.py does: flash, reset, read 6 s, PASS if "BOOT OK" appears."""
    await call_tool(session, trial, "flash", {"port": port, "firmware_path": CANDIDATE})
    if not trial.flashed:
        trial.verdict, trial.reason = "FAIL", "flashing failed"
        return
    log, _ = await call_tool(session, trial, "read_logs",
                             {"port": port, "seconds": 6, "reset": True})
    found = "BOOT OK" in log
    trial.verdict = "PASS" if found else "FAIL"
    trial.reason = "'BOOT OK' found" if found else "'BOOT OK' not found"


def llm_agent(client: anthropic.AsyncAnthropic, model: str, effort: str, max_turns: int):
    async def run(session: ClientSession, trial: Trial, port: str) -> None:
        listed = (await session.list_tools()).tools
        tools = [{"name": t.name, "description": t.description or "",
                  "input_schema": t.input_schema} for t in listed] + [VERDICT_TOOL]
        trial.log("system", SYSTEM)
        trial.log("user", trial.prompt)
        messages = [{"role": "user", "content": trial.prompt}]
        for _ in range(max_turns):
            resp = await client.messages.create(
                model=model, max_tokens=16000, system=SYSTEM, tools=tools, messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": effort},
                cache_control={"type": "ephemeral"},
            )
            trial.api_calls += 1
            trial.models.add(resp.model)
            for k in USAGE_KEYS:
                trial.usage[k] += getattr(resp.usage, k, None) or 0
            if not resp.model.startswith(model):
                trial.status, trial.error = "model_mismatch", f"asked for {model}, got {resp.model}"
                return

            thinking = "\n\n".join(b.thinking for b in resp.content
                                   if b.type == "thinking" and b.thinking)
            text = "\n\n".join(b.text for b in resp.content if b.type == "text")
            if text or thinking:
                trial.log("assistant", text, **({"thinking": thinking} if thinking else {}))
            if resp.stop_reason == "refusal":
                trial.status = "refusal"
                return
            if resp.stop_reason == "max_tokens":
                trial.status = "truncated"
                return

            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                return  # ended without report_verdict -> no_verdict
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for use in uses:
                if use.name == "report_verdict":
                    trial.log("tool_call", json.dumps(use.input, indent=2), name=use.name)
                    trial.verdict, trial.reason = use.input["verdict"], use.input["reason"]
                    return
                out, is_error = await call_tool(session, trial, use.name, use.input)
                results.append({"type": "tool_result", "tool_use_id": use.id,
                                "content": out or "(no output)", "is_error": is_error})
            messages.append({"role": "user", "content": results})
        trial.status = "max_turns"

    return run


async def run_trial(trial: Trial, firmware: Path, port: str, agent) -> None:
    """Fresh server per trial, with a firmware folder that holds only this case."""
    with tempfile.TemporaryDirectory(prefix="esp_eval_", ignore_cleanup_errors=True) as d:
        shutil.copy2(firmware, Path(d) / CANDIDATE)
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)],
                                       env={"ESP_FIRMWARE_DIR": d})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                try:
                    await agent(session, trial, port)
                except Exception as e:
                    trial.status = ("api_error" if isinstance(e, anthropic.APIError)
                                    else "harness_error")
                    trial.error = repr(e)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def summarize(rows: list[dict], header: str, model: str | None, wall_s: float) -> str:
    lines = [header, ""]
    per_case = {}
    for case in CASES:
        rs = sorted((r for r in rows if r["case"] == case), key=lambda r: r["rep"])
        if rs:
            verdicts = " ".join(r["verdict"] if r["status"] == "ok" else r["status"] for r in rs)
            scored = [r for r in rs if r["status"] not in HARNESS_FAILURES]
            per_case[case] = (verdicts, sum(r["correct"] for r in scored), len(scored))
    width = max([len(v) for v, _, _ in per_case.values()] + [8]) + 2
    lines.append(f"{'case':<14}{'expected':<10}{'verdicts':<{width}}correct")
    for case, (verdicts, k, n) in per_case.items():
        lines.append(f"{case:<14}{CASES[case][0]:<10}{verdicts:<{width}}{k}/{n}")

    scored = [r for r in rows if r["status"] not in HARNESS_FAILURES]
    k, n = sum(r["correct"] for r in scored), len(scored)
    lines.append("")
    if n:
        lo, hi = wilson(k, n)
        lines.append(f"Accuracy: {k}/{n} = {k / n:.0%} (95% CI {lo:.0%}-{hi:.0%})")
        broken = [r for r in scored if r["expected"] == "FAIL"]
        good = [r for r in scored if r["expected"] == "PASS"]
        lines.append(f"Broken firmware caught: {sum(r['correct'] for r in broken)}/{len(broken)}")
        lines.append(f"Good firmware passed:   {sum(r['correct'] for r in good)}/{len(good)}")
    agent_fail = [r for r in scored if r["status"] in AGENT_FAILURES]
    if agent_fail:
        lines.append("Agent failures (counted wrong): "
                     + ", ".join(f"{r['case']}:{r['status']}" for r in agent_fail))
    excluded = [r for r in rows if r["status"] in HARNESS_FAILURES]
    if excluded:
        lines.append("Harness errors (left out): "
                     + ", ".join(f"{r['case']}:{r['status']}" for r in excluded))

    wrong = [r for r in scored if r["status"] == "ok" and not r["correct"]]
    if wrong:
        lines += ["", "Wrong verdicts:"]
        lines += [f"  {r['case']} rep{r['rep']}: {r['verdict']} - {r['reason']}" for r in wrong]

    tot = {key: sum(r["usage"][key] for r in rows) for key in USAGE_KEYS}
    if tot["input_tokens"] or tot["output_tokens"]:
        line = (f"Tokens: {tot['input_tokens']:,} in, {tot['cache_read_input_tokens']:,} cache read, "
                f"{tot['cache_creation_input_tokens']:,} cache write, {tot['output_tokens']:,} out")
        if model in PRICES:
            p_in, p_out = PRICES[model]
            cost = (tot["input_tokens"] + 1.25 * tot["cache_creation_input_tokens"]
                    + 0.1 * tot["cache_read_input_tokens"]) * p_in / 1e6 \
                + tot["output_tokens"] * p_out / 1e6
            line += f" (about ${cost:.2f})"
        lines += ["", line]
    lines.append(f"Wall clock: {wall_s / 60:.1f} min")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent", choices=["baseline", "llm"], required=True)
    p.add_argument("--port", default="COM3")
    p.add_argument("--cases", help="comma-separated subset of: " + ", ".join(CASES))
    p.add_argument("--reps", type=int, default=1, help="trials per case (default 1)")
    p.add_argument("--seed", type=int, help="shuffle seed (default: random, shown in the summary)")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-turns", type=int, default=20, help="API calls per trial (default 20)")
    p.add_argument("--timeout-s", type=float, default=600, help="per trial (default 600)")
    return p.parse_args()


async def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    args = parse_args()
    cases = args.cases.split(",") if args.cases else list(CASES)
    unknown = [c for c in cases if c not in CASES]
    if unknown:
        sys.exit(f"Unknown case(s): {', '.join(unknown)}. Known: {', '.join(CASES)}")
    missing = [c for c in cases if not (FIRMWARE / f"{c}.ino.merged.bin").is_file()]
    if missing:
        sys.exit(f"Missing firmware for {', '.join(missing)}: run python build_firmware.py")

    if args.agent == "llm":
        client = anthropic.AsyncAnthropic(max_retries=4)
        try:
            await client.models.retrieve(args.model)
        except Exception as e:
            sys.exit(f"Can't use {args.model} through the API: {e}\n"
                     "Set ANTHROPIC_API_KEY (or run `ant auth login`) and try again.")
        agent = llm_agent(client, args.model, args.effort, args.max_turns)
        desc, model = f"llm ({args.model}, effort {args.effort})", args.model
    else:
        agent = baseline_agent
        desc, model = 'baseline ("BOOT OK" substring, 6 s capture)', None

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    trials = [(c, r) for c in cases for r in range(args.reps)]
    random.Random(seed).shuffle(trials)
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}_{args.agent}"
    out = RUNS / run_id
    (out / "traces").mkdir(parents=True)
    header = (f"Run {run_id}\nAgent: {desc}\n"
              f"Cases: {len(cases)} x {args.reps} rep(s), seed {seed}, port {args.port}")
    print(header + "\n", flush=True)

    rows, t_start = [], time.monotonic()
    with open(out / "results.jsonl", "w", encoding="utf-8") as results:
        for i, (case, rep) in enumerate(trials, 1):
            firmware = FIRMWARE / f"{case}.ino.merged.bin"
            trial = Trial(case, rep, PROMPT.format(firmware=CANDIDATE, port=args.port))
            try:
                async with asyncio.timeout(args.timeout_s):
                    await run_trial(trial, firmware, args.port, agent)
            except TimeoutError:
                trial.status, trial.error = "timeout", f"over {args.timeout_s:.0f} s"
            except Exception as e:  # e.g. the server failed to start
                trial.status, trial.error = "harness_error", repr(e)
            trial.finish()

            row = {
                "run": run_id, "agent": args.agent, "model": model,
                "effort": args.effort if model else None,
                "case": case, "rep": rep, "expected": trial.expected,
                "verdict": trial.verdict, "correct": trial.correct, "status": trial.status,
                "reason": trial.reason, "error": trial.error,
                "firmware_sha256": hashlib.sha256(firmware.read_bytes()).hexdigest(),
                "tool_calls": trial.tool_calls, "api_calls": trial.api_calls,
                "served_models": sorted(trial.models), "usage": trial.usage,
                "latency_s": trial.duration, "started_at": trial.started_at,
            }
            rows.append(row)
            results.write(json.dumps(row) + "\n")
            results.flush()
            (out / "traces" / f"{case}_rep{rep}.json").write_text(
                json.dumps(trial.trace, indent=2), encoding="utf-8")

            mark = ("left out" if trial.status in HARNESS_FAILURES
                    else "right" if trial.correct else "WRONG")
            print(f"[{i}/{len(trials)}] {case} rep{rep}: {trial.verdict or '-'} ({trial.status}), "
                  f"expected {trial.expected} -> {mark}  [{trial.duration:.0f} s]", flush=True)

    summary = summarize(rows, header, model, time.monotonic() - t_start)
    (out / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    print("\n" + summary + f"\n\nResults: {out}")


if __name__ == "__main__":
    asyncio.run(main())
