# VibeQuota

A small widget that lives **inside the Windows 11 taskbar** and shows how much of your
**Codex** and **Claude Code** subscription limits you have used.

```
[Codex icon]  5H 100%  W 43%
[Claude icon] 5H 94%   W 43%
```

- **5H**: % used of the 5-hour window. **W**: % used of the weekly limit.
  Green below 70%, orange from 70%, red from 90%.
- **Hover**: details for both tools side by side:
  - account, plan, every limit with its reset time, and limit-reset credits with their expiry;
  - messages and lines of code, plus tokens (input, output, cache), tool calls and estimated
    cost for Today / 7 days / 30 days / Year / All time;
  - top models and top tools.
- **Click**: refresh now. **Right-click** (widget or tray icon): refresh interval (1 to 30 min,
  default 1 min), font, size, bold, italic, exit.
- A row is only shown when that tool is signed in on the machine.

## Install

Download `VibeQuota.exe` from [Releases](https://github.com/hiepkhach1005/VibeQuota/releases)
and run it. There is nothing to install.

- It starts with Windows by default. Turn this off with **Start with Windows** in the right-click
  menu. If you move the exe, run it once from the new place and the startup entry follows it.
- Only one copy runs at a time; launching it again does nothing.
- It updates itself. 30 seconds after start and then every 6 hours it checks the latest GitHub
  release. If that release is newer, it downloads `VibeQuota.exe`, swaps it in, and restarts.
  Turn this off with **Auto update** in the right-click menu, or update now with
  **Check for updates**.
- Windows SmartScreen may warn the first time because the exe is not code-signed. Click
  **More info → Run anyway**.

To uninstall: untick **Start with Windows**, choose **Exit**, then delete the exe and
`%APPDATA%\VibeQuota`.

## Where the data comes from

| Data | Codex | Claude Code |
|---|---|---|
| % used, resets, reset credits | `chatgpt.com/backend-api/wham/usage` and `wham/rate-limit-reset-credits` | `api.anthropic.com/api/oauth/usage?cedar_ember=1` |
| Messages, lines of code | `wham/analytics/*`, the same source as the Codex app's *Settings → Analytics* page | Counted from local logs: typed prompts, and the `+` / `-` lines of edit patches (shell edits are not counted) |
| Tokens, tool calls, cost | Local session logs in `~/.codex/sessions` | Local session logs in `~/.claude/projects` |

Cost is an estimate from public per-token prices, including the long-context tier for requests
over 272K tokens. Its pricing table follows the
[Token Watch](https://marketplace.visualstudio.com/items?itemName=alvin0-dinhai.token-watch) VS Code extension.

VibeQuota reads the sign-in that the Codex CLI (`~/.codex/auth.json`) and Claude Code
(`~/.claude/.credentials.json`) already store. It never refreshes or writes those tokens: if one
expires, use the CLI once and the next refresh picks up the new token. Nothing is sent anywhere
except to those two providers' own APIs.

The endpoints are **undocumented** and can change without notice.

Settings and the last fetched numbers are kept in `%APPDATA%\VibeQuota`, so after a restart the
widget shows the previous numbers (marked *as of …*) until fresh ones arrive.

## Build from source

Requires Windows 11 and Python 3.11+.

```powershell
.\build.ps1   # -> dist\VibeQuota.exe
```

Or run it directly with `pythonw vibequota.py` after `pip install -r requirements.txt`.

## Releasing a new version

1. Raise `VERSION` in `vibequota.py`, for example to `"1.0.1"`.
2. Run `.\build.ps1`, then commit and push.
3. Create a GitHub release with the matching tag (`v1.0.1`) and attach `dist\VibeQuota.exe`.
   The asset must be named exactly `VibeQuota.exe`.

Installed copies pick it up within 6 hours, or straight away with **Check for updates**.
The updater only sees releases of a **public** repository, and it ignores drafts and pre-releases.

## How it sits in the taskbar

As [TrafficMonitor](https://github.com/zhongyang219/TrafficMonitor) does on Windows 11, the window
is made a child of `Shell_TrayWnd` with `WS_EX_LAYERED`. It is placed just left of
`TrayNotifyWnd` and re-positioned every 5 seconds.
