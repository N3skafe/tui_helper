"""Dispatch and foreground execution tests. No interactive terminal."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from completion import CommandCompleter, apply_completion
from commands import command_switches, system_executable
from execute import CommandResult, classify, format_status, run_shell, shell_launch
from main import (
    SafeFileHistory,
    change_directory,
    diskpart_body,
    handle_line,
    nested_program,
    prompt_message,
    run_diskpart_script,
    step_nested,
)


def _python_line(script: Path) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, str(script)])
    import shlex

    return " ".join(shlex.quote(part) for part in (sys.executable, str(script)))


class DispatchTests(unittest.TestCase):
    def test_classify_does_not_execute(self):
        with mock.patch("execute.subprocess.Popen") as popen:
            self.assertEqual(classify("   "), ("empty", []))
            self.assertEqual(classify("HELP"), ("help", []))
            self.assertEqual(classify("  Clear  "), ("clear", []))
            self.assertEqual(classify("Exit"), ("exit", []))
            self.assertEqual(classify('cd "my documents"'), ("cd", ["my documents"]))
            self.assertEqual(classify("echo hi"), ("shell", ["hi"]))
            self.assertEqual(classify("  echo hi"), ("shell", ["hi"]))
            self.assertEqual(classify("cd nested && echo hi")[0], "shell")
            self.assertEqual(classify('cd "my & documents"'), ("cd", ["my & documents"]))
            popen.assert_not_called()

    def test_local_commands_do_not_start_a_shell(self):
        with (
            mock.patch("main.run_shell") as run,
            mock.patch("main.clear_screen"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(handle_line("help"))
            self.assertTrue(handle_line("clear"))
            self.assertTrue(handle_line("cd"))
            self.assertFalse(handle_line("EXIT"))
            run.assert_not_called()

    def test_bare_netsh_and_diskpart_stay_in_the_app(self):
        self.assertEqual(nested_program("netsh"), "netsh")
        self.assertEqual(nested_program("DISKPART"), "diskpart")
        self.assertIsNone(nested_program("netsh wlan show drivers"))
        self.assertIsNone(nested_program("netsh && echo hi"))
        with mock.patch(
            "main.netsh_child_commands",
            return_value=[("wlan", "Changes to the `netsh wlan' context.")],
        ):
            action, context, command = step_nested("netsh", (), "wlan")
        self.assertEqual((action, context, command), ("stay", ("wlan",), ""))
        action, context, command = step_nested("netsh", ("wlan",), "show drivers")
        self.assertEqual(action, "run")
        self.assertEqual(command, "netsh wlan show drivers")
        self.assertEqual(step_nested("netsh", ("wlan",), "exit")[0], "leave")
        self.assertEqual(diskpart_body("diskpart list disk"), "list disk")
        action, _context, command = step_nested("diskpart", (), "list disk")
        self.assertEqual((action, command), ("run", "list disk"))

    def test_diskpart_with_an_operator_stays_a_shell_line(self):
        with (
            mock.patch("main.run_shell") as run,
            mock.patch("main.run_diskpart_script") as script,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            run.return_value = CommandResult(0, "", "", False)
            self.assertTrue(handle_line("diskpart && echo hi"))
        script.assert_not_called()
        run.assert_called_once_with("diskpart && echo hi")

    def test_diskpart_script_is_direct_and_explains_elevation(self):
        seen: dict[str, object] = {}

        def fake_run(args, **_kwargs):
            seen["args"] = args
            seen["body"] = Path(args[2]).read_text(encoding="utf-8")

            class Completed:
                returncode = 0

            return Completed()

        with mock.patch("main.subprocess.run", side_effect=fake_run):
            result = run_diskpart_script("list disk")
        self.assertEqual(result.returncode, 0)
        args = seen["args"]
        self.assertTrue(str(args[0]).lower().endswith("diskpart.exe"))
        self.assertEqual(args[1], "/s")
        self.assertNotIn("cmd.exe", str(args[0]).lower())
        self.assertIn("list disk", seen["body"])
        self.assertIn("exit", seen["body"])

        error = OSError("elevation required")
        error.winerror = 740
        with mock.patch("main.subprocess.run", side_effect=error):
            denied = run_diskpart_script("list disk")
        self.assertEqual(denied.returncode, 740)
        self.assertIn("administrator permission is required", denied.stderr)
        self.assertIn("UAC", denied.stderr)

    def test_submitted_shell_line_is_not_rewritten(self):
        with mock.patch("main.run_shell") as run, contextlib.redirect_stdout(io.StringIO()):
            run.return_value = CommandResult(0, "", "", False)
            self.assertTrue(handle_line('  echo keep "quotes"'))
            run.assert_called_once_with('  echo keep "quotes"')

    def test_shell_launch_is_explicit(self):
        windows = shell_launch('echo keep "quotes"', platform="nt")
        self.assertTrue(windows.executable.lower().endswith("cmd.exe"))
        self.assertIsNone(windows.argv)
        self.assertIn("/d /s /c", windows.command_line)
        self.assertTrue(windows.command_line.endswith('"echo keep "quotes""'))
        self.assertNotIn("powershell", windows.command_line.casefold())

        posix = shell_launch('echo keep "quotes"', platform="posix")
        self.assertIn(posix.argv[0], {"/bin/sh", "/usr/bin/sh"})
        self.assertEqual(posix.argv[1:], ["-c", 'echo keep "quotes"'])
        self.assertIsNone(posix.command_line)

        if os.name == "nt":
            powershell = shell_launch("Write-Output hello-from-ps", platform="nt")
            self.assertTrue(powershell.executable.lower().endswith("powershell.exe"))
            self.assertEqual(
                powershell.argv,
                [
                    powershell.executable,
                    "-NoProfile",
                    "-Command",
                    "Write-Output hello-from-ps",
                ],
            )
            cmd_program = shell_launch("netsh", platform="nt")
            self.assertTrue(cmd_program.executable.lower().endswith("cmd.exe"))

    @unittest.skipUnless(os.name == "nt", "cmd quote handling is Windows-only")
    def test_cmd_keeps_quotes_inside_the_line(self):
        result = run_shell('echo keep "quotes"', capture=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('keep "quotes"', result.stdout)
        self.assertNotIn("\\", result.stdout)

    @unittest.skipUnless(os.name == "nt", "System32 is Windows-only")
    def test_system_executable_ignores_a_fake_system_root(self):
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\fake-windows"}):
            path = system_executable("cmd.exe")
        self.assertFalse(path.lower().startswith(r"c:\fake-windows"))
        self.assertTrue(path.lower().endswith("\\cmd.exe"))
        self.assertTrue(os.path.isfile(path))

    def test_unknown_switch_lookup_does_not_run_a_program(self):
        with mock.patch("commands.subprocess.run") as run:
            self.assertEqual(command_switches("zzzqqq-no-such-tui"), [])
        run.assert_not_called()

    def test_leading_space_is_not_stored(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "history"
        history = SafeFileHistory(path)
        history.append_string(" secret-token")
        history.append_string("echo hi")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("secret-token", text)
        self.assertIn("echo hi", text)

    def test_diskpart_mentions_admin_when_already_elevated(self):
        with (
            mock.patch("main.is_windows_admin", return_value=True),
            mock.patch("main.subprocess.run", return_value=mock.Mock(returncode=0)),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            run_diskpart_script("list disk")
        self.assertIn("administrator", stdout.getvalue().casefold())


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self._previous = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        os.chdir(self.base)

    def tearDown(self):
        os.chdir(self._previous)
        self._tmp.cleanup()

    @unittest.skipUnless(os.name == "nt", "PowerShell is Windows-only")
    def test_powershell_only_command_runs(self):
        result = run_shell("Write-Output hello-from-ps", capture=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello-from-ps", result.stdout)
        self.assertFalse(result.not_found)

    def test_echo_stdout_and_exit_status(self):
        result = run_shell("echo hello-from-shell", capture=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello-from-shell", result.stdout)
        self.assertFalse(result.interrupted)
        self.assertEqual(format_status(result), "[exit 0]")

    def test_failed_command_reports_stderr_and_status(self):
        script = self.base / "fail.py"
        script.write_text(
            "import sys\nsys.stderr.write('fail-marker\\n')\nsys.exit(3)\n",
            encoding="utf-8",
        )
        result = run_shell(_python_line(script), capture=True)
        self.assertEqual(result.returncode, 3)
        self.assertIn("fail-marker", result.stderr)
        self.assertEqual(format_status(result), "[exit 3]")

    def test_missing_command_is_reported(self):
        result = run_shell("this-command-does-not-exist-tui-helper-xyz", capture=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.not_found)
        self.assertIn("command not found", format_status(result))
        self.assertTrue(result.stderr.strip())
        korean = CommandResult(
            1,
            "",
            "'missing-tool'은(는) 내부 또는 외부 명령, 실행할 수 있는 프로그램, 또는\n"
            "배치 파일이 아닙니다.\n",
            False,
            command="missing-tool",
        )
        self.assertTrue(korean.not_found)
        self.assertIn("command not found", format_status(korean))

    def test_shell_variables_do_not_persist(self):
        if os.name == "nt":
            first = "set TUI_HELPER_TESTVAR=from-shell"
            second = "echo %TUI_HELPER_TESTVAR%"
        else:
            first = "TUI_HELPER_TESTVAR=from-shell"
            second = 'echo "$TUI_HELPER_TESTVAR"'
        self.assertEqual(run_shell(first, capture=True).returncode, 0)
        result = run_shell(second, capture=True)
        self.assertNotIn("from-shell", result.stdout)

    def test_compound_cd_runs_in_the_shell(self):
        nested = self.base / "nested"
        nested.mkdir()
        before = Path.cwd().resolve()
        if os.name == "nt":
            line = f'cd /d "{nested}" && echo hello-from-shell'
        else:
            line = f'cd "{nested}" && echo hello-from-shell'
        result = run_shell(line, capture=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello-from-shell", result.stdout)
        self.assertEqual(Path.cwd().resolve(), before)

    def test_shell_cd_does_not_change_process_directory(self):
        nested = self.base / "nested"
        nested.mkdir()
        if os.name == "nt":
            line = f'cd /d "{nested}"'
        else:
            line = f'cd "{nested}"'
        run_shell(line, capture=True)
        self.assertEqual(Path.cwd().resolve(), self.base.resolve())

    def test_interrupt_returns_without_raising(self):
        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self.terminated = False

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                if not self.terminated:
                    raise KeyboardInterrupt
                self.returncode = 1
                return self.returncode

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.terminated = True
                self.returncode = 1

        with mock.patch("execute.subprocess.Popen", return_value=FakeProcess()):
            result = run_shell("echo should-not-run", capture=False)
        self.assertTrue(result.interrupted)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(format_status(result).startswith("[interrupted"))


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self._previous = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        os.chdir(self.base)

    def tearDown(self):
        os.chdir(self._previous)
        self._tmp.cleanup()

    def test_cd_persists_for_later_commands_and_the_prompt(self):
        nested = self.base / "nested"
        nested.mkdir()
        spaced = self.base / "my documents"
        spaced.mkdir()
        with mock.patch("main.run_shell") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(handle_line("cd nested"))
            run.assert_not_called()
        self.assertEqual(Path.cwd().resolve(), nested.resolve())
        self.assertTrue(prompt_message().startswith(os.getcwd()))

        script = self.base / "where.py"
        script.write_text(
            "from pathlib import Path\n"
            "Path('cwd-marker.txt').write_text(str(Path.cwd()), encoding='utf-8')\n",
            encoding="utf-8",
        )
        result = run_shell(_python_line(script), capture=True)
        self.assertEqual(result.returncode, 0)
        marker = nested / "cwd-marker.txt"
        self.assertTrue(marker.is_file())
        self.assertEqual(Path(marker.read_text(encoding="utf-8")).resolve(), nested.resolve())

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(handle_line("cd .."))
            self.assertEqual(Path.cwd().resolve(), self.base.resolve())
            self.assertTrue(handle_line('cd "my documents"'))
            self.assertEqual(Path.cwd().resolve(), spaced.resolve())

    def test_invalid_directory_keeps_the_current_directory(self):
        before = Path.cwd().resolve()
        with mock.patch("builtins.print") as printed:
            self.assertTrue(handle_line("cd missing-dir-tui-helper"))
            self.assertTrue(change_directory(["missing-dir-tui-helper"]) is None)
        self.assertEqual(Path.cwd().resolve(), before)
        messages = " ".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("cd:", messages)
        self.assertIn("missing-dir-tui-helper", messages)

        file_path = self.base / "plain.txt"
        file_path.write_text("x", encoding="utf-8")
        with mock.patch("builtins.print") as printed:
            handle_line(f'cd "{file_path.name}"')
        self.assertEqual(Path.cwd().resolve(), before)
        messages = " ".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("cd:", messages)

        with mock.patch("main.run_shell") as run, mock.patch("builtins.print"):
            handle_line("cd one two")
            run.assert_not_called()
        self.assertEqual(Path.cwd().resolve(), before)


class CompletionDoesNotExecuteTests(unittest.TestCase):
    def test_completion_acceptance_does_not_execute(self):
        with mock.patch("execute.subprocess.Popen") as popen:
            document = Document("git sta")
            completions = list(
                CommandCompleter().get_completions(
                    document, CompleteEvent(completion_requested=True)
                )
            )
            status = next(item for item in completions if item.display_text == "status")
            applied = apply_completion(document, status)
            self.assertEqual(applied.text, "git status")
            popen.assert_not_called()

        with mock.patch("main.run_shell") as run, contextlib.redirect_stdout(io.StringIO()):
            run.return_value = CommandResult(0, "", "", False)
            self.assertTrue(handle_line(applied.text))
            run.assert_called_once_with("git status")

    def test_import_and_completion_do_not_spawn_processes(self):
        with mock.patch("subprocess.Popen") as popen:
            import execute
            import main

            self.assertTrue(callable(main.main))
            self.assertTrue(callable(execute.run_shell))
            list(
                CommandCompleter().get_completions(
                    Document(""), CompleteEvent(text_inserted=True)
                )
            )
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
