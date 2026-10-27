"""Command and path completion for the prompt.

Completion text is built so prompt_toolkit can apply it with Document
positions alone: only the prefix before the cursor is replaced, and text
after the cursor stays. When that tail is already the end of the chosen
candidate, it is not inserted again.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from commands import (
    command_switches,
    diskpart_child_commands,
    matching_commands,
    netsh_child_commands,
)
from git_commands import (
    GIT_GLOBAL_OPTIONS,
    GIT_GLOBAL_PATH_EXACT,
    GIT_GLOBAL_PATH_OPTIONS,
    GIT_GLOBAL_VALUE_OPTIONS,
    GIT_PATH_COMMANDS,
    GIT_REF_COMMANDS,
    GIT_REMOTE_COMMANDS,
    git_commands,
    git_nested_commands,
    git_option_expects_value,
    git_options,
    git_refs,
    git_remotes,
    git_resolved_command,
    split_git_words,
)

# Listed order is the menu order. Matching is a case-insensitive prefix.
ROOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("help", "Show help"),
    ("clear", "Clear the screen"),
    ("exit", "Exit"),
    ("git", "Git commands"),
    ("docker", "Docker commands"),
    ("npm", "npm commands"),
    ("cd", "Change directory"),
    ("open", "Open a path"),
)

SUBCOMMANDS: dict[str, tuple[tuple[str, str], ...]] = {
    "docker": (
        ("ps", "List containers"),
        ("images", "List images"),
        ("logs", "Show logs"),
    ),
    "npm": (
        ("install", "Install packages"),
        ("run", "Run a script"),
        ("test", "Run tests"),
    ),
}

# True means directories only.
PATH_COMMANDS: dict[str, bool] = {
    "cd": True,
    "open": False,
}

_QUOTES = ("'", '"')
_SEPARATORS = ("/", "\\")


@dataclass(frozen=True)
class Token:
    """One whitespace-separated word, optionally wrapped in one pair of quotes."""

    start: int
    end: int
    quote: str | None
    closed: bool
    value: str


@dataclass(frozen=True)
class _AtCursor:
    arg_index: int
    command: str | None
    prefix_raw: str
    suffix_raw: str
    value_prefix: str
    value_suffix: str
    quote: str | None


def tokenize(text: str) -> list[Token]:
    """Split a line into tokens.

    Supported: whitespace and one pair of single or double quotes.
    Not supported: backslash escapes, nested quotes, ``$var``, ``~``,
    globs, pipes, ``&&``, ``;``, and redirection. Those characters stay
    literal and do not group words.
    """
    tokens: list[Token] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index].isspace():
            index += 1
            continue
        if text[index] in _QUOTES:
            quote = text[index]
            start = index
            index += 1
            chars: list[str] = []
            while index < length and text[index] != quote:
                chars.append(text[index])
                index += 1
            closed = index < length and text[index] == quote
            if closed:
                index += 1
            tokens.append(Token(start, index, quote, closed, "".join(chars)))
            continue
        start = index
        index += 1
        while index < length and not text[index].isspace():
            index += 1
        tokens.append(Token(start, index, None, False, text[start:index]))
    return tokens


def parse_argv(text: str) -> list[str]:
    """Return unquoted token values. Quotes are not interpreted further."""
    return [token.value for token in tokenize(text)]


def _split_value(token: Token, cursor: int) -> tuple[str, str]:
    content_start = token.start + (1 if token.quote else 0)
    content_end = token.end - (1 if token.closed else 0)
    left = min(max(cursor, content_start), content_end)
    offset = left - content_start
    return token.value[:offset], token.value[offset:]


def _at_cursor(text: str, cursor: int) -> _AtCursor:
    cursor = min(max(cursor, 0), len(text))
    tokens = tokenize(text)
    chosen: tuple[int, Token] | None = None
    for index, token in enumerate(tokens):
        if token.start == cursor or token.start < cursor < token.end:
            chosen = (index, token)
            break
        if cursor == token.end:
            next_start = tokens[index + 1].start if index + 1 < len(tokens) else None
            if next_start != cursor:
                chosen = (index, token)
                break
    if chosen is None:
        arg_index = sum(1 for token in tokens if token.end <= cursor)
        command = tokens[0].value if arg_index > 0 and tokens else None
        return _AtCursor(arg_index, command, "", "", "", "", None)

    index, token = chosen
    command = tokens[0].value if index > 0 else None
    value_prefix, value_suffix = _split_value(token, cursor)
    return _AtCursor(
        arg_index=index,
        command=command,
        prefix_raw=text[token.start : cursor],
        suffix_raw=text[cursor : token.end],
        value_prefix=value_prefix,
        value_suffix=value_suffix,
        quote=token.quote,
    )


def _prefix_match(name: str, prefix: str) -> bool:
    return name.casefold().startswith(prefix.casefold())


def _completion_text(formatted: str, suffix_raw: str) -> str:
    """Drop a token tail that is already sitting after the cursor.

    ``formatted`` is the full token that should replace the current word.
    prompt_toolkit keeps ``suffix_raw`` (the rest of that word). If that
    tail is already the end of ``formatted``, inserting it again would
    duplicate it, so the inserted text stops where the tail begins.
    """
    if suffix_raw and len(formatted) >= len(suffix_raw):
        tail = formatted[-len(suffix_raw) :]
        if tail.casefold() == suffix_raw.casefold():
            return formatted[: -len(suffix_raw)]
    return formatted


def _word_completion(
    name: str,
    description: str,
    prefix_raw: str,
    suffix_raw: str,
) -> Completion:
    return Completion(
        text=_completion_text(name, suffix_raw),
        start_position=-len(prefix_raw),
        display=name,
        display_meta=description,
    )


def _command_completions(
    commands: Sequence[tuple[str, str]],
    ctx: _AtCursor,
) -> Iterable[Completion]:
    for name, description in commands:
        if _prefix_match(name, ctx.value_prefix):
            yield _word_completion(name, description, ctx.prefix_raw, ctx.suffix_raw)


def _separator(directory_part: str) -> str:
    slash = directory_part.rfind("/")
    backslash = directory_part.rfind("\\")
    if slash == -1 and backslash == -1:
        return os.sep
    if backslash > slash:
        return "\\"
    return "/"


def _split_path_prefix(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    if value[-1] in _SEPARATORS:
        return value, ""
    slash = value.rfind("/")
    backslash = value.rfind("\\")
    cut = max(slash, backslash)
    if cut == -1:
        return "", value
    return value[: cut + 1], value[cut + 1 :]


def _path_match(name: str, prefix: str) -> bool:
    if os.name == "nt":
        return os.path.normcase(name).startswith(os.path.normcase(prefix))
    return name.startswith(prefix)


def _quote_for(value: str, existing: str | None) -> str | None:
    if existing in _QUOTES:
        if existing not in value:
            return existing
        other = "'" if existing == '"' else '"'
        if other not in value:
            return other
        return existing
    if any(char.isspace() for char in value):
        if '"' not in value:
            return '"'
        if "'" not in value:
            return "'"
        return '"'
    return None


def _render_token(value: str, existing_quote: str | None) -> str:
    quote = _quote_for(value, existing_quote)
    if quote is None:
        return value
    return f"{quote}{value}{quote}"


def _directory_to_list(directory_part: str, base: Path) -> Path:
    if directory_part == "":
        return base
    candidate = Path(directory_part)
    if candidate.is_absolute():
        return candidate
    return base / directory_part


def _path_completions(
    ctx: _AtCursor,
    base: Path,
    dirs_only: bool,
) -> Iterable[Completion]:
    directory_part, name_prefix = _split_path_prefix(ctx.value_prefix)
    try:
        directory = _directory_to_list(directory_part, base)
        entries = list(directory.iterdir())
    except (OSError, ValueError):
        return

    entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
    for entry in entries:
        name = entry.name
        if not name or name in (".", ".."):
            continue
        if "\n" in name or "\r" in name or "\x00" in name:
            continue
        if name.startswith(".") and not name_prefix.startswith("."):
            continue
        if name_prefix and not _path_match(name, name_prefix):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if dirs_only and not is_dir:
            continue

        completed = f"{directory_part}{name}"
        if is_dir and ctx.value_suffix == "" and not completed.endswith(_SEPARATORS):
            completed += _separator(directory_part)
        formatted = _render_token(completed, ctx.quote)
        yield Completion(
            text=_completion_text(formatted, ctx.suffix_raw),
            start_position=-len(ctx.prefix_raw),
            display=completed,
            display_meta="directory" if is_dir else "file",
        )


class CommandCompleter(Completer):
    """Complete the command tree and one directory level for cd/open.

    ``base_dir`` fixes the directory used for path completion. When it is
    omitted, the process working directory is read on each request, so a
    local ``cd`` is visible to the next prompt. ``suppress_while_typing``
    hides the automatic menu for one cycle after a candidate is accepted.
    Explicit completion (Tab, Ctrl+Space) still runs.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = None if base_dir is None else Path(base_dir)
        self.suppress_while_typing = False
        # Words already entered, such as ("netsh", "wlan"). The buffer holds
        # only the line inside that prompt.
        self.nesting: tuple[str, ...] | None = None
        self._expanding_nest = False

    def _base(self) -> Path:
        if self.base_dir is not None:
            return self.base_dir
        return Path.cwd()

    def _complete_git(self, document: Document, ctx: _AtCursor):
        """Complete git commands, options, refs, remotes, and paths.

        Queries such as ``git help -a`` and ``git commit -h`` discover words.
        They are not the command the user is typing.
        """
        tokens = tokenize(document.text)
        words = [token.value for token in tokens[1 : ctx.arg_index]]
        if words and (
            words[-1] in GIT_GLOBAL_PATH_EXACT or words[-1].casefold() in GIT_GLOBAL_PATH_OPTIONS
        ):
            yield from _path_completions(ctx, self._base(), True)
            return
        if words and words[-1].casefold() in GIT_GLOBAL_VALUE_OPTIONS:
            return
        subcommand, positional = split_git_words(words)
        if subcommand and words and git_option_expects_value(subcommand, words[-1], self._base()):
            return
        if subcommand is None:
            if ctx.value_prefix.startswith("-"):
                yield from _command_completions(GIT_GLOBAL_OPTIONS, ctx)
            else:
                yield from _command_completions(git_commands(self._base()), ctx)
            return
        resolved = git_resolved_command(subcommand, self._base())
        if resolved is None:
            return
        if ctx.value_prefix.startswith("-"):
            yield from _command_completions(git_options(resolved, self._base()), ctx)
            return
        if any(word == "--" for word in words):
            yield from _path_completions(ctx, self._base(), False)
            return
        folded = resolved.casefold()
        if folded == "help" and not positional:
            yield from _command_completions(git_commands(self._base()), ctx)
            return
        nested = git_nested_commands(resolved, self._base())
        if not positional and nested:
            yield from _command_completions(nested, ctx)
            return
        if folded == "remote" and len(positional) == 1:
            names = {name.casefold() for name, _description in nested}
            if positional[0].casefold() in names and positional[0].casefold() != "add":
                yield from _command_completions(git_remotes(self._base()), ctx)
                return
        if folded in GIT_REMOTE_COMMANDS and not positional:
            yield from _command_completions(git_remotes(self._base()), ctx)
            return
        if folded in GIT_REMOTE_COMMANDS and positional:
            yield from _command_completions(git_refs(self._base()), ctx)
            return
        if folded in GIT_REF_COMMANDS:
            yield from _command_completions(git_refs(self._base()), ctx)
        if folded in GIT_PATH_COMMANDS:
            yield from _path_completions(ctx, self._base(), False)

    def get_completions(self, document: Document, complete_event):
        if self.suppress_while_typing and complete_event.text_inserted:
            return
        if self.nesting and not self._expanding_nest:
            document = _document_inside_nest(document, self.nesting)
            self._expanding_nest = True
            try:
                yield from self.get_completions(document, complete_event)
            finally:
                self._expanding_nest = False
            return
        ctx = _at_cursor(document.text, document.cursor_position)
        if ctx.arg_index == 0:
            yield from _command_completions(
                matching_commands(ctx.value_prefix, ROOT_COMMANDS),
                ctx,
            )
            return
        if not ctx.command:
            return
        if ctx.command.casefold() == "netsh" and ctx.arg_index >= 1:
            tokens = tokenize(document.text)
            context = tuple(token.value for token in tokens[1 : ctx.arg_index])
            yield from _command_completions(netsh_child_commands(context), ctx)
            return
        if ctx.command.casefold() == "diskpart" and ctx.arg_index >= 1:
            tokens = tokenize(document.text)
            context = tuple(token.value for token in tokens[1 : ctx.arg_index])
            yield from _command_completions(diskpart_child_commands(context), ctx)
            return
        key = ctx.command.casefold()
        if key == "git" and ctx.arg_index >= 1:
            yield from self._complete_git(document, ctx)
            return
        # docker, npm, cd, and open keep their own second word.
        # A switch lookup would also run "<command> /?" while typing.
        if (
            key not in SUBCOMMANDS
            and key not in PATH_COMMANDS
            and ctx.value_prefix.startswith(("/", "-"))
        ):
            yield from _command_completions(command_switches(ctx.command), ctx)
            return
        if ctx.arg_index != 1:
            return
        subcommands = SUBCOMMANDS.get(key)
        if subcommands is not None:
            yield from _command_completions(subcommands, ctx)
            return
        if key in PATH_COMMANDS:
            yield from _path_completions(ctx, self._base(), PATH_COMMANDS[key])


def _document_inside_nest(document: Document, nesting: tuple[str, ...]) -> Document:
    """Pretend the nest words were typed before the current line."""
    head = " ".join(nesting)
    if document.text:
        text = f"{head} {document.text}"
        cursor = len(head) + 1 + document.cursor_position
    else:
        text = f"{head} "
        cursor = len(text)
    return Document(text, cursor_position=min(max(cursor, 0), len(text)))


def apply_completion(document: Document, completion: Completion) -> Document:
    """Apply ``completion`` the way prompt_toolkit replaces text.

    Characters before the replacement start stay. Characters after the
    cursor stay. The cursor lands just after the inserted text.
    """
    before = document.text_before_cursor
    after = document.text_after_cursor
    cut = len(before) + completion.start_position
    if cut < 0:
        cut = 0
    new_before = before[:cut] + completion.text
    return Document(new_before + after, cursor_position=len(new_before))
