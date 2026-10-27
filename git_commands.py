"""Git words for completion.

The catalog comes from ``git help -a``. Options come from ``git <command> -h``.
Branches, tags, and remotes are read from the repository. None of these runs
the line the user is typing. A shell alias (``!...``) is offered as a name
and is not executed to discover further words.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from commands import decode_console_bytes, prefer_english

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_OPT_RE = re.compile(
    r"(?<![\w-])(--\[no-\][A-Za-z0-9][\w-]*|--[A-Za-z0-9][\w-]*|-[A-Za-z])"
)
_SKIP_SECTIONS = (
    "low-level commands / internal helpers",
    "user-facing repository",
    "developer-facing file formats",
)

# Used when git itself is not installed. A normal install replaces this
# with the full `git help -a` list.
GIT_FALLBACK: tuple[tuple[str, str], ...] = (
    ("add", "Add file contents to the index"),
    ("am", "Apply a series of patches from a mailbox"),
    ("archive", "Create an archive of files from a named tree"),
    ("bisect", "Find the commit that introduced a bug"),
    ("blame", "Show who last modified each line"),
    ("branch", "List, create, or delete branches"),
    ("bundle", "Move objects and refs by archive"),
    ("checkout", "Switch branches or restore working tree files"),
    ("cherry-pick", "Apply changes from existing commits"),
    ("clean", "Remove untracked files from the working tree"),
    ("clone", "Clone a repository into a new directory"),
    ("commit", "Record changes to the repository"),
    ("config", "Get and set repository or global options"),
    ("describe", "Give an object a human readable name"),
    ("diff", "Show changes between commits and the working tree"),
    ("difftool", "Show changes using a diff tool"),
    ("fetch", "Download objects and refs from another repository"),
    ("format-patch", "Prepare patches for e-mail submission"),
    ("grep", "Print lines matching a pattern"),
    ("init", "Create an empty Git repository"),
    ("log", "Show commit logs"),
    ("merge", "Join two or more development histories"),
    ("mergetool", "Run a merge conflict tool"),
    ("mv", "Move or rename a file, directory, or symlink"),
    ("notes", "Add or inspect object notes"),
    ("pull", "Fetch from and integrate with another repository"),
    ("push", "Update remote refs along with associated objects"),
    ("range-diff", "Compare two commit ranges"),
    ("rebase", "Reapply commits on top of another base tip"),
    ("reflog", "Manage reflog information"),
    ("remote", "Manage tracked repositories"),
    ("reset", "Reset current HEAD to the specified state"),
    ("restore", "Restore working tree files"),
    ("revert", "Revert some existing commits"),
    ("rm", "Remove files from the working tree and the index"),
    ("shortlog", "Summarize git log output"),
    ("show", "Show various types of objects"),
    ("sparse-checkout", "Reduce the working tree to a subset of files"),
    ("stash", "Stash changes in a dirty working directory"),
    ("status", "Show the working tree status"),
    ("submodule", "Initialize, update, or inspect submodules"),
    ("switch", "Switch branches"),
    ("tag", "Create, list, delete, or verify a tag"),
    ("worktree", "Manage multiple working trees"),
)

GIT_GLOBAL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("-C", "Run as if git started in this path"),
    ("-c", "Set a configuration variable"),
    ("--exec-path", "Path to git executables"),
    ("--html-path", "Print the HTML documentation path"),
    ("--man-path", "Print the manpath for git"),
    ("--info-path", "Print the info documentation path"),
    ("-p", "Pipe output into a pager"),
    ("--paginate", "Pipe output into a pager"),
    ("-P", "Do not pipe output into a pager"),
    ("--no-pager", "Do not pipe output into a pager"),
    ("--no-replace-objects", "Do not use replacement refs"),
    ("--no-lazy-fetch", "Do not fetch missing objects"),
    ("--no-optional-locks", "Do not perform optional locks"),
    ("--no-advice", "Disable advice messages"),
    ("--bare", "Treat the repository as bare"),
    ("--git-dir", "Set the path to the repository"),
    ("--work-tree", "Set the path to the working tree"),
    ("--namespace", "Set the git namespace"),
    ("--config-env", "Set a config variable from the environment"),
    ("-v", "Print the git version"),
    ("--version", "Print the git version"),
    ("-h", "Show help"),
    ("--help", "Show help"),
)

# -C stays out of the casefolded set so it does not collide with -c.
GIT_GLOBAL_PATH_OPTIONS = frozenset({"--git-dir", "--work-tree", "--exec-path"})
GIT_GLOBAL_PATH_EXACT = frozenset({"-C"})
GIT_GLOBAL_VALUE_OPTIONS = frozenset({"-c", "--namespace", "--config-env"})

GIT_REF_COMMANDS = frozenset(
    {
        "blame",
        "branch",
        "checkout",
        "cherry-pick",
        "describe",
        "diff",
        "format-patch",
        "log",
        "merge",
        "rev-list",
        "shortlog",
        "range-diff",
        "rebase",
        "reflog",
        "reset",
        "revert",
        "show",
        "switch",
        "tag",
        "whatchanged",
    }
)

# `git log -h` only prints a few log-specific options. The history and
# diff options below are accepted by log, show, shortlog, and format-patch.
_REVISION_COMMANDS = frozenset(
    {"format-patch", "log", "rev-list", "shortlog", "show", "whatchanged"}
)
_REVISION_OPTIONS: tuple[tuple[str, str, bool], ...] = (
    ("--oneline", "One line per commit", False),
    ("--graph", "Draw a text graph of commits", False),
    ("--all", "All refs", False),
    ("--decorate", "Show ref names", False),
    ("--stat", "Diffstat", False),
    ("--shortstat", "Short diffstat", False),
    ("--name-only", "Show only names of changed files", False),
    ("--name-status", "Show names and status of changed files", False),
    ("--numstat", "Numeric diffstat", False),
    ("--abbrev-commit", "Show only a partial commit hash", False),
    ("--author", "Filter by author", True),
    ("--committer", "Filter by committer", True),
    ("--since", "Show commits after a date", True),
    ("--until", "Show commits before a date", True),
    ("--after", "Show commits after a date", True),
    ("--before", "Show commits before a date", True),
    ("--grep", "Filter by commit message", True),
    ("--reverse", "Oldest commits first", False),
    ("--first-parent", "Follow only the first parent", False),
    ("--follow", "Follow file history across renames", False),
    ("--patch", "Show the patch", False),
    ("--pretty", "Pretty-print format", True),
    ("--format", "Pretty-print format", True),
    ("--date", "Date format", True),
    ("--max-count", "Limit the number of commits", True),
    ("--skip", "Skip a number of commits", True),
    ("--merges", "Only merge commits", False),
    ("--no-merges", "No merge commits", False),
    ("--branches", "Local branches", False),
    ("--tags", "Tags", False),
    ("--remotes", "Remote-tracking branches", False),
    ("--walk-reflogs", "Walk reflog entries", False),
    ("--show-signature", "Show signatures", False),
    ("--parents", "Show parent hashes", False),
    ("--children", "Show child hashes", False),
    ("--left-right", "Mark which side a commit is on", False),
    ("--boundary", "Show boundary commits", False),
    ("--ancestry-path", "Only commits on the ancestry path", False),
    ("--topo-order", "Topological order", False),
    ("--date-order", "Date order", False),
    ("--full-history", "Do not prune history", False),
    ("--simplify-by-decoration", "Only commits that are decorated", False),
    ("--notes", "Show notes", False),
    ("--no-notes", "Do not show notes", False),
    ("--raw", "Raw diff format", False),
    ("--cc", "Combined diff for merges", False),
    ("--check", "Warn if changes introduce conflict markers", False),
    ("--summary", "Condensed summary", False),
    ("--exit-code", "Exit with 1 if there were differences", False),
    ("--color", "Show colored diff", False),
    ("--no-color", "Turn off colored diff", False),
    ("--find-renames", "Detect renames", False),
    ("--find-copies", "Detect copies", False),
    ("--word-diff", "Show a word diff", False),
    ("--ignore-space-change", "Ignore whitespace amount changes", False),
    ("--ignore-all-space", "Ignore all whitespace", False),
    ("--diff-filter", "Select files by diff status", True),
    ("--relative", "Exclude changes outside a directory", False),
    ("-S", "Search changes for a string", True),
    ("-G", "Search changes for a regex", True),
    ("-U", "Lines of context", True),
    ("--unified", "Lines of context", True),
)
GIT_REMOTE_COMMANDS = frozenset({"fetch", "pull", "push"})
GIT_PATH_COMMANDS = frozenset(
    {
        "add",
        "checkout",
        "clean",
        "commit",
        "diff",
        "grep",
        "mv",
        "reset",
        "restore",
        "rm",
    }
)

_help_commands: list[tuple[str, str]] | None = None
_alias_cache: dict[str, list[tuple[str, str]]] = {}
_help_text_cache: dict[str, str] = {}


def parse_git_help_listing(text: str) -> list[tuple[str, str]]:
    """Read command names and descriptions from ``git help -a``."""
    commands: list[tuple[str, str]] = []
    seen: set[str] = set()
    skipping = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace():
            if skipping:
                continue
            name, _, description = line.strip().partition(" ")
            if not _NAME_RE.fullmatch(name):
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            commands.append((name, " ".join(description.split())[:80] or "git command"))
            continue
        header = line.strip().casefold()
        skipping = any(header.startswith(prefix) for prefix in _SKIP_SECTIONS)
    return commands


def _option_names(raw: str) -> tuple[str, ...]:
    if raw.startswith("--[no-]"):
        base = raw[len("--[no-]") :]
        return (f"--{base}", f"--no-{base}")
    return (raw,)


def parse_git_options(text: str) -> list[tuple[str, str, bool]]:
    """Read ``-h`` option lines. The third item is true when a value follows."""
    lines: list[str] = []
    pending = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("-"):
            lines.append(line)
            pending = True
        elif pending and line[:1].isspace() and stripped:
            lines[-1] = f"{lines[-1]} {stripped}"
        else:
            pending = False
    found: dict[str, tuple[str, str, bool]] = {}
    for line in lines:
        matches = list(_OPT_RE.finditer(line))
        if not matches:
            continue
        expects = "<" in line
        description = _OPT_RE.sub(" ", line)
        description = re.sub(r"\[[^\]]*\]|<[^>]*>", " ", description)
        description = " ".join(description.split()).strip(" ,;:-")[:80] or "option"
        for match in matches:
            for name in _option_names(match.group(1)):
                key = name.casefold()
                if key not in found:
                    found[key] = (name, description, expects)
    for line in _usage_lines(text):
        matches = list(_OPT_RE.finditer(line))
        for index, match in enumerate(matches):
            nxt = matches[index + 1].start() if index + 1 < len(matches) else match.end() + 16
            gap = line[match.end() : nxt]
            expects = bool(re.match(r"\s*(<|=|\[=)", gap))
            for name in _option_names(match.group(1)):
                key = name.casefold()
                if key not in found:
                    found[key] = (name, "option", expects)
    return list(found.values())


def parse_git_nested(text: str, command: str) -> list[tuple[str, str]]:
    """Read the word after ``git <command>`` on usage lines."""
    found: dict[str, tuple[str, str]] = {}
    pattern = re.compile(rf"\bgit\s+{re.escape(command)}\b", re.IGNORECASE)
    for line in text.splitlines():
        match = pattern.search(line)
        if match is None:
            continue
        for word in _command_words_after(line[match.end() :]):
            if word.casefold() == command.casefold():
                continue
            found.setdefault(word.casefold(), (word, f"{command} {word}"))
    return list(found.values())


def parse_git_alias_lines(text: str) -> list[tuple[str, str]]:
    """Read ``alias.name expansion`` lines from ``git config``."""
    aliases: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.casefold().startswith("alias."):
            continue
        key, _, expansion = stripped.partition(" ")
        name = key.split(".", 1)[1]
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        aliases.append((name, " ".join(expansion.split())))
    return aliases


def git_commands(cwd: Path | str | None = None) -> list[tuple[str, str]]:
    """Porcelain, plumbing, external commands, then aliases for this directory."""
    rows = list(_help_commands_cached())
    seen = {name.casefold() for name, _description in rows}
    for name, expansion in _aliases(cwd):
        key = name.casefold()
        if key in seen or not _NAME_RE.fullmatch(name):
            continue
        label = "alias: " + expansion[:60] if expansion else "alias"
        rows.append((name, label))
        seen.add(key)
    return rows


def git_resolved_command(name: str, cwd: Path | str | None = None) -> str | None:
    """Map an alias to its builtin command.

    A shell alias returns None so completion does not execute it.
    """
    if not _NAME_RE.fullmatch(name or ""):
        return None
    for alias, expansion in _aliases(cwd):
        if alias.casefold() != name.casefold():
            continue
        if not expansion or expansion.startswith("!"):
            return None
        first = expansion.split()[0]
        if _NAME_RE.fullmatch(first):
            return first
        return None
    return name


def git_options(command: str, cwd: Path | str | None = None) -> list[tuple[str, str]]:
    safe = _safe_builtin(command, cwd)
    if safe is None:
        return []
    return [(name, description) for name, description, _expects in _options(safe)]


def git_option_expects_value(command: str, token: str, cwd: Path | str | None = None) -> bool:
    safe = _safe_builtin(command, cwd)
    if safe is None:
        return False
    folded = token.casefold()
    for name, _description, expects in _options(safe):
        if name.casefold() == folded:
            return expects
    return False


def git_nested_commands(command: str, cwd: Path | str | None = None) -> list[tuple[str, str]]:
    safe = _safe_builtin(command, cwd)
    if safe is None:
        return []
    return parse_git_nested(_command_help(safe), safe)


def git_refs(cwd: Path | str | None = None) -> list[tuple[str, str]]:
    """Local branches, remote-tracking branches, and tags."""
    text = _run_git(
        "for-each-ref",
        "--format=%(refname:short)%09%(refname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
        cwd=cwd,
    )
    found: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        short, separator, full = line.strip().partition("\t")
        if not separator or not short or short.casefold() in found:
            continue
        if full.startswith("refs/tags/"):
            kind = "tag"
        elif full.startswith("refs/remotes/"):
            kind = "remote-tracking branch"
        else:
            kind = "branch"
        found[short.casefold()] = (short, kind)
    return list(found.values())


def git_remotes(cwd: Path | str | None = None) -> list[tuple[str, str]]:
    text = _run_git("remote", cwd=cwd)
    remotes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        name = line.strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        remotes.append((name, "remote"))
    return remotes


def split_git_words(words: Sequence[str]) -> tuple[str | None, list[str]]:
    """Drop global options and return ``(subcommand, positional words)``."""
    subcommand: str | None = None
    positional: list[str] = []
    skip_value = False
    values = GIT_GLOBAL_PATH_OPTIONS | GIT_GLOBAL_PATH_EXACT | GIT_GLOBAL_VALUE_OPTIONS
    for word in words:
        if skip_value:
            skip_value = False
            continue
        if subcommand is None:
            if word in GIT_GLOBAL_PATH_EXACT or word.casefold() in values:
                skip_value = True
                continue
            if word.startswith("-"):
                continue
            subcommand = word
            continue
        if word.startswith("-"):
            continue
        positional.append(word)
    return subcommand, positional


def _help_commands_cached() -> list[tuple[str, str]]:
    global _help_commands
    if _help_commands is not None:
        return _help_commands
    loaded = parse_git_help_listing(_run_git("help", "-a", cwd=None))
    if not loaded:
        return list(GIT_FALLBACK)
    _help_commands = loaded
    return _help_commands


def _aliases(cwd: Path | str | None) -> list[tuple[str, str]]:
    folder = _existing_dir(cwd)
    if folder in _alias_cache:
        return _alias_cache[folder]
    seen: dict[str, tuple[str, str]] = {}
    for args in (
        ("config", "--global", "--get-regexp", "^alias\\."),
        ("config", "--get-regexp", "^alias\\."),
    ):
        for name, expansion in parse_git_alias_lines(_run_git(*args, cwd=folder)):
            seen.setdefault(name.casefold(), (name, expansion))
    rows = list(seen.values())
    _alias_cache[folder] = rows
    return rows


def _safe_builtin(command: str, cwd: Path | str | None) -> str | None:
    resolved = git_resolved_command(command, cwd)
    if not resolved:
        return None
    known = {name.casefold() for name, _description in _help_commands_cached()}
    if resolved.casefold() not in known:
        return None
    return resolved


def _options(command: str) -> list[tuple[str, str, bool]]:
    rows = parse_git_options(_command_help(command))
    if command.casefold() not in _REVISION_COMMANDS:
        return rows
    seen = {name.casefold() for name, _description, _expects in rows}
    for name, description, expects in _REVISION_OPTIONS:
        key = name.casefold()
        if key not in seen:
            rows.append((name, description, expects))
            seen.add(key)
    return rows


def _command_help(command: str) -> str:
    key = command.casefold()
    if key not in _help_text_cache:
        _help_text_cache[key] = _run_git(command, "-h", cwd=None)
    return _help_text_cache[key]


def _run_git(*args: str, cwd: Path | str | None) -> str:
    executable = shutil.which("git")
    if executable is None:
        return ""
    prefer_english()
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["LANGUAGE"] = "C"
    env["GIT_PAGER"] = "cat"
    try:
        completed = subprocess.run(
            [executable, "--no-pager", "-C", _existing_dir(cwd), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=12,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return decode_console_bytes(completed.stdout) + decode_console_bytes(completed.stderr)


def _existing_dir(cwd: Path | str | None) -> str:
    if cwd is not None:
        candidate = Path(cwd)
        if candidate.is_dir():
            return str(candidate)
    try:
        current = Path.cwd()
    except OSError:
        current = Path(os.environ.get("TEMP", "."))
    if current.is_dir():
        return str(current)
    return str(Path(os.environ.get("TEMP", ".")))


def _is_placeholder(word: str) -> bool:
    folded = word.casefold()
    if folded in {"options", "option", "args", "arg", "arguments", "opts"}:
        return True
    return folded.endswith("-options") or folded.endswith("-option")


def _command_words_after(text: str) -> list[str]:
    """The subcommand word, a parenthesized group, or nothing."""
    rest = text.strip()
    while rest.startswith("["):
        inner = rest[1:].lstrip()
        if inner[:1].isalpha():
            match = re.match(r"[A-Za-z][\w-]*", inner)
            word = match.group(0) if match else ""
            if word and not _is_placeholder(word):
                return [word]
        end = _closing_pair(rest, "[", "]")
        if end is None:
            return []
        rest = rest[end + 1 :].strip()
    if rest.startswith("("):
        end = _closing_pair(rest, "(", ")")
        if end is None:
            return []
        words: list[str] = []
        for part in rest[1:end].split("|"):
            word = _plain_word(part)
            if word:
                words.append(word)
        return words
    word = _plain_word(rest)
    return [word] if word else []


def _plain_word(text: str) -> str | None:
    stripped = text.strip()
    if not stripped or stripped[0] in "-<([|":
        return None
    match = re.match(r"[A-Za-z][\w-]*", stripped)
    if match is None or _is_placeholder(match.group(0)):
        return None
    return match.group(0)


def _usage_lines(text: str) -> list[str]:
    """Usage synopsis lines, including wrapped flag groups."""
    rows: list[str] = []
    active = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("usage:", "or:")):
            active = True
            rows.append(line)
            continue
        if active and line[:1].isspace() and stripped[:1] in "[<(":
            rows.append(line)
            continue
        active = False
    return rows


def _closing_pair(text: str, opener: str, closer: str) -> int | None:
    depth = 0
    for index, char in enumerate(text):
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return None
