from __future__ import annotations

import json
import logging
import shutil
import subprocess

log = logging.getLogger("moneypilot.ai")


class AIUnavailable(Exception):
    """Claude could not be reached / produced nothing usable. Callers fall back."""


def extract_json(text: str, opener: str = "["):
    """Pull the first JSON value opening with `opener` out of an AI reply,
    tolerating prose and markdown fences around it."""
    i = text.find(opener)
    if i < 0:
        raise ValueError(f"no {opener!r} found in AI reply")
    obj, _ = json.JSONDecoder().raw_decode(text[i:])
    return obj


def _via_sdk(prompt: str, system, timeout_s: int, web: bool = False) -> str:
    """Claude Agent SDK transport — rides the local Claude Code login.

    web=True opts into web search (chat only): expose ONLY WebSearch and allow
    several turns for the search->read->answer round-trip. web=False keeps the
    model tool-free for FACTS-only callers (briefing, parser, onboarding)."""
    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, query

    async def _run():
        if web:
            # Give the model EXACTLY WebSearch — both exposed (`tools`) and
            # auto-approved (`allowed_tools`), so no shell/file tool is reachable
            # and no permission prompt blocks it headless. max_turns>1 is
            # essential: a tool call costs a turn, so at max_turns=1 the model
            # would die on error_max_turns before it could answer (the very bug
            # that made the advisor crash on internet questions).
            opts = ClaudeAgentOptions(system_prompt=system, max_turns=6,
                                      tools=["WebSearch"],
                                      allowed_tools=["WebSearch"])
        else:
            # tools=[] makes the CLI receive `--tools ""` — an EMPTY base toolset.
            # This is load-bearing: the model answers from FACTS + general
            # knowledge only, and with max_turns=1 any tool call is fatal (on an
            # internet-ish question the model spends its single turn on a tool
            # call and the SDK returns error_max_turns instead of an answer).
            # allowed_tools=[] does NOT achieve this: an empty allow-list is
            # falsy, so the SDK omits --allowedTools and every tool stays live.
            opts = ClaudeAgentOptions(system_prompt=system, max_turns=1, tools=[])
        result = None
        with anyio.move_on_after(timeout_s):
            async for message in query(prompt=prompt, options=opts):
                if getattr(message, "is_error", False):
                    raise RuntimeError(f"SDK error result: "
                                       f"{getattr(message, 'result', '')!r}")
                r = getattr(message, "result", None)
                if isinstance(r, str):
                    result = r
        if result is None:
            raise TimeoutError("no result from Agent SDK")
        return result

    return anyio.run(_run)


def _via_cli(prompt: str, system, timeout_s: int, web: bool = False) -> str:
    """`claude -p` headless transport — same login, zero extra deps.

    Mirrors _via_sdk's tool policy: web=True exposes+allows only WebSearch with
    several turns; web=False stays tool-free (empty base toolset, one turn)."""
    exe = shutil.which("claude")
    if not exe:
        raise AIUnavailable("claude CLI not found on PATH")
    if web:
        cmd = [exe, "-p", "--output-format", "json", "--max-turns", "6",
               "--tools", "WebSearch", "--allowedTools", "WebSearch"]
    else:
        # --tools "" gives an empty base toolset (mirrors _via_sdk): the fallback
        # must also stay tool-free, or an internet-ish question burns its one turn
        # on a tool call and exits non-zero / empty -> a needless "Advisor offline".
        cmd = [exe, "-p", "--output-format", "json", "--max-turns", "1",
               "--tools", ""]
    if system:
        cmd += ["--append-system-prompt", system]
    try:
        res = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise AIUnavailable("claude CLI timed out") from e
    except OSError as e:
        # the exe exists but fails to launch (perms, bad image, ENOEXEC…):
        # surface as AIUnavailable so the advisor falls back gracefully
        raise AIUnavailable(f"claude CLI failed to launch: {e}") from e
    if res.returncode != 0:
        raise AIUnavailable(f"claude CLI exit {res.returncode}: {res.stderr[:200]}")
    try:
        out = json.loads(res.stdout).get("result")
    except (json.JSONDecodeError, AttributeError):
        out = res.stdout
    if not out or not str(out).strip():
        raise AIUnavailable("empty reply from claude CLI")
    return str(out)


def ask_claude(prompt: str, system=None, timeout_s: int = 60,
               web: bool = False) -> str:
    """Primary: Agent SDK. Fallback: claude -p. Raises AIUnavailable if both fail.

    web=True lets the model use web search (chat uses this for live data); the
    default keeps every other caller tool-free."""
    try:
        return _via_sdk(prompt, system, timeout_s, web)
    except Exception as sdk_exc:
        try:
            return _via_cli(prompt, system, timeout_s, web)
        except AIUnavailable as cli_exc:
            # Both transports failed. Log the detail so a recurrence is
            # diagnosable — the GUI suppresses consoles, so this file log is
            # the only trace of WHY the advisor went offline.
            log.warning("AI unavailable — both transports failed. "
                        "SDK: %r ; CLI: %s", sdk_exc, cli_exc)
            raise AIUnavailable(f"SDK: {sdk_exc!r}; CLI: {cli_exc}") from cli_exc
