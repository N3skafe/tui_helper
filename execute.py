"""Run one submitted line in a fresh foreground shell.

Windows runs System32 ``cmd.exe /d /s /c`` with the submitted line as one
argument. PowerShell-only command names, and lines that look like PowerShell
(``$var = ...``, ``if ($true)``, ``[string]$x = ...``), run
``powershell.exe -NoProfile -Command``. Linux runs ``/bin/sh -c`` (or
``/usr/bin/sh -c`` when ``/bin/sh`` is missing). Each submission starts a new
shell process. Values set in one submission do not persist to the next.

The Python process environment is passed through. This does not attach to
the parent CMD or PowerShell session, does not load CMD AutoRun (``/d``),
and does not load bash aliases, functions, or shell variables.

The submitted text is the shell command string. It is not rewritten.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from commands import (
    command_origin,
    decode_console_bytes,
    powershell_executable,
    prefer_english,
    system_executable,
)
from completion import parse_argv

_CONTINUATION_HINT = (
    "Trailing backtick continuation is ignored. "
    "This app submits one line at a time. Put the whole statement on one line."
)
_LOCAL_COMMANDS = frozenset({"help", "clear", "exit", "cd"})
_PS_CONTROL = frozenset({"if", "foreach", "while", "switch", "for"})
_POSIX_SHELLS = ("/bin/sh", "/usr/bin/sh")


@dataclass(frozen=True)
class ShellLaunch:
    """How to start the platform shell for one unmodified command line."""

    executable: str
    command_line: str | None
    argv: list[str] | None


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    interrupted: bool
    command: str = ""

    @property
    def not_found(self) -> bool:
        if self.interrupted or self.returncode is None:
            return False
        if self.returncode in (127, 9009):
            return True
        text = f"{self.stdout}\n{self.stderr}"
        folded = text.casefold()
        if (
            "not recognized as an internal or external command" in folded
            or "command not found" in folded
            or ": not found" in folded
            or "내부 또는 외부 명령" in text
            or "배치 파일이 아닙니다" in text
        ):
            return True
        # cmd quotes the missing name in every locale: 'name' ...
        name = _command_token(self.command)
        return bool(name) and f"'{name}'" in text


def _command_token(command: str) -> str:
    argv = parse_argv(command)
    if argv:
        return argv[0]
    return command.strip()


def _statement_for_routing(command: str) -> str:
    """Drop one trailing continuation backtick so routing sees the statement."""
    text = command.strip()
    if text.endswith("`"):
        return text[:-1].rstrip()
    return text


def has_trailing_continuation_backtick(command: str) -> bool:
    """True when the line ends with a PowerShell continuation backtick."""
    return command.rstrip().endswith("`")


def looks_like_powershell(command: str) -> bool:
    """True for a few PowerShell forms that cmd would not treat the same way.

    This does not launch a shell. Ordinary cmd lines stay false.
    """
    text = _statement_for_routing(command)
    if not text:
        return False
    first = _command_token(text)
    if first.startswith("$"):
        return True
    folded = text.casefold()
    if folded.startswith("function ") or folded.startswith("param("):
        return True
    if _is_dot_scriptblock(text):
        return True
    if _control_flow_with_dollar(first, text):
        return True
    return _has_powershell_marker(text)


def _is_dot_scriptblock(text: str) -> bool:
    """True for PowerShell ``. { ... }``. ``.\\program`` stays a cmd path."""
    stripped = text.lstrip()
    if not stripped.startswith(".") or stripped.startswith(".\\") or stripped.startswith("./"):
        return False
    return stripped[1:].lstrip().startswith("{")


def _control_flow_with_dollar(first: str, text: str) -> bool:
    """``if``/``foreach``/``while``/``switch``/``for`` whose ``(...)`` contains ``$``."""
    folded = first.casefold()
    matched = False
    for keyword in _PS_CONTROL:
        if folded == keyword or folded.startswith(keyword + "("):
            matched = True
            break
    if not matched:
        return False
    return _unquoted_paren_has_dollar(text)


def _unquoted_paren_has_dollar(text: str) -> bool:
    quote = ""
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            index += 1
            continue
        if char != "(":
            index += 1
            continue
        depth = 1
        saw_dollar = False
        index += 1
        while index < length and depth:
            inner = text[index]
            if quote:
                if inner == quote:
                    quote = ""
                index += 1
                continue
            if inner in ("'", '"'):
                quote = inner
                index += 1
                continue
            if inner == "(":
                depth += 1
            elif inner == ")":
                depth -= 1
            elif inner == "$":
                saw_dollar = True
            index += 1
        if saw_dollar and depth == 0:
            return True
    return False


def _has_powershell_marker(text: str) -> bool:
    """Look for ``$_``, ``@{``, ``@(``, or ``& {`` outside quotes."""
    quote = ""
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            index += 1
            continue
        if text.startswith("$_", index) or text.startswith("@{", index) or text.startswith("@(", index):
            return True
        if text.startswith("]$", index):
            return True
        if char == "&":
            cursor = index + 1
            while cursor < length and text[cursor] in " \t":
                cursor += 1
            if cursor < length and text[cursor] == "{":
                return True
        index += 1
    return False


def _decode_captured(data: bytes | None) -> str:
    if not data:
        return ""
    return decode_console_bytes(data)


def _has_unquoted_operator(text: str) -> bool:
    """True when a shell operator sits outside quotes.

    A plain ``cd`` stays in this process. ``cd dir && other`` is left for the
    shell so the rest of the submitted line still runs.
    """
    quote = ""
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            index += 1
            continue
        if text.startswith("&&", index) or text.startswith("||", index):
            return True
        if char in "|;><&":
            return True
        index += 1
    return False


def classify(text: str) -> tuple[str, list[str]]:
    """Return ``(kind, args)`` without running anything.

    ``kind`` is ``empty``, ``help``, ``clear``, ``exit``, ``cd``, or ``shell``.
    Builtin names are matched without case sensitivity. The original line is
    left unchanged for the shell.
    """
    argv = parse_argv(text)
    if not argv:
        return "empty", []
    name = argv[0].casefold()
    if name in _LOCAL_COMMANDS and not _has_unquoted_operator(text):
        return name, argv[1:]
    return "shell", argv[1:]


def windows_shell() -> str:
    return system_executable("cmd.exe")


def posix_shell() -> str:
    for candidate in _POSIX_SHELLS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return _POSIX_SHELLS[0]


def shell_launch(command: str, platform: str | None = None) -> ShellLaunch:
    """Build the shell invocation. ``command`` is copied through as-is."""
    system = os.name if platform is None else platform
    if system == "nt":
        if looks_like_powershell(command) or command_origin(_command_token(command)) == "powershell":
            executable = powershell_executable()
            return ShellLaunch(
                executable,
                None,
                [executable, "-NoProfile", "-Command", command],
            )
        executable = windows_shell()
        # /s makes cmd strip one pair of wrapping quotes and keep quotes
        # inside the submitted line. list2cmdline would escape those quotes
        # in a way cmd does not understand.
        command_line = f'"{executable}" /d /s /c "{command}"'
        return ShellLaunch(executable, command_line, None)
    executable = posix_shell()
    return ShellLaunch(executable, None, [executable, "-c", command])


def format_status(result: CommandResult) -> str:
    if result.interrupted:
        if result.returncode is None:
            return "[interrupted]"
        return f"[interrupted {result.returncode}]"
    label = f"[exit {result.returncode}]"
    if result.not_found:
        return f"{label} command not found"
    return label


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            return
        try:
            proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            return
    except KeyboardInterrupt:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
            return


def run_shell(
    command: str,
    *,
    cwd: Path | str | None = None,
    capture: bool = False,
) -> CommandResult:
    """Run ``command`` in the foreground shell.

    ``capture=False`` leaves stdin, stdout, and stderr attached to the
    terminal so the command can use it. Call this only after the
    prompt_toolkit session has returned the line. ``capture=True`` collects
    text for tests and does not read the terminal.
    """
    prefer_english()
    if os.name == "nt" and has_trailing_continuation_backtick(command):
        print(_CONTINUATION_HINT)
    launch = shell_launch(command)
    workdir = os.fspath(Path.cwd() if cwd is None else cwd)
    stdout: int | None = subprocess.PIPE if capture else None
    stderr: int | None = subprocess.PIPE if capture else None
    stdin: int | None = subprocess.DEVNULL if capture else None
    popen_kwargs: dict = {
        "cwd": workdir,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "shell": False,
    }
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        if launch.command_line is not None:
            proc = subprocess.Popen(
                launch.command_line,
                executable=launch.executable,
                **popen_kwargs,
            )
        else:
            proc = subprocess.Popen(launch.argv, **popen_kwargs)
    except FileNotFoundError as exc:
        message = f"shell not found: {exc.filename or launch.executable}\n"
        return CommandResult(127, "", message, False, command)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        message = f"shell: {detail}\n"
        return CommandResult(
            exc.errno if isinstance(exc.errno, int) else 1,
            "",
            message,
            False,
            command,
        )

    interrupted = False
    out = ""
    err = ""
    try:
        if capture:
            raw_out, raw_err = proc.communicate()
            out = _decode_captured(raw_out)
            err = _decode_captured(raw_err)
        else:
            proc.wait()
    except KeyboardInterrupt:
        interrupted = True
        _stop_process(proc)
    returncode = proc.poll()
    if returncode is None:
        returncode = proc.returncode
    return CommandResult(returncode, out or "", err or "", interrupted, command)
