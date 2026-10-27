"""Run one submitted line in a fresh foreground shell.

Windows runs ``%SystemRoot%\\System32\\cmd.exe /d /c`` with the submitted
line. Linux runs ``/bin/sh -c`` (or ``/usr/bin/sh -c`` when ``/bin/sh`` is
missing). Each submission starts a new shell process.

The Python process environment is passed through. This does not attach to
the parent CMD or PowerShell session, does not load CMD AutoRun (``/d``),
and does not load bash aliases, functions, or shell variables. Values set
inside one command do not persist to the next command.

The submitted text is the shell command string. It is not rewritten.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from commands import command_origin, decode_console_bytes, powershell_executable, prefer_english
from completion import parse_argv

_LOCAL_COMMANDS = frozenset({"help", "clear", "exit", "cd"})
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
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(system_root) / "System32" / "cmd.exe")


def posix_shell() -> str:
    for candidate in _POSIX_SHELLS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return _POSIX_SHELLS[0]


def shell_launch(command: str, platform: str | None = None) -> ShellLaunch:
    """Build the shell invocation. ``command`` is copied through as-is."""
    system = os.name if platform is None else platform
    if system == "nt":
        if command_origin(_command_token(command)) == "powershell":
            executable = powershell_executable()
            return ShellLaunch(
                executable,
                None,
                [executable, "-NoProfile", "-Command", command],
            )
        executable = windows_shell()
        command_line = f'"{executable}" /d /c "{command}"'
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
