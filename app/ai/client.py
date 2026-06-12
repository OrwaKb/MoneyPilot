from __future__ import annotations

import json
import shutil
import subprocess


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


def _via_sdk(prompt: str, system, timeout_s: int) -> str:
    """Claude Agent SDK transport — rides the local Claude Code login."""
    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, query

    async def _run():
        opts = ClaudeAgentOptions(system_prompt=system, max_turns=1,
                                  allowed_tools=[])
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


def _via_cli(prompt: str, system, timeout_s: int) -> str:
    """`claude -p` headless transport — same login, zero extra deps."""
    exe = shutil.which("claude")
    if not exe:
        raise AIUnavailable("claude CLI not found on PATH")
    cmd = [exe, "-p", "--output-format", "json", "--max-turns", "1"]
    if system:
        cmd += ["--append-system-prompt", system]
    try:
        res = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise AIUnavailable("claude CLI timed out") from e
    if res.returncode != 0:
        raise AIUnavailable(f"claude CLI exit {res.returncode}: {res.stderr[:200]}")
    try:
        out = json.loads(res.stdout).get("result")
    except (json.JSONDecodeError, AttributeError):
        out = res.stdout
    if not out or not str(out).strip():
        raise AIUnavailable("empty reply from claude CLI")
    return str(out)


def ask_claude(prompt: str, system=None, timeout_s: int = 60) -> str:
    """Primary: Agent SDK. Fallback: claude -p. Raises AIUnavailable if both fail."""
    try:
        return _via_sdk(prompt, system, timeout_s)
    except Exception as sdk_exc:
        try:
            return _via_cli(prompt, system, timeout_s)
        except AIUnavailable as cli_exc:
            raise AIUnavailable(f"SDK: {sdk_exc!r}; CLI: {cli_exc}") from cli_exc
