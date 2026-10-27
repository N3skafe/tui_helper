# tui helper

A terminal prompt that lists candidates and a short description under the input line. Choosing a candidate does not run a command. Enter submits that line in the foreground and shows its stdout, stderr, and exit status.

The program UI and Windows command text are English, including on a Korean Windows install.

Python 3.10 or newer is required. The only install dependency is `prompt_toolkit`. Commands run through the standard-library `subprocess` module.

## Get the project

Copy or download this project folder, then open a terminal in that folder.

## Setup

Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

In this folder, cmd and PowerShell do not start the program the same way. cmd searches the current folder, so `tui` runs `tui.cmd`. PowerShell does not search the current folder, so the same `tui` is not found. Use `.\tui` there.

cmd, in this folder:

```bat
tui
```

PowerShell, in this folder:

```powershell
.\tui
```

To type `tui` from any folder in both cmd and PowerShell, register the command once.

cmd, in this folder:

```bat
install.cmd
```

PowerShell, in this folder:

```powershell
.\install.cmd
```

Close that window and open a new cmd or PowerShell window. Then `tui` works in both. The installer writes `%LOCALAPPDATA%\tui_helper\tui.cmd` and adds that directory to the user PATH. It does not leave a background process running. The prompt still opens in the folder where you type `tui`.

You can also start it without installing the command:

```powershell
.\.venv\Scripts\python main.py
```

Linux:

```bash
.venv/bin/python main.py
```

The prompt is the working directory plus `>`. Example: `W:\work> `. `help` prints the commands and keys.

## Keys

- Typing opens a single column of candidates under the line, with a short description on the right. Nothing is executed at this step.
- `Ctrl+Space` opens the menu.
- `Up` / `Down` select a candidate while the menu is open. The selection is highlighted and previewed in the input line. When the menu is closed, they move through history.
- `Tab` inserts the selection and closes the menu. If the menu is closed, `Tab` only opens it. If nothing is selected yet, it inserts the first candidate.
- `Enter` accepts the selection and closes the menu. It does not submit the line. The next `Enter`, with the menu closed, submits the line.
- `Esc` closes the menu and restores text that arrow keys had previewed.
- When the start of the line matches an earlier command, the rest is shown in gray. `Right` accepts that suggestion.
- `Ctrl+C` while typing cancels the line. `Ctrl+C` while a command is running stops that command and returns to the prompt. The program stays open.
- `Ctrl+D` quits even when the line is not empty.

prompt_toolkit is idle after `prompt()` returns the line. The command then owns the terminal. The prompt opens again when the command finishes.

History is stored in `.tui_helper_history` in the home directory. If that file cannot be created or written, history stays in memory for this run.

## Commands

| Input | Action |
| --- | --- |
| `help` | Show help. Does not start a shell |
| `clear` | Clear the screen. Does not start a shell |
| `exit` | Quit. Does not start a shell |
| `cd` | Print this process's working directory |
| `cd <dir>` | Change this process's working directory. Later commands, the prompt, and path completion keep it |
| Any other submitted line | Run in the foreground shell described below |

`cd` prints a `cd:` reason and keeps the current directory when the path is missing or is not a directory. `cd` accepts one path. A line such as `cd dir && other` is given to the shell as a whole, and the `cd` inside it does not change this process's directory.

A missing command shows the shell error and `[exit N] command not found`. Other failures show `[exit N]`. A command stopped with Ctrl+C shows `[interrupted]`.

On an empty line the menu is only these eight names: `help`, `clear`, `exit`, `git`, `docker`, `npm`, `cd`, `open`.

On Windows, the first typed word also matches:

- Names from `cmd /c help` (`dir`, `copy`, `cls`, and the rest)
- `exe`, `bat`, `cmd`, and `com` files on `PATH` (`netsh`, `ipconfig`, and the rest)
- PowerShell cmdlets, functions, and aliases (`Get-Process`, and the rest)

One name is kept when several sources share it, in this order: this program, cmd, PATH, PowerShell. Linux does not build the cmd or PowerShell lists.

