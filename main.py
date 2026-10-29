"""Interactive prompt. Completion lives in completion.py; execution in execute.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.filters import has_completions
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.shortcuts import CompleteStyle, PromptSession
from prompt_toolkit.styles import Style

from commands import (
    INTERACTIVE_PROGRAMS,
    is_context_switch,
    is_windows_admin,
    netsh_child_commands,
    prefer_english,
    restrict_to_current_user,
    system_executable,
)
from completion import CommandCompleter, parse_argv, tokenize
from execute import CommandResult, _has_unquoted_operator, classify, format_status, run_shell

HISTORY_NAME = ".tui_helper_history"

HELP_TEXT = """\
commands
  help                 show this help
  clear                clear the screen
  exit                 quit
  cd <dir>             change this process directory
  other lines          run in the foreground shell

shell
  Windows cmd          System32\\cmd.exe /d /s /c
  Windows PowerShell   System32 powershell.exe -NoProfile -Command
                       names that exist only in PowerShell
  netsh, diskpart      stay in this prompt. exit returns
  Linux                /bin/sh -c

history
  saved lines          home directory .tui_helper_history
  leading space        that line is not saved
  passwords            stay in that file as plain text

keys
  typing               open the menu. system help may be read
  Ctrl+Space           open the menu
  Up/Down              select a candidate, or history when the menu is closed
  Tab                  insert the selection, or open the menu
  Enter                accept the selection and close the menu
  Enter                submit the line when the menu is closed
  Esc                  close the menu and undo a preview
  Right                accept the gray history suggestion
  Ctrl+C               cancel the line, or the running command
  Ctrl+D               quit
