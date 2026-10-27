"""Unit tests for completion. No interactive terminal is required."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from commands import (
    APP_COMMAND_NAMES,
    _next_netsh_words,
    _parse_switches,
    decode_console_bytes,
    diskpart_child_commands,
    is_context_switch,
    parse_netsh_help,
)
from completion import ROOT_COMMANDS, CommandCompleter, apply_completion, parse_argv
from git_commands import (
    git_commands,
    git_resolved_command,
    parse_git_alias_lines,
    parse_git_help_listing,
    parse_git_nested,
    parse_git_options,
)


def _labels(completions) -> list[str]:
    return [item.display_text for item in completions]


def _metas(completions) -> list[str]:
    return [item.display_meta_text for item in completions]


class CompletionTestCase(unittest.TestCase):
    def complete(self, text, cursor=None, base=None, event=None):
        if cursor is None:
            cursor = len(text)
        document = Document(text, cursor_position=cursor)
        completer = CommandCompleter(base_dir=base)
        if event is None:
            event = CompleteEvent(completion_requested=True)
        completions = list(completer.get_completions(document, event))
        return document, completions

    def labels(self, text, cursor=None, base=None):
        _document, completions = self.complete(text, cursor=cursor, base=base)
        return _labels(completions)

    def by_label(self, text, label, cursor=None, base=None):
        document, completions = self.complete(text, cursor=cursor, base=base)
        found = [item for item in completions if item.display_text == label]
        self.assertEqual(len(found), 1, _labels(completions))
        return document, found[0]

    def assert_tail_preserved(self, document, completion):
        applied = apply_completion(document, completion)
        cut = document.cursor_position + completion.start_position
        self.assertGreaterEqual(cut, 0)
        self.assertLessEqual(cut, document.cursor_position)
        self.assertEqual(applied.text[:cut], document.text[:cut])
        self.assertTrue(applied.text.endswith(document.text[document.cursor_position :]))
        self.assertEqual(
            applied.cursor_position,
            len(applied.text) - len(document.text[document.cursor_position :]),
        )
        return applied


class CommandTests(CompletionTestCase):
    def test_root_order_descriptions_and_empty_input(self):
        _document, completions = self.complete("")
        self.assertEqual(
            _labels(completions),
            ["help", "clear", "exit", "git", "docker", "npm", "cd", "open"],
        )
        self.assertEqual(
            _metas(completions),
            [
                "Show help",
                "Clear the screen",
                "Exit",
                "Git commands",
                "Docker commands",
                "npm commands",
                "Change directory",
                "Open a path",
            ],
        )
        self.assertEqual(_labels(completions), self.labels(""))
        self.assertEqual(
            tuple(name for name, _description in ROOT_COMMANDS),
            APP_COMMAND_NAMES,
        )

    def test_prefix_is_case_insensitive_and_keeps_listed_order(self):
        # clear stays before cd; alphabetical order would put cd first.
        folded = [name.casefold() for name in self.labels("c")]
        self.assertLess(folded.index("clear"), folded.index("cd"))
        self.assertEqual(folded, [name.casefold() for name in self.labels("C")])
        self.assertEqual(self.labels("d")[0], "docker")
        self.assertEqual(self.labels("Gi")[0], "git")
        self.assertEqual(self.labels("NPM")[0], "npm")
        if os.name != "nt":
            self.assertEqual(self.labels("c"), ["clear", "cd"])
            self.assertEqual(self.labels("d"), ["docker"])

    def test_unknown_command_and_blank_word(self):
        self.assertEqual(self.labels("zzzqqq-no-such"), [])
        self.assertEqual(self.labels("zzz "), [])
        self.assertEqual(self.labels("git nope"), [])
        self.assertEqual(self.labels("git status extra"), [])

    def test_subcommand_order_and_descriptions(self):
        names = [name.casefold() for name in self.labels("git ")]
        self.assertIn("status", names)
        self.assertIn("commit", names)
        self.assertIn("push", names)
        self.assertIn("rebase", names)
        self.assertIn("stash", names)
        self.assertLess(names.index("add"), names.index("commit"))
        self.assertLess(names.index("commit"), names.index("push"))
        _document, git_items = self.complete("git sta")
        status = next(item for item in git_items if item.display_text.casefold() == "status")
        self.assertIn("status", status.display_meta_text.casefold())
        sta = [name.casefold() for name in _labels(git_items)]
        self.assertIn("stash", sta)
        self.assertTrue(all(name.startswith("sta") for name in sta))
        che = [name.casefold() for name in self.labels("GIT CHE")]
        self.assertIn("checkout", che)
        self.assertIn("cherry-pick", che)
        self.assertEqual(self.labels("docker "), ["ps", "images", "logs"])
        self.assertEqual(self.labels("npm "), ["install", "run", "test"])
        self.assertEqual(self.labels("npm t"), ["test"])
        self.assertEqual(self.labels("npm T"), ["test"])

    def test_exact_command_does_not_offer_subcommands_until_a_space(self):
        git_labels = self.labels("git")
        npm_labels = self.labels("npm")
        self.assertEqual(git_labels[0], "git")
        self.assertEqual(npm_labels[0], "npm")
        self.assertNotIn("status", git_labels)
        self.assertNotIn("commit", git_labels)
        self.assertNotIn("push", git_labels)
        if os.name != "nt":
            self.assertEqual(git_labels, ["git"])
            self.assertEqual(npm_labels, ["npm"])

    def test_replacement_position_at_end_of_prefix(self):
        document, completion = self.by_label("docker im", "images")
        self.assertEqual(completion.start_position, -len("im"))
        self.assertEqual(
            document.text[: document.cursor_position + completion.start_position],
            "docker ",
        )
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "docker images")
        self.assertEqual(completion.text, "images")

    def test_midline_edit_preserves_later_text(self):
        cursor = len("docker im")
        document, completion = self.by_label(
            "docker im --all", "images", cursor=cursor
        )
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "docker images --all")
        self.assertEqual(applied.text[applied.cursor_position :], " --all")

    def test_existing_suffix_is_not_duplicated(self):
        cursor = len("git che")
        document, completion = self.by_label(
            "git checkout", "checkout", cursor=cursor
        )
        self.assertEqual(completion.display_text, "checkout")
        self.assertEqual(completion.start_position, -len("che"))
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "git checkout")
        self.assertEqual(applied.text.count("checkout"), 1)

        cursor = len("npm te")
        document, completion = self.by_label(
            "npm test 안녕", "test", cursor=cursor
        )
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "npm test 안녕")
        self.assertTrue(applied.text[applied.cursor_position :].endswith("안녕"))

    def test_suffix_and_following_words_stay(self):
        cursor = len("git sta")
        document, completion = self.by_label(
            "git status --verbose", "status", cursor=cursor
        )
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "git status --verbose")

    def test_different_candidate_keeps_text_after_cursor(self):
        cursor = len("npm ")
        document, completion = self.by_label("npm install", "test", cursor=cursor)
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "npm testinstall")
        self.assertTrue(applied.text.endswith("install"))

        document, completion = self.by_label("npm install", "install", cursor=cursor)
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "npm install")
        self.assertEqual(applied.text.count("install"), 1)

    def test_case_is_normalized_for_the_replaced_prefix(self):
        document, completion = self.by_label("GIT sta", "status")
        applied = apply_completion(document, completion)
        self.assertEqual(applied.text, "GIT status")

        document, completion = self.by_label("GIT", "git")
        self.assertEqual(apply_completion(document, completion).text, "git")

    def test_leading_space_stays(self):
        document, completion = self.by_label("  gi", "git")
        self.assertEqual(apply_completion(document, completion).text, "  git")

    @unittest.skipUnless(os.name == "nt", "cmd and PowerShell catalogs are Windows-only")
    def test_windows_commands_appear_by_prefix(self):
        folded = [name.casefold() for name in self.labels("netsh")]
        self.assertIn("netsh", folded)
        self.assertIn("dir", [name.casefold() for name in self.labels("dir")])
        self.assertIn("cls", [name.casefold() for name in self.labels("cl")])
        powershell = [name.casefold() for name in self.labels("Get-Process")]
        self.assertIn("get-process", powershell)
        self.assertEqual(self.labels("Get-Process "), [])

    def test_netsh_help_parser_reads_wlan_show_drivers(self):
        english = "\n".join(
            [
                "Commands in this context:",
                "show all       - Shows complete wireless information.",
                "show drivers   - Shows properties of the wireless LAN drivers.",
                "                 disabled.",
            ]
        )
        korean = "\n".join(
            [
                "이 컨텍스트에 있는 명령:",
                "show drivers   - 시스템의 무선 LAN 드라이버 속성을 표시합니다.",
                "                 표시합니다.",
            ]
        )
        for text in (english, korean):
            parsed = parse_netsh_help(text)
            words = dict(_next_netsh_words(("wlan", "show"), parsed))
            self.assertIn("drivers", words)
            self.assertTrue(words["drivers"])

    @unittest.skipUnless(os.name == "nt", "netsh is a Windows command")
    def test_netsh_wlan_show_drivers_completes(self):
        folded = [name.casefold() for name in self.labels("netsh w")]
        self.assertIn("wlan", folded)
        _document, top = self.complete("netsh w")
        wlan = next(item for item in top if item.display_text.casefold() == "wlan")
        self.assertIn("context", wlan.display_meta_text.casefold())
        self.assertTrue(wlan.display_meta_text.isascii())
        folded = [name.casefold() for name in self.labels("netsh wlan show d")]
        self.assertIn("drivers", folded)
        _document, completions = self.complete("netsh wlan show d")
        drivers = next(item for item in completions if item.display_text.casefold() == "drivers")
        applied = apply_completion(_document, drivers)
        self.assertEqual(applied.text, "netsh wlan show drivers")

    def test_slash_switches_match_the_typed_prefix(self):
        parsed = _parse_switches(
            "\n".join(
                [
                    "sfc /SCANNOW",
                    "sfc /VERIFYFILE=c:\\windows\\system32\\kernel32.dll",
                    "sfc /SCANFILE=d:\\windows\\system32\\kernel32.dll",
                    "sfc /VERIFYONLY",
                ]
            )
        )
        names = [name.casefold() for name, _description in parsed]
        self.assertIn("/scannow", names)
        self.assertIn("/verifyonly", names)
        self.assertIn("/verifyfile=", names)
        with mock.patch(
            "completion.command_switches",
            return_value=parsed,
        ):
            labels = [name.casefold() for name in self.labels("sfc /sca")]
        self.assertEqual(labels, ["/scannow", "/scanfile="])
        with mock.patch(
            "completion.command_switches",
            return_value=[
                ("/SCANNOW", "scan"),
                ("/OFFBOOTDIR=", "offline boot directory"),
            ],
        ):
            later = [name.casefold() for name in self.labels("sfc /SCANNOW /off")]
        self.assertEqual(later, ["/offbootdir="])

    def test_switch_completion_skips_app_commands(self):
        with mock.patch("completion.command_switches", side_effect=AssertionError("help")):
            flags = [name.casefold() for name in self.labels("git -")]
            self.assertIn("--version", flags)
            self.assertIn("-c", flags)
            self.assertNotIn("status", flags)
            self.labels("git /")
            names = [name.casefold() for name in self.labels("git ")]
            self.assertIn("commit", names)
            self.assertIn("push", names)
            self.assertEqual(self.labels("docker "), ["ps", "images", "logs"])
            self.assertEqual(self.labels("npm t"), ["test"])

    @unittest.skipUnless(os.name == "nt", "dir is a Windows command")
    def test_dir_slash_switch_from_help(self):
        labels = [name.casefold() for name in self.labels("dir /b")]
        self.assertIn("/b", labels)

    def test_diskpart_list_completes_without_launching_it(self):
        with mock.patch("subprocess.Popen") as popen:
            labels = [name.casefold() for name in self.labels("diskpart list d")]
            popen.assert_not_called()
        self.assertIn("disk", labels)
        nested = CommandCompleter()
        nested.nesting = ("diskpart",)
        document = Document("list v")
        completions = list(
            nested.get_completions(document, CompleteEvent(completion_requested=True))
        )
        self.assertIn("volume", [item.display_text for item in completions])
        self.assertIn("vdisk", [item.display_text for item in completions])

    def test_nested_netsh_prompt_completes_the_inner_line(self):
        nested = CommandCompleter()
        nested.nesting = ("netsh",)
        with mock.patch(
            "completion.netsh_child_commands",
            return_value=[("wlan", "Changes to the `netsh wlan' context.")],
        ):
            completions = list(
                nested.get_completions(
                    Document("wl"),
                    CompleteEvent(completion_requested=True),
                )
            )
        self.assertEqual([item.display_text for item in completions], ["wlan"])
        utf8 = "`netsh wlan' 컨텍스트의 변경 내용입니다.".encode("utf-8")
        self.assertIn("컨텍스트", decode_console_bytes(utf8))
        self.assertNotIn("而", decode_console_bytes(utf8))
        cp949 = "컨텍스트의 변경 내용입니다.".encode("cp949")
        with mock.patch("commands._console_encoding", return_value="cp949"):
            self.assertIn("컨텍스트", decode_console_bytes(cp949))
        self.assertTrue(is_context_switch("Changes to the `netsh wlan' context."))
        self.assertTrue(is_context_switch("`netsh wlan' 컨텍스트의 변경 내용입니다."))
        self.assertFalse(is_context_switch("Displays information."))
        self.assertIn("disk", [name for name, _description in diskpart_child_commands(("list",))])

    def test_empty_input_does_not_load_system_commands(self):
        with mock.patch("commands._system_entries_map", side_effect=AssertionError("loaded")):
            self.assertEqual(
                self.labels(""),
                ["help", "clear", "exit", "git", "docker", "npm", "cd", "open"],
            )

    def test_while_typing_and_explicit_request_match(self):
        typed = self.complete("git s", event=CompleteEvent(text_inserted=True))[1]
        asked = self.complete(
            "git s", event=CompleteEvent(completion_requested=True)
        )[1]
        self.assertEqual(_labels(typed), _labels(asked))
        self.assertIn("status", _labels(typed))
        self.assertIn("stash", _labels(typed))

    def test_suppress_blocks_only_automatic_completion(self):
        completer = CommandCompleter()
        completer.suppress_while_typing = True
        document = Document("git")
        typed = list(
            completer.get_completions(document, CompleteEvent(text_inserted=True))
        )
        asked = list(
            completer.get_completions(
                document, CompleteEvent(completion_requested=True)
            )
        )
        self.assertEqual(typed, [])
        self.assertIn("git", _labels(asked))


class PathTests(CompletionTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        (self.base / "alpha").mkdir()
        (self.base / "alpha" / "leaf.txt").write_text("x", encoding="utf-8")
        (self.base / "notes.txt").write_text("x", encoding="utf-8")
        (self.base / "my documents").mkdir()
        (self.base / "my documents" / "memo.txt").write_text("x", encoding="utf-8")
        (self.base / "한글 폴더").mkdir()
        (self.base / "한글 폴더" / "메모.txt").write_text("가", encoding="utf-8")
        (self.base / "메모.txt").write_text("나", encoding="utf-8")
        (self.base / "sub" / "child").mkdir(parents=True)
        (self.base / "other").mkdir()
        (self.base / "top" / "mid" / "leaf").mkdir(parents=True)
        (self.base / ".secret").mkdir()
        (self.base / "visible").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_git_add_and_directory_option_use_paths(self):
        added = self.labels("git add al", base=self.base)
        self.assertTrue(any(label.startswith("alpha") for label in added))
        switched = self.labels("git -C al", base=self.base)
        self.assertTrue(any(label.startswith("alpha") for label in switched))

    def test_cd_and_open_do_not_query_switches(self):
        with mock.patch("completion.command_switches", side_effect=AssertionError("help")):
            self.labels("cd -", base=self.base)
            self.labels("open -", base=self.base)
            self.assertTrue(any(label.startswith("alpha") for label in self.labels("cd a", base=self.base)))

    def test_directories_only_for_cd_and_files_for_open(self):
        cd_labels = self.labels("cd ", base=self.base)
        self.assertTrue(any(label.startswith("alpha") for label in cd_labels))
        self.assertTrue(any(label.startswith("visible") for label in cd_labels))
        self.assertFalse(any("notes.txt" in label for label in cd_labels))
        self.assertFalse(any(label.startswith(".") for label in cd_labels))

        open_labels = self.labels("open no", base=self.base)
        self.assertIn("notes.txt", open_labels)
        note = self.by_label("open no", "notes.txt", base=self.base)[1]
        self.assertEqual(note.display_meta_text, "file")

        alpha = next(label for label in cd_labels if label.startswith("alpha"))
        _document, completion = self.complete("cd ", base=self.base)
        match = next(item for item in completion if item.display_text == alpha)
        self.assertEqual(match.display_meta_text, "directory")

    def test_relative_path_and_no_recursion(self):
        document, completion = self.by_label("cd alp", f"alpha{os.sep}", base=self.base)
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, f"cd alpha{os.sep}")
        self.assertEqual(completion.start_position, -len("alp"))

        child = self.labels("cd sub/ch", base=self.base)
        self.assertEqual(child, ["sub/child/"])
        parent = self.labels("cd sub/../ot", base=self.base)
        self.assertEqual(parent, ["sub/../other/"])

        top = self.labels("cd ", base=self.base)
        self.assertTrue(any(label.startswith("top") for label in top))
        self.assertFalse(any("mid" in label or "leaf" in label for label in top))

    def test_dotfiles_require_a_dot_prefix(self):
        hidden = self.labels("cd .", base=self.base)
        self.assertTrue(any(label.startswith(".secret") for label in hidden))
        self.assertFalse(any(label.startswith("visible") for label in hidden))

    def test_quoted_path_with_spaces(self):
        label = f"my documents{os.sep}"
        document, completion = self.by_label('cd "my doc', label, base=self.base)
        self.assertEqual(completion.start_position, -len('"my doc'))
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, f'cd "my documents{os.sep}"')

        document, completion = self.by_label("cd 'my doc", label, base=self.base)
        applied = apply_completion(document, completion)
        self.assertEqual(applied.text, f"cd 'my documents{os.sep}'")

        cursor = len('cd "my doc')
        text = 'cd "my documents" --flag'
        document, completion = self.by_label(text, "my documents", cursor=cursor, base=self.base)
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, text)
        self.assertEqual(applied.text.count("my documents"), 1)

    def test_unicode_names_and_cursor_inside_the_path(self):
        document, completion = self.by_label("open 메", "메모.txt", base=self.base)
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "open 메모.txt")

        document, completion = self.by_label(
            'open "한글 폴더/메',
            "한글 폴더/메모.txt",
            base=self.base,
        )
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, 'open "한글 폴더/메모.txt"')

        cursor = len("open 메")
        document, completion = self.by_label(
            "open 메모.txt extra",
            "메모.txt",
            cursor=cursor,
            base=self.base,
        )
        applied = self.assert_tail_preserved(document, completion)
        self.assertEqual(applied.text, "open 메모.txt extra")

    def test_missing_path_and_permission_error_yield_nothing(self):
        self.assertEqual(self.labels("cd missing/nope", base=self.base), [])
        self.assertEqual(self.labels("open 없는파일", base=self.base), [])
        self.assertEqual(self.labels("cd notes.txt/child", base=self.base), [])
        self.assertEqual(self.labels('cd "no such place/x"', base=self.base), [])

        with mock.patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            self.assertEqual(self.labels("cd ", base=self.base), [])
            self.assertEqual(self.labels("open ", base=self.base), [])

    def test_unreadable_entry_is_skipped(self):
        real_is_dir = Path.is_dir

        def is_dir(path, *, follow_symlinks=True):
            if path.name == "visible":
                raise PermissionError("denied")
            return real_is_dir(path, follow_symlinks=follow_symlinks)

        with mock.patch.object(Path, "is_dir", is_dir):
            labels = self.labels("cd ", base=self.base)
        self.assertTrue(any(label.startswith("alpha") for label in labels))
        self.assertFalse(any(label.startswith("visible") for label in labels))


class GitParseTests(CompletionTestCase):
    def test_help_listing_keeps_plumbing_and_skips_internal_helpers(self):
        parsed = parse_git_help_listing(
            "\n".join(
                [
                    "Main Porcelain Commands",
                    "   commit                  Record changes to the repository",
                    "   status                  Show the working tree status",
                    "Low-level Commands / Interrogators",
                    "   cat-file                Provide contents or details of objects",
                    "Low-level Commands / Internal Helpers",
                    "   sh-i18n                 should not appear",
                    "External commands",
                    "   lfs",
                ]
            )
        )
        names = [name for name, _description in parsed]
        self.assertEqual(names, ["commit", "status", "cat-file", "lfs"])
        self.assertEqual(parsed[0][1], "Record changes to the repository")
        self.assertEqual(parsed[-1][1], "git command")

    def test_options_expand_no_forms_and_values(self):
        parsed = parse_git_options(
            "\n".join(
                [
                    "usage: git commit [<options>] [--] [<pathspec>...]",
                    "    -q, --quiet           suppress summary after successful commit",
                    "    -m, --message <message>",
                    "                          commit message",
                    "    --amend               amend previous commit",
                    "",
                    "    later note that is not an option",
                    "    --[no-]verbose        be more verbose",
                ]
            )
        )
        rows = {name: (description, expects) for name, description, expects in parsed}
        self.assertIn("-m", rows)
        self.assertTrue(rows["-m"][1])
        self.assertTrue(rows["--message"][1])
        self.assertIn("commit message", rows["--message"][0])
        self.assertFalse(rows["--amend"][1])
        self.assertNotIn("later note", rows["--amend"][0])
        self.assertIn("--verbose", rows)
        self.assertIn("--no-verbose", rows)
        self.assertNotIn("--[no-]verbose", rows)

    def test_log_help_is_filled_with_history_options(self):
        import git_commands

        text = "usage: git log [<options>]\n    -q, --quiet      suppress diff output\n"
        with (
            mock.patch("git_commands._safe_builtin", return_value="log"),
            mock.patch("git_commands._command_help", return_value=text),
        ):
            names = [name for name, _description in git_commands.git_options("log")]
        self.assertIn("--quiet", names)
        self.assertIn("--oneline", names)
        self.assertIn("--graph", names)
        self.assertIn("--stat", names)

    def test_nested_words_skip_option_groups(self):
        text = "\n".join(
            [
                "usage: git remote [-v | --verbose]",
                "   or: git remote add [-t <branch>] <name> <url>",
                "   or: git remote remove <name>",
                "   or: git submodule [--quiet] add <repository> [<path>]",
                "   or: git submodule [--quiet] status [--cached]",
                "   or: git stash [push [-p | --patch] [<pathspec>...]]",
                "   or: git stash list [<log-options>]",
                "usage: git rebase [-i] [options] [--exec <cmd>] [<upstream>]",
                "   or: git rebase --continue | --abort | --skip | --edit-todo",
                "usage: git sparse-checkout (init | list | set | add | reapply | disable | check-rules) [<options>]",
                "   or: git bisect (good|bad) [<rev>...]",
                "   or: git stash drop [-q | --quiet] [<stash>]",
            ]
        )
        remote = [name for name, _description in parse_git_nested(text, "remote")]
        self.assertEqual(remote, ["add", "remove"])
        submodule = [name for name, _description in parse_git_nested(text, "submodule")]
        self.assertEqual(submodule, ["add", "status"])
        stash = [name for name, _description in parse_git_nested(text, "stash")]
        self.assertEqual(stash, ["push", "list", "drop"])
        self.assertEqual(parse_git_nested(text, "rebase"), [])
        sparse = [name for name, _description in parse_git_nested(text, "sparse-checkout")]
        self.assertEqual(
            sparse,
            ["init", "list", "set", "add", "reapply", "disable", "check-rules"],
        )
        self.assertEqual(
            [name for name, _description in parse_git_nested(text, "bisect")],
            ["good", "bad"],
        )
        usage_options = {name for name, _description, _expects in parse_git_options(text)}
        self.assertIn("--continue", usage_options)
        self.assertIn("--abort", usage_options)
        self.assertIn("--quiet", usage_options)
        self.assertNotIn("options", usage_options)

    def test_alias_lines_and_shell_aliases_are_not_executed(self):
        parsed = parse_git_alias_lines("alias.co checkout\nalias.boom !echo hi\n")
        self.assertEqual(parsed, [("co", "checkout"), ("boom", "!echo hi")])
        with mock.patch("git_commands._aliases", return_value=parsed):
            self.assertEqual(git_resolved_command("co"), "checkout")
            self.assertIsNone(git_resolved_command("boom"))
            self.assertEqual(git_resolved_command("status"), "status")

    def test_fallback_commands_when_git_is_missing(self):
        import git_commands

        previous = git_commands._help_commands
        git_commands._help_commands = None
        try:
            with mock.patch("git_commands.shutil.which", return_value=None):
                names = [name for name, _description in git_commands.git_commands()]
        finally:
            git_commands._help_commands = previous
        self.assertIn("commit", names)
        self.assertIn("push", names)
        self.assertIn("rebase", names)

    def test_installed_git_commands_options_and_refs(self):
        import shutil

        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        names = [name.casefold() for name, _description in git_commands()]
        for required in ("commit", "push", "rebase", "stash", "switch", "status"):
            self.assertIn(required, names)
        stash = [name.casefold() for name in self.labels("git stash p")]
        self.assertIn("pop", stash)
        self.assertIn("push", stash)
        self.assertIn("--quiet", [name.casefold() for name in self.labels("git stash --q")])
        self.assertNotIn("options", [name.casefold() for name in self.labels("git rebase ")])
        self.assertIn("--continue", [name.casefold() for name in self.labels("git rebase --con")])
        sparse = [name.casefold() for name in self.labels("git sparse-checkout ")]
        self.assertIn("init", sparse)
        self.assertIn("set", sparse)
        self.assertIn("good", [name.casefold() for name in self.labels("git bisect g")])
        options = [name.casefold() for name in self.labels("git commit --am")]
        self.assertIn("--amend", options)
        log_options = [name.casefold() for name in self.labels("git log --one")]
        self.assertIn("--oneline", log_options)
        self.assertIn("--graph", [name.casefold() for name in self.labels("git show --gr")])
        remote = [name.casefold() for name in self.labels("git remote ")]
        self.assertIn("add", remote)
        self.assertIn("remove", remote)
        with mock.patch(
            "completion.git_refs",
            return_value=[("main", "branch"), ("feature/login", "branch")],
        ), mock.patch(
            "completion.git_remotes",
            return_value=[("origin", "remote")],
        ):
            self.assertEqual(self.labels("git merge ma"), ["main"])
            self.assertEqual(self.labels("git push ori"), ["origin"])
            self.assertEqual(self.labels("git push origin fe"), ["feature/login"])


class ParseTests(unittest.TestCase):
    def test_quotes_spaces_and_unicode(self):
        self.assertEqual(parse_argv('cd "my documents"'), ["cd", "my documents"])
        self.assertEqual(parse_argv("open '한글 폴더'"), ["open", "한글 폴더"])
        self.assertEqual(parse_argv('cd "unterminated'), ["cd", "unterminated"])
        self.assertEqual(parse_argv(""), [])
        self.assertEqual(parse_argv("   "), [])

    def test_escapes_and_pipes_are_literal(self):
        self.assertEqual(
            parse_argv("cd my\\ documents"),
            ["cd", "my\\", "documents"],
        )
        self.assertEqual(
            parse_argv("git status | more"),
            ["git", "status", "|", "more"],
        )


if __name__ == "__main__":
    unittest.main()