`git` is followed by the commands from `git help -a`, external git commands, and aliases. That includes `commit`, `push`, `rebase`, and `stash`. A shell alias that starts with `!` is offered as a name and is not run to discover more words. `-` starts that command's options from `git <command> -h`. `log` and `show` also include options that short help omits, such as `--oneline` and `--graph`. Branches, tags, remotes, and paths follow when the command uses them.

Typing only `netsh` or `diskpart` and pressing Enter does not hand the terminal to that program. The prompt becomes `netsh>` or `diskpart>`, and the next words continue there, as in `wlan show drivers` or `list disk`. `exit` returns to the original prompt. A context such as `netsh wlan` changes the prompt to `netsh wlan>`. netsh help is read once per context. `diskpart` needs an administrator to read disks. This program does not open a UAC prompt. `list` without that permission prints `administrator permission is required` and leaves the prompt in place. `list disk` works when `python main.py` is started from an elevated terminal.

A token that starts with `/` or `-`, such as `dir /b` or `sfc /sca`, is read from that command's `/?` help. `docker`, `npm`, `cd`, and `open` do not use that list. `sfc` prints its switches only on a real console, so `/SCANNOW`, `/VERIFYONLY`, `/SCANFILE=`, and `/VERIFYFILE=` are stored separately. Completion does not search for similar spellings, and it does not run the command the user is typing. The PowerShell name list is read once, on the first search.

After submit, only `help`, `clear`, `exit`, and `cd` are handled here. Every other line goes to the shell.