"""

STYLE = Style.from_dict(
    {
        "completion-menu": "bg:#262626 #f2f2f2",
        "completion-menu.completion": "bg:#262626 #f2f2f2",
        "completion-menu.completion.current": "bg:#005f87 #ffffff",
        "completion-menu.meta.completion": "bg:#262626 #9a9a9a",
        "completion-menu.meta.completion.current": "bg:#005f87 #ffffff",
        "scrollbar.background": "bg:#262626",
        "scrollbar.button": "bg:#767676",
        "auto-suggestion": "#6c6c6c",
    }
)


class SafeFileHistory(FileHistory):
    """File history that ignores storage errors after startup."""

    def load_history_strings(self):
        try:
            yield from super().load_history_strings()
        except OSError:
            return

    def append_string(self, string: str) -> None:
        if string.startswith(" "):
            return
        super().append_string(string)

    def store_string(self, string: str) -> None:
        try:
            super().store_string(string)
        except OSError:
            return


def create_history() -> History:
    """Store history under the home directory, or keep it in memory."""
    try:
        path = Path.home() / HISTORY_NAME
        with path.open("a", encoding="utf-8"):
            pass
        restrict_to_current_user(str(path))
    except (OSError, RuntimeError):
        return InMemoryHistory()
    return SafeFileHistory(path)


def _commit_completion(event: KeyPressEvent) -> None:
    """Keep the selected candidate and close the menu without submitting."""
    buffer = event.current_buffer
    state = buffer.complete_state
    if state is None or not state.completions:
        return
    index = 0 if state.complete_index is None else state.complete_index
    buffer.go_to_completion(index)
    buffer.complete_state = None


def _key_bindings(completer: CommandCompleter) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter", filter=has_completions, eager=True)
    def _accept_completion(event: KeyPressEvent) -> None:
        completer.suppress_while_typing = True
        _commit_completion(event)

    @bindings.add("c-i", eager=True)
    def _tab(event: KeyPressEvent) -> None:
        buffer = event.current_buffer
        if buffer.complete_state and buffer.complete_state.completions:
            completer.suppress_while_typing = True
            _commit_completion(event)
            return
        completer.suppress_while_typing = False
        buffer.start_completion(select_first=False)

    @bindings.add("escape", filter=has_completions, eager=True)
    def _close_menu(event: KeyPressEvent) -> None:
        event.current_buffer.cancel_completion()

    @bindings.add("c-space", eager=True)
    def _open_menu(event: KeyPressEvent) -> None:
        completer.suppress_while_typing = False
        buffer = event.current_buffer
        if buffer.complete_state:
            return
        buffer.start_completion(select_first=False)

    @bindings.add("c-d", eager=True)
    def _quit(event: KeyPressEvent) -> None:
        event.app.exit(exception=EOFError())

    return bindings


def clear_screen() -> None:
    try:
        from prompt_toolkit.shortcuts import clear

        clear()
    except Exception:
        print("\x1b[2J\x1b[H", end="", flush=True)


def prompt_message() -> str:
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = "?"
    return f"{cwd}> "


def change_directory(args: list[str]) -> None:
    """Change this process directory. A later shell command inherits it."""
    if not args:
        try:
            print(os.getcwd())
        except OSError:
            print("cd: cannot read the working directory")
        return
    if len(args) != 1:
        print("cd: one path expected")
        return
    try:
        os.chdir(args[0])
    except OSError:
        print(f"cd: cannot change directory: {args[0]}")
        return
    try:
        print(os.getcwd())
    except OSError:
        print("cd: cannot read the working directory")


def _emit_result(result: CommandResult) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        sys.stderr.write(result.stderr if result.stderr.endswith("\n") else result.stderr + "\n")
    print(format_status(result))


def nested_program(text: str) -> str | None:
    """Bare ``netsh`` or ``diskpart`` opens an in-app prompt."""
    if _has_unquoted_operator(text):
        return None
    argv = parse_argv(text)
    if len(argv) == 1 and argv[0].casefold() in INTERACTIVE_PROGRAMS:
        return argv[0].casefold()
    return None


def nested_message(program: str, context: tuple[str, ...]) -> str:
    return " ".join((program, *context)) + "> "


def step_nested(
    program: str,
    context: tuple[str, ...],
    line: str,
) -> tuple[str, tuple[str, ...], str]:
    """Decide what a line inside netsh or diskpart does.

    Returns ``(action, context, command)``. ``action`` is ``stay``, ``leave``,
    or ``run``. ``command`` is set only for ``run``.
    """
    stripped = line.strip()
    if not stripped:
        return "stay", context, ""
    if stripped.casefold() in {"exit", "quit"}:
        return "leave", (), ""
    if stripped == "..":
        if context:
            return "stay", context[:-1], ""
        return "leave", (), ""
    argv = parse_argv(stripped)
    if program == "netsh" and len(argv) == 1:
        for name, description in netsh_child_commands(context):
            if name.casefold() == argv[0].casefold() and is_context_switch(description):
                return "stay", context + (name,), ""
    if program == "diskpart":
        return "run", context, stripped
    command = " ".join((program, *context, stripped))
    return "run", context, command


def diskpart_body(text: str) -> str:
    """Drop a leading diskpart token. The rest is the script body."""
    tokens = tokenize(text)
    if tokens and tokens[0].value.casefold() == "diskpart":
        return text[tokens[0].end :].strip()
    return text.strip()


def run_diskpart_script(body: str) -> CommandResult:
    """Run one diskpart script. This does not request elevation.

    diskpart.exe rejects a normal token before it reads the script. Call it
    directly so the script path is not damaged by cmd quoting.
    """
    if is_windows_admin():
        print(
            "diskpart: this program is already running as administrator. "
            "diskpart uses that permission."
        )
    script = body.strip() + "\r\nexit\r\n"
    fd, path = tempfile.mkstemp(prefix="tui-diskpart-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(script)
        restrict_to_current_user(path)
        return _run_diskpart_file(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _diskpart_executable() -> str:
    return system_executable("diskpart.exe")


def _run_diskpart_file(path: str) -> CommandResult:
    prefer_english()
    try:
        completed = subprocess.run(
            [_diskpart_executable(), "/s", path],
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        if winerror in {5, 740}:
            message = (
                "diskpart: administrator permission is required. "
                "Start this program from an elevated terminal. "
                "A UAC prompt is not requested.\n"
            )
            return CommandResult(winerror, "", message, False, "diskpart")
        detail = exc.strerror or str(exc)
        return CommandResult(winerror or 1, "", f"diskpart: {detail}\n", False, "diskpart")
    return CommandResult(completed.returncode, "", "", False, "diskpart")


def handle_line(text: str) -> bool:
    """Run one submitted line. Return False when the session should stop.

    Completion never calls this. The prompt loop calls it only after Enter
    submits the line and the input session has released the terminal.
    """
    kind, args = classify(text)
    if kind == "empty":
        return True
    if kind == "help":
        print(HELP_TEXT, end="")
        return True
    if kind == "clear":
        clear_screen()
        return True
    if kind == "exit":
        return False
    if kind == "cd":
        change_directory(args)
        return True
    command = parse_argv(text)
    if (
        command
        and command[0].casefold() == "diskpart"
        and not _has_unquoted_operator(text)
    ):
        _emit_result(run_diskpart_script(diskpart_body(text)))
        return True
    _emit_result(run_shell(text))
    return True


def _resume_completion(completer: CommandCompleter):
    def handler(_buffer) -> None:
        completer.suppress_while_typing = False

    return handler


def main() -> None:
    completer = CommandCompleter()
    session: PromptSession[str] = PromptSession(
        message="> ",
        completer=completer,
        history=create_history(),
        auto_suggest=AutoSuggestFromHistory(),
        complete_while_typing=True,
        complete_style=CompleteStyle.COLUMN,
        reserve_space_for_menu=8,
        key_bindings=_key_bindings(completer),
        style=STYLE,
        enable_history_search=False,
        enable_open_in_editor=False,
        enable_system_prompt=False,
        enable_suspend=False,
        mouse_support=False,
        wrap_lines=True,
    )
    session.default_buffer.on_text_insert.add_handler(_resume_completion(completer))
    prefer_english()
    print("tui helper | type help, Ctrl+D quits")
    nesting: tuple[str, ...] | None = None
    while True:
        completer.suppress_while_typing = False
        completer.nesting = nesting
        try:
            if nesting:
                text = session.prompt(message=nested_message(nesting[0], nesting[1:]))
            else:
                text = session.prompt(message=prompt_message())
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        except Exception as exc:
            if type(exc).__name__ == "NoConsoleScreenBufferError":
                print("Run this inside Windows Terminal, cmd, or a Linux terminal.")
                raise SystemExit(1) from exc
            raise
        if nesting:
            action, context, command = step_nested(nesting[0], nesting[1:], text)
            if action == "leave":
                nesting = None
                continue
            if action == "stay":
                nesting = (nesting[0], *context)
                continue
            if nesting[0] == "diskpart":
                _emit_result(run_diskpart_script(command))
            else:
                _emit_result(run_shell(command))
            continue
        program = nested_program(text)
        if program:
            print(f"{program} prompt. Type exit to return.")
            nesting = (program,)
            continue
        if not handle_line(text):
            break


if __name__ == "__main__":
    main()