`cd` completion lists directories. `open` completion lists files and directories, one level from the current location. A name with a space is quoted in the completed text. An existing `'...'` or `"..."` quote is kept and closed. A `/` or `\` typed by the user is kept; otherwise the operating-system separator is used. Hidden names appear only when the prefix starts with `.`.

## Shell

The submitted line is passed unchanged to a new shell process. The program does not attach to the parent cmd or PowerShell session, and it does not load that session's functions, aliases, doskey macros, AutoRun, or unexported shell variables.

- Windows cmd commands and PATH programs: `%SystemRoot%\System32\cmd.exe /d /c` plus the submitted line. `/d` skips CMD AutoRun.
- Names that exist only in Windows PowerShell: `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command` plus the submitted line. This is not the parent PowerShell session.
- Linux: `/bin/sh -c` plus the submitted line, or `/usr/bin/sh -c` when `/bin/sh` is missing. This is not a bash login shell.

The Python process environment, including `PATH`, is passed to the child. Values changed with `set` or `export` disappear when that process exits. The next submission starts a new shell. Use this program's `cd` when the directory should last. A `cd` inside the shell lasts only for that process.

A program that prints nothing, then exits 0, leaves only `[exit 0]` on screen.

## Limits

Commands run in the foreground.

- Full-screen programs such as editors and pagers can leave the terminal in an awkward state.
- There is no job control. `&`, `bg`, `fg`, and background-job tracking are not handled.
- Shell variables, aliases, and functions do not last from one submission to the next.
- The program does not request elevation, and it does not retry a failed command.
- Completion sees simple tokens only. Backslash escapes, nested quotes, `~`, `$variables`, globs, pipes, `&&`, `;`, and redirection are not used to build candidates. The submitted line itself is interpreted by the shell.
- Path completion looks one level deep. It does not search subdirectories recursively.
- A `cd` path that contains a space is written as `cd "my documents"`.

When the menu is wider than the terminal, the library shortens items with `...`. Korean file names and input are supported. The terminal encoding should be UTF-8. If the terminal or an IME captures `Ctrl+Space`, that key does not reach this program.

## Tests

Windows:

```powershell
.\.venv\Scripts\python -m unittest test_completion.py test_execution.py
```

Linux:

```bash
.venv/bin/python -m unittest test_completion.py test_execution.py
```

`test_completion.py` checks candidates, descriptions, order, and the replacement position. `test_execution.py` checks command classification, results, failures, missing commands, a persistent `cd`, and that accepting a completion does not execute it. Neither opens an interactive terminal. Typing in Windows Terminal or a Linux terminal, resizing the window, and Ctrl+C against a real console are not covered by these tests.

---

# 한국어

입력줄 아래에 후보와 짧은 설명을 보여주는 터미널 프롬프트입니다. 후보를 고르는 동안에는 아무 것도 실행하지 않습니다. Enter로 줄을 제출하면 그 줄만 포그라운드에서 실행하고, stdout, stderr, 종료 상태를 보여 줍니다.

프로그램 화면과 Windows 명령 설명은 한글 Windows에서도 영어입니다.

Python 3.10 이상이 필요합니다. 설치 의존성은 `prompt_toolkit` 하나이고, 명령 실행은 표준 라이브러리 `subprocess`만 사용합니다.

## 프로젝트 받기

이 프로젝트 폴더를 복사하거나 내려받은 뒤, 그 폴더에서 터미널을 엽니다.

## 준비

Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 실행

이 폴더에서 cmd와 PowerShell은 시작 방법이 다릅니다. cmd는 현재 폴더를 검색하므로 `tui`가 `tui.cmd`를 실행합니다. PowerShell은 현재 폴더를 검색하지 않으므로 같은 `tui`를 찾지 못합니다. PowerShell에서는 `.\tui`를 사용합니다.

cmd, 이 폴더에서:

```bat
tui
```

PowerShell, 이 폴더에서:

```powershell
.\tui
```

어느 폴더에서나 cmd와 PowerShell 모두 `tui`로 켜려면 명령을 한 번 등록합니다.

cmd, 이 폴더에서:

```bat
install.cmd
```

PowerShell, 이 폴더에서:

```powershell
.\install.cmd
```

그 창을 닫고 cmd 또는 PowerShell을 새로 엽니다. 그 다음 `tui`는 양쪽에서 동작합니다. 설치는 `%LOCALAPPDATA%\tui_helper\tui.cmd`를 만들고, 그 디렉터리를 사용자 PATH에 넣습니다. 백그라운드 프로세스를 남겨 두지 않습니다. 프롬프트는 `tui`를 친 폴더에서 열립니다.

명령을 등록하지 않고 직접 실행할 수도 있습니다.

```powershell
.\.venv\Scripts\python main.py
```

Linux:

```bash
.venv/bin/python main.py
```

프롬프트는 작업 디렉터리와 `>`입니다. 예: `W:\work> `. `help`는 명령과 키를 출력합니다.

## 조작

- 글자를 입력하면 후보가 입력줄 아래 한 열로 열리고, 오른쪽에 짧은 설명이 붙습니다. 이 단계에서는 명령을 실행하지 않습니다.
- `Ctrl+Space`는 목록을 엽니다.
- `Up` / `Down`은 목록이 열려 있으면 후보를 고릅니다. 고른 항목은 강조되고 입력줄에 미리 들어갑니다. 목록이 닫혀 있으면 이전 입력을 오갑니다.
- `Tab`은 고른 후보를 넣고 목록을 닫습니다. 목록이 닫혀 있으면 목록만 엽니다. 아직 고르지 않았으면 첫 후보를 넣습니다.
- `Enter`는 목록이 열려 있으면 후보를 반영하고 목록만 닫습니다. 줄을 실행하지 않습니다. 목록이 닫힌 뒤의 `Enter`가 줄을 제출합니다.
- `Esc`는 목록을 닫고, 화살표로 미리 넣었던 글자를 원래 입력으로 되돌립니다.
- 예전에 실행한 줄과 앞부분이 같으면 나머지가 흐리게 보입니다. `Right`로 그 제안을 받습니다.
- 입력 중 `Ctrl+C`는 그 줄만 취소합니다. 명령이 실행되는 동안의 `Ctrl+C`는 그 명령을 멈추고 프롬프트로 돌아옵니다. 프로그램은 끝나지 않습니다.
- `Ctrl+D`는 줄에 글자가 있어도 프로그램을 끝냅니다.

prompt_toolkit는 `prompt()`가 줄을 돌려준 뒤 멈춰 있습니다. 그 다음에 명령이 터미널을 사용합니다. 명령이 끝나면 프롬프트가 다시 열립니다.

입력 기록은 홈 디렉터리의 `.tui_helper_history`에 저장합니다. 그 파일을 만들거나 쓸 수 없으면 이번 실행 동안만 메모리에 둡니다.

## 명령

| 입력 | 동작 |
| --- | --- |
| `help` | 도움말. 셸을 부르지 않음 |
| `clear` | 화면 지우기. 셸을 부르지 않음 |
| `exit` | 종료. 셸을 부르지 않음 |
| `cd` | 이 프로세스의 작업 디렉터리를 출력 |
| `cd <dir>` | 이 프로세스의 작업 디렉터리를 바꿈. 다음 명령, 프롬프트, 경로 완성에 유지됨 |
| 그 외 제출된 줄 | 아래 셸이 포그라운드에서 실행 |

`cd`에 경로가 없거나 디렉터리가 아니면 `cd:`로 이유를 출력하고 현재 디렉터리는 유지합니다. `cd`는 경로 하나만 받습니다. `cd dir && other`처럼 셸 연산자가 있으면 그 줄 전체를 셸에 넘기고, 그 안의 `cd`는 프로세스 디렉터리를 바꾸지 않습니다.

없는 명령은 셸 오류와 `[exit N] command not found`로 알립니다. 다른 실패는 `[exit N]`, Ctrl+C로 멈춘 명령은 `[interrupted]`입니다.

빈 줄에서는 `help`, `clear`, `exit`, `git`, `docker`, `npm`, `cd`, `open`만 나옵니다.

Windows에서 첫 단어를 치면 다음도 앞글자로 맞습니다.

- `cmd /c help`의 명령 (`dir`, `copy`, `cls` 등)
- PATH의 `exe`, `bat`, `cmd`, `com` (`netsh`, `ipconfig` 등)
- PowerShell cmdlet, 함수, 별칭 (`Get-Process` 등)

같은 이름은 이 프로그램, cmd, PATH, PowerShell 순으로 하나만 남습니다. Linux에서는 cmd와 PowerShell 목록을 만들지 않습니다.

`git` 뒤에는 `git help -a`의 명령, 외부 명령, 별칭이 나옵니다. `commit`, `push`, `rebase`, `stash`가 포함됩니다. `!`로 시작하는 셸 별칭은 이름만 완성하고 실행하지 않습니다. `-`는 `git <명령> -h`의 옵션입니다. `log`와 `show`는 그 짧은 도움말에 없는 `--oneline`, `--graph`도 넣습니다. 브랜치, 태그, 원격, 경로는 그 명령이 쓸 때 이어집니다.

`netsh`나 `diskpart`만 입력하고 Enter를 누르면 그 프로그램으로 터미널을 넘기지 않습니다. 프롬프트가 `netsh>` 또는 `diskpart>`로 바뀌고, `wlan show drivers`나 `list disk`처럼 다음 단어가 이어집니다. `exit`로 원래 프롬프트에 돌아옵니다. `netsh wlan`처럼 컨텍스트만 바꾸면 프롬프트가 `netsh wlan>`가 됩니다. netsh 도움말은 단계마다 한 번만 읽습니다. `diskpart`는 디스크를 읽으려면 관리자 권한이 필요합니다. 이 프로그램은 UAC 창을 띄우지 않습니다. 권한 없이 `list`를 치면 `administrator permission is required`를 출력하고 프롬프트는 남습니다. 관리자 터미널에서 `python main.py`를 실행하면 `list disk`가 동작합니다.

`dir /b`, `sfc /sca`처럼 `/` 또는 `-`로 시작하는 토큰은 그 명령의 `/?` 도움말에서 읽습니다. `docker`, `npm`, `cd`, `open`은 이 목록을 쓰지 않습니다. `sfc`는 스위치가 실제 콘솔에만 나와서 `/SCANNOW`, `/VERIFYONLY`, `/SCANFILE=`, `/VERIFYFILE=`를 따로 넣었습니다. 비슷한 글자 검색은 하지 않고, 목록을 만드는 동안 사용자가 친 명령은 실행하지 않습니다. PowerShell 이름 목록은 첫 검색 때 한 번만 읽습니다.

제출하면 `help`, `clear`, `exit`, `cd`만 이 프로그램이 처리합니다. 나머지는 셸로 갑니다.

`cd` 완성은 디렉터리만, `open` 완성은 파일과 디렉터리를 현재 위치에서 한 단계만 보여 줍니다. 이름에 공백이 있으면 완성 결과에 따옴표를 붙입니다. 이미 입력한 `'...'` 또는 `"..."`는 유지하고 닫습니다. 사용자가 친 `/` 또는 `\`는 유지하고, 아니면 운영체제 구분자를 씁니다. 숨김 이름은 접두사가 `.`일 때만 나옵니다.

## 셸

제출된 줄은 고치지 않고 새 셸 프로세스에 넘깁니다. 부모 cmd나 PowerShell 세션에 붙지 않고, 그 세션의 함수, 별칭, doskey, AutoRun, 내보내지 않은 셸 변수를 불러오지 않습니다.

- Windows cmd 명령과 PATH 프로그램: `%SystemRoot%\System32\cmd.exe /d /c`와 제출된 줄. `/d`는 CMD AutoRun을 건너뜁니다.
- Windows PowerShell에만 있는 이름: `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command`와 제출된 줄. 부모 PowerShell 세션은 아닙니다.
- Linux: `/bin/sh -c`와 제출된 줄. `/bin/sh`가 없으면 `/usr/bin/sh -c`입니다. bash 로그인 셸이 아닙니다.

Python 프로세스가 가진 환경 변수(`PATH` 등)는 자식에 전달됩니다. 명령 안에서 `set`이나 `export`로 바꾼 값은 그 프로세스가 끝나면 사라집니다. 다음 제출은 새 셸입니다. 디렉터리를 다음 명령까지 유지하려면 이 프로그램의 `cd`를 사용합니다. 셸 안의 `cd`는 그 프로세스 안에서만 적용됩니다.

화면에 아무것도 출력하지 않고 0으로 끝나는 프로그램은 `[exit 0]`만 남깁니다.

## 제한

명령은 포그라운드에서 실행합니다.

- 편집기나 페이저 같은 전체 화면 프로그램은 터미널 상태가 어색하게 남을 수 있습니다.
- 작업 제어는 없습니다. `&`, `bg`, `fg`, 백그라운드 작업 추적을 다루지 않습니다.
- 셸 변수, 별칭, 함수는 제출 사이에 유지되지 않습니다.
- 권한 상승을 요청하지 않고, 실패한 명령을 자동으로 다시 실행하지 않습니다.
- 완성은 단순한 토큰만 봅니다. 백슬래시 이스케이프, 중첩 따옴표, `~`, `$변수`, 글롭, 파이프, `&&`, `;`, 리다이렉션은 후보 계산에 쓰지 않습니다. 제출된 줄 자체는 셸이 해석합니다.
- 경로 완성은 한 단계만 봅니다. 하위 디렉터리를 재귀적으로 찾지 않습니다.
- 공백이 있는 `cd` 경로는 `cd "my documents"`처럼 따옴표로 묶습니다.

메뉴가 터미널 너비보다 넓으면 라이브러리가 항목을 `...`으로 줄입니다. 한국어 파일 이름과 입력은 지원합니다. 터미널 인코딩은 UTF-8이 맞습니다. 터미널이나 IME가 `Ctrl+Space`를 가로채면 그 키는 이 프로그램까지 오지 않습니다.

## 테스트

Windows:

```powershell
.\.venv\Scripts\python -m unittest test_completion.py test_execution.py
```

Linux:

```bash
.venv/bin/python -m unittest test_completion.py test_execution.py
```

`test_completion.py`는 후보, 설명, 순서, 치환 위치를 확인합니다. `test_execution.py`는 명령 분류, 실행 결과, 실패와 없는 명령, `cd` 유지, 완성 수용이 실행되지 않는 것을 확인합니다. 둘 다 대화형 터미널을 열지 않습니다. Windows Terminal이나 Linux 터미널에서 직접 입력하는 동작, 창 크기 변경, 실제 콘솔에서 실행 중 Ctrl+C는 이 테스트로 확인하지 않습니다.
