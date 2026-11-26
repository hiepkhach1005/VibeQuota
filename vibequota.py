"""VibeQuota: a small widget inside the Windows 11 taskbar that shows Codex and Claude Code usage
(% of the 5-hour window and of the week). Hover for details, click to refresh, right-click for the menu.

    pythonw vibequota.py

Reads ~/.codex/auth.json and ~/.claude/.credentials.json and calls undocumented endpoints,
so it may break without notice. It never refreshes or writes tokens: when a token expires,
use the CLI once and the next poll picks up the new one.
"""

import base64
import ctypes
import json
import os
import random
import re
import shutil
import sys
import subprocess
import threading
import winreg
from ctypes import wintypes
import time
import tkinter as tk
import tkinter.font as tkfont

import pystray
from PIL import Image, ImageTk

import usage_stats
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, so the widget lands on the taskbar

HOME = Path.home()
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
CODEX_HOME = Path(usage_stats.codex_home())  # $CODEX_HOME or ~/.codex
CLAUDE_DIR = Path(usage_stats.claude_dir())  # $CLAUDE_CONFIG_DIR or ~/.claude
# Claude Code keeps .claude.json inside CLAUDE_CONFIG_DIR when that is set, else next to ~/.claude
CLAUDE_JSON = CLAUDE_DIR / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude.json"
SOURCES = {"codex": CODEX_HOME / "auth.json", "claude": CLAUDE_DIR / ".credentials.json"}
LOGS = {"codex": CODEX_HOME / "sessions", "claude": CLAUDE_DIR / "projects"}


CODEX_LOCKED = SOURCES["codex"].with_name("auth.json.vibequota-locked")  # auth.json moved aside while over the limit


def auth_kind(key: str) -> str | None:
    """'plan' (subscription sign-in: limits available), 'apikey' (only local log stats) or None (not used here).
    Like Token Watch: Codex with auth_mode other than "chatgpt", or no sign-in token, counts as an API key.
    While VibeQuota has locked Codex the sign-in lives in CODEX_LOCKED, so that file decides.
    Not signed in and no API key anywhere: None, so the tool is not shown (old logs alone do not count)."""
    src = SOURCES[key]
    if key == "codex" and not src.exists() and CODEX_LOCKED.exists():
        src = CODEX_LOCKED
    has_key = any(os.environ.get(v) for v in (("OPENAI_API_KEY", "CODEX_API_KEY") if key == "codex"
                                                 else ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")))
    try:
        d = json.loads(src.read_text(encoding="utf-8"))
        if key == "codex":
            if d.get("auth_mode") in (None, "chatgpt") and (d.get("tokens") or {}).get("access_token"):
                return "plan"
            has_key = has_key or bool(d.get("OPENAI_API_KEY"))
        elif (d.get("claudeAiOauth") or {}).get("accessToken"):
            return "plan"
    except (OSError, ValueError, AttributeError):
        pass
    if key == "claude" and not has_key:
        has_key = claude_api_key_configured()
    return "apikey" if has_key else None


def claude_api_key_configured() -> bool:
    """Claude Code with an API key: `primaryApiKey` in ~/.claude.json, or an API key / helper in its settings."""
    try:
        if json.loads(CLAUDE_JSON.read_text(encoding="utf-8")).get("primaryApiKey"):
            return True
    except (OSError, ValueError, AttributeError):
        pass
    try:
        s = json.loads((CLAUDE_DIR / "settings.json").read_text(encoding="utf-8"))
        env = s.get("env") or {}
        return bool(s.get("apiKeyHelper") or env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"))
    except (OSError, ValueError, AttributeError):
        return False


def light_theme() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


LIGHT = light_theme()
FG, DIM = ("#000000", "#707070") if LIGHT else ("#ffffff", "#9a9a9a")
APP = "VibeQuota"
VERSION = "1.0.8"  # keep in step with the release tag (1.0.8): the updater compares the two
REPO = "hiepkhach1005/VibeQuota"
UPDATE_EVERY_MS = 6 * 3600 * 1000


def is_newer(tag: str, current: str) -> bool:
    def parts(v):
        return tuple(int(x) for x in v.lstrip("vV").split("-")[0].split(".") if x.isdigit())
    try:
        return parts(tag) > parts(current)
    except ValueError:
        return False


def latest_release() -> tuple[str, str | None]:
    """(tag, download URL of VibeQuota.exe) of the newest published release. Public API, no sign-in."""
    with urllib.request.urlopen(urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "vibequota"}), timeout=20) as r:
        rel = json.load(r)
    url = next((a["browser_download_url"] for a in rel.get("assets") or []
                if a.get("name", "").lower() == "vibequota.exe"), None)
    return rel.get("tag_name", ""), url


def install_update(url: str) -> None:
    """Download the new exe next to ours and swap it in. Windows lets a running exe be renamed, not overwritten."""
    exe = Path(sys.executable)
    new, old = exe.with_suffix(".new.exe"), exe.with_suffix(".old.exe")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "vibequota"}), timeout=120) as r:
        new.write_bytes(r.read())
    data = new.read_bytes()[:2]
    if data != b"MZ" or new.stat().st_size < 1_000_000:  # not a Windows exe: an error page or a cut-off download
        new.unlink(missing_ok=True)
        raise ValueError("downloaded file is not a valid exe")
    old.unlink(missing_ok=True)
    exe.rename(old)
    try:
        new.rename(exe)
    except OSError:
        old.rename(exe)  # put the running version back
        raise


def remove_old_exe() -> None:
    """The previous version, kept after a self-update until it has exited."""
    if getattr(sys, "frozen", False):
        try:
            Path(sys.executable).with_suffix(".old.exe").unlink(missing_ok=True)
        except OSError:
            pass
SETTINGS = HOME / "AppData" / "Roaming" / APP / "settings.json"
OLD_DIR = HOME / "AppData" / "Roaming" / "UsageBar"  # name before VibeQuota: move its settings/cache over once
if OLD_DIR.is_dir() and not SETTINGS.parent.exists():
    try:
        OLD_DIR.rename(SETTINGS.parent)
    except OSError:
        pass
CACHE = SETTINGS.with_name("last_quota.json")  # last good quota per tool, shown at start-up until a fetch works
DEFAULT_FONT = {"family": "Tahoma", "size": 10, "bold": False, "italic": False,
                "refresh": 60,  # seconds; Claude quota is fetched less often, see CLAUDE_EVERY
                "autostart": True, "autoupdate": True, "remaining": False,
                # hover rows / blocks turned off in Settings; by default the server table shows only lines added and tokens
                "hover_hidden": ["server:Messages", "server:Lines removed", "server:Threads", "server:Input",
                                 "server:Output", "server:Cache read"]}
# What the Settings window can hide in the hover, chosen apart for each table: saved as "local:<row>" / "server:<row>"
HOVER_ROWS = ("Messages", "Lines added", "Lines removed", "Sessions", "Requests", "Tool calls", "Input", "Output",
              "Cache read", "Cache write", "Total tokens", "Cost")
HOVER_SERVER_ROWS = ("Messages", "Lines added", "Lines removed", "Threads", "Input", "Output", "Cache read",
                     "Total tokens")  # what Codex Analytics reports
HOVER_BLOCKS = (("local", "This machine (local logs) table"), ("server", "Account (server) table"),
                ("models", "Model (7 days)"), ("tools", "Top tools (7 days)"))
# Spending limit for Codex with an API key (no subscription limits there): over it, Codex is locked out
DEFAULT_LIMIT = {"on": True, "kind": "cost", "period": "today", "value": 3.0, "kill": True}  # $3 a day until changed
LIMIT_PERIODS = [("today", "Today"), ("month", "This month"), ("7d", "Last 7 days"), ("30d", "Last 30 days")]
LIMIT_WARN_STEPS = (80, 85, 90, 95)  # % of the limit that shows a tray notification; 100% blocks
LIMIT_KINDS = [("cost", "Cost (USD)"), ("tokens", "Tokens")]


ALERTS = SETTINGS.with_name("limit_alerts.json")  # warning levels already shown, for the current period


def period_key(period: str) -> str:
    """Today -> 2026-11-26, This month -> 2026-11; rolling windows have no boundary (they re-arm by falling under)."""
    return f"{datetime.now():%Y-%m-%d}" if period == "today" else f"{datetime.now():%Y-%m}" if period == "month" else ""


def load_alerts(key: str) -> list[int]:
    try:
        d = json.loads(ALERTS.read_text(encoding="utf-8"))
        return list(d.get("levels") or []) if d.get("key") == key else []
    except (OSError, ValueError, AttributeError):
        return []


def save_alerts(key: str, levels: list[int]) -> None:
    try:
        ALERTS.parent.mkdir(parents=True, exist_ok=True)
        ALERTS.write_text(json.dumps({"key": key, "levels": levels}), encoding="utf-8")
    except OSError:
        pass


def limit_used(st: dict | None, lim: dict) -> float | None:
    """What counts against the limit in its period: estimated cost, or all tokens (input, output, cache)."""
    r = (st or {}).get(lim["period"])
    if not r:
        return None
    return r["cost_usd"] if lim["kind"] == "cost" else r["total"]


def fmt_limit(v: float, kind: str) -> str:
    return f"${v:,.2f}" if kind == "cost" else human(v)


def codex_locked() -> bool:
    return CODEX_LOCKED.exists()


def lock_codex(kill: bool) -> None:
    """Move ~/.codex/auth.json aside so Codex has no API key, and optionally stop running Codex processes
    (a running Codex keeps the key in memory). Codex signed in again meanwhile: move that file aside too."""
    src = SOURCES["codex"]
    if src.exists():
        dst = CODEX_LOCKED if not CODEX_LOCKED.exists() else \
            src.with_name(f"auth.json.vibequota-locked-{datetime.now():%Y%m%d%H%M%S}")
        os.replace(src, dst)
    if kill:
        subprocess.run(["taskkill", "/F", "/IM", "codex.exe"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)


def unlock_codex() -> None:
    """Put the key back. If Codex has made a new auth.json meanwhile, that one wins and ours is kept as a backup."""
    if not CODEX_LOCKED.exists():
        return
    if SOURCES["codex"].exists():
        os.replace(CODEX_LOCKED, SOURCES["codex"].with_name(f"auth.json.vibequota-backup-{datetime.now():%Y%m%d%H%M%S}"))
    else:
        os.replace(CODEX_LOCKED, SOURCES["codex"])


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(on: bool) -> None:
    """Start with Windows via the HKCU Run key, pointing at wherever this exe is now (it may have been moved)."""
    if not getattr(sys, "frozen", False):  # only the built exe; `python vibequota.py` is for development
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, f'"{sys.executable}"')
            else:
                try:
                    winreg.DeleteValue(k, APP)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
REFRESH_CHOICES = [(60, "1 min"), (120, "2 min"), (300, "5 min"), (600, "10 min"), (900, "15 min"), (1800, "30 min")]
FONT_CHOICES = ["Tahoma", "Segoe UI", "Segoe UI Semibold", "Arial", "Verdana", "Calibri", "Consolas"]
SIZE_CHOICES = [8, 9, 10, 11, 12]


def taskbar_colour() -> str:
    u = ctypes.windll.user32
    r = wintypes.RECT()
    u.GetWindowRect(u.FindWindowW("Shell_TrayWnd", None), ctypes.byref(r))
    dc = u.GetDC(0)
    tray = wintypes.RECT()  # sample just left of the tray icons, where the widget sits
    u.GetWindowRect(u.FindWindowExW(u.FindWindowW("Shell_TrayWnd", None), None, "TrayNotifyWnd", None), ctypes.byref(tray))
    # bottom edge of the taskbar: the widget is vertically centred and shorter, so this is never our own pixel
    c = ctypes.windll.gdi32.GetPixel(dc, (tray.left or r.right - 300) - 2, r.bottom - 1)
    u.ReleaseDC(0, dc)
    if c == 0xFFFFFFFF:
        return "#eef0f3" if LIGHT else "#1c1c1d"
    return f"#{c & 0xFF:02x}{(c >> 8) & 0xFF:02x}{(c >> 16) & 0xFF:02x}"


BG = taskbar_colour()


def taskbar_spot(w: int, h: int) -> tuple[int, int]:
    """Left of the tray icons, vertically centred in the taskbar."""
    u = ctypes.windll.user32
    r, t = wintypes.RECT(), wintypes.RECT()
    bar = u.FindWindowW("Shell_TrayWnd", None)
    u.GetWindowRect(bar, ctypes.byref(r))
    tray = u.FindWindowExW(bar, None, "TrayNotifyWnd", None)
    left = r.right - 300
    if tray and u.GetWindowRect(tray, ctypes.byref(t)):
        left = t.left
    work = wintypes.RECT()
    u.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA: taskbar is below it
    screen_h = u.GetSystemMetrics(1)
    return left - w + 6, work.bottom + (screen_h - work.bottom - h) // 2


def get_json(url: str, headers: dict) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
        return json.load(r)


def fmt_time(ts) -> str:
    """Epoch seconds/ms or ISO string -> local 'dd/mm HH:MM'."""
    if not ts:
        return "-"
    try:
        if isinstance(ts, str):
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%d/%m %H:%M")
        return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts).strftime("%d/%m %H:%M")
    except (ValueError, OSError, TypeError):
        return str(ts)


def claude_cli_version() -> str:
    """Claude only reports usage-limit resets to its own clients, so send its user agent (as Token Watch does)."""
    vers = []
    for d in (HOME / ".vscode" / "extensions").glob("anthropic.claude-code-*"):
        try:
            vers.append(tuple(int(x) for x in d.name.split("-")[2].split(".")))
        except ValueError:
            pass
    return ".".join(map(str, max(vers))) if vers else "2.1.0"


def codex_auth() -> dict:
    return json.loads((CODEX_HOME / "auth.json").read_text(encoding="utf-8"))["tokens"]


def codex_headers(t: dict) -> dict:
    return {"Authorization": f"Bearer {t['access_token']}", "ChatGPT-Account-Id": t.get("account_id", ""),
            "Accept": "application/json", "originator": "codex_cli_rs", "User-Agent": "vibequota"}


def codex_account() -> str:
    try:
        claims = json.loads(base64.urlsafe_b64decode(codex_auth()["id_token"].split(".")[1] + "=="))
        return claims.get("email", "?")
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return "?"


def claude_account() -> str:
    try:
        return (json.loads(CLAUDE_JSON.read_text(encoding="utf-8")).get("oauthAccount") or {}).get(
            "emailAddress", "?")
    except (OSError, ValueError):
        return "?"


# Shown even when the quota call fails: comes from local files only.
HEADER = {"codex": lambda: ("Codex", codex_account()), "claude": lambda: ("Claude Code", claude_account())}


def fetch_codex() -> dict:
    t = codex_auth()
    d = get_json("https://chatgpt.com/backend-api/wham/usage", codex_headers(t))
    rl = d.get("rate_limit") or {}
    p, s = rl.get("primary_window") or {}, rl.get("secondary_window") or {}
    r = {"h5": p.get("used_percent"), "week": s.get("used_percent"), "plan": d.get("plan_type"),
         "limits": [("5H", p.get("used_percent"), fmt_time(p.get("reset_at"))),
                    ("Week", s.get("used_percent"), fmt_time(s.get("reset_at")))],
         "notes": ["LIMIT REACHED"] if rl.get("limit_reached") else [], "resets": [], "analytics": None}
    if (d.get("rate_limit_reset_credits") or {}).get("available_count"):
        try:
            cr = get_json("https://chatgpt.com/backend-api/wham/rate-limit-reset-credits", codex_headers(t))
            avail = [c for c in cr.get("credits") or [] if c and c.get("status") == "available"]
        except (urllib.error.URLError, ValueError):
            avail = []
        # 4th item: the credit id that the [Use] button spends (None: nothing to spend from here)
        r["resets"] = [(c.get("title") or "Reset", 1, fmt_time(c.get("expires_at")), c.get("id"))
                       for c in sorted(avail, key=lambda c: str(c.get("expires_at") or ""))]
        if not r["resets"]:
            r["resets"] = [("Reset", d["rate_limit_reset_credits"]["available_count"], "-", None)]
    r["analytics"] = codex_analytics(t)
    return r


def codex_limits_from_log(r: dict, api_error: str) -> bool:
    """When the usage API fails (offline, expired token, 429): 5H / Week from the rate_limits that Codex wrote
    with its last request on this machine, instead of --% or an error. A window whose reset time has passed
    reads 0%. Not live: use on other machines or the cloud since then is not in it."""
    snap = usage_stats.codex_rate_limits()
    if not snap:
        return False
    ts, rl = snap
    if api_error.startswith("from local log"):  # kept from an earlier refresh: keep the API's reason
        api_error = api_error.split(" · API: ", 1)[-1]
    now = time.time()
    vals = {}
    for key, label in (("primary", "5H"), ("secondary", "Week")):
        w = rl.get(key) or {}
        pct, reset = w.get("used_percent"), w.get("resets_at")
        if pct is not None and reset and reset < now:
            pct = 0.0  # that window has reset since the log was written
        vals[label] = (pct, fmt_time(reset) if reset else "-")
    r["h5"], r["week"] = vals["5H"][0], vals["Week"][0]
    r["limits"] = [(label, pct, reset) for label, (pct, reset) in vals.items() if pct is not None]
    r["plan"] = r.get("plan") or rl.get("plan_type")
    r["error"] = f"from local log at {datetime.fromtimestamp(ts):%d/%m %H:%M} · API: {api_error}"
    return True


def codex_auth_type(p: Path) -> str | None:
    """What a JSON file shaped like Codex's auth.json holds, whatever it is called: 'plan' for a ChatGPT
    sign-in {"tokens": {"access_token", "id_token" or "refresh_token"}}, 'apikey' for {"OPENAI_API_KEY": "sk-..."},
    else None. Big files are not sign-ins."""
    try:
        if p.stat().st_size > 256 * 1024:
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        t = d.get("tokens") or {}
        if isinstance(t, dict) and t.get("access_token") and (t.get("id_token") or t.get("refresh_token")):
            return "plan"
        k = d.get("OPENAI_API_KEY")
        if isinstance(k, str) and k.strip():
            return "apikey"
    except (OSError, ValueError, AttributeError):
        pass
    return None


def is_codex_auth(p: Path) -> bool:
    return codex_auth_type(p) is not None


def api_key_tail(p: Path) -> str:
    """Last 4 characters of the key in an API-key auth file: enough to tell keys apart, never the key."""
    try:
        k = json.loads(p.read_text(encoding="utf-8")).get("OPENAI_API_KEY") or ""
        return re.sub(r"[^\w]", "", k.strip())[-4:]
    except (OSError, ValueError, AttributeError):
        return ""


def extra_codex_files() -> list[Path]:
    """Other saved Codex sign-ins: any *.json directly in ~/.codex (not its sub-folders, so auth_backup is
    left out) whose content looks like auth.json. Only read, never renamed or written."""
    codex = CODEX_HOME
    return [p for p in sorted(codex.glob("*.json"))
            if p.is_file() and p.name.lower() != "auth.json" and is_codex_auth(p)]


def codex_file_label(name: str) -> str:
    """auth_work.json -> work, my-acc.json -> my-acc"""
    stem = Path(name).stem
    return stem.removeprefix("auth_") or stem


def fetch_codex_extra() -> list[dict]:
    """5H / Week for each extra Codex sign-in; one failing file never hides the others."""
    main = codex_account()
    cur = CODEX_HOME / "auth.json"
    main_key = api_key_tail(cur) if codex_auth_type(cur) == "apikey" else None  # same key as auth.json: skip
    out, seen = [], set()
    for f in extra_codex_files():
        r = {"file": f.name, "label": codex_file_label(f.name), "email": "?", "plan": None, "limits": [],
             "error": None, "expired": False}
        if codex_auth_type(f) == "apikey":  # no quota to fetch: listed so Codex can be switched back to the key
            tail = api_key_tail(f)
            if ("apikey", tail) in seen or (main_key and tail == main_key):
                continue
            seen.add(("apikey", tail))
            r.update(apikey=True, email=f"API key …{tail}" if tail else "API key")
            out.append(r)
            continue
        try:
            t = json.loads(f.read_text(encoding="utf-8"))["tokens"]
            try:
                r["email"] = json.loads(base64.urlsafe_b64decode(t["id_token"].split(".")[1] + "==")).get("email", "?")
            except (ValueError, KeyError, IndexError, TypeError):
                pass
            if r["email"] != "?" and (r["email"] == main or r["email"] in seen):  # shown already (auth.json / copy)
                continue
            seen.add(r["email"])
            d = get_json("https://chatgpt.com/backend-api/wham/usage", codex_headers(t))
            rl = d.get("rate_limit") or {}
            p, s = rl.get("primary_window") or {}, rl.get("secondary_window") or {}
            r["plan"] = d.get("plan_type")
            r["limits"] = [(n, w.get("used_percent"), fmt_time(w.get("reset_at")))
                           for n, w in (("5H", p), ("Week", s)) if w.get("used_percent") is not None]
            n = (d.get("rate_limit_reset_credits") or {}).get("available_count") or 0
            r["resets"] = []
            if n:  # same as the main Codex column: list each available reset credit
                try:
                    cr = get_json("https://chatgpt.com/backend-api/wham/rate-limit-reset-credits", codex_headers(t))
                    avail = [c for c in cr.get("credits") or [] if c and c.get("status") == "available"]
                except (urllib.error.URLError, ValueError):
                    avail = []
                r["resets"] = [(c.get("title") or "Reset", 1, fmt_time(c.get("expires_at")))
                               for c in sorted(avail, key=lambda c: str(c.get("expires_at") or ""))] \
                    or [("Reset", n, "-")]
        except urllib.error.HTTPError as e:
            r["expired"] = e.code == 401  # can still be switched to: Codex renews the token or asks to sign in
            r["error"] = {401: "token expired (sign in again)", 429: "rate limited"}.get(e.code, f"HTTP {e.code}")
        except Exception as e:
            r["error"] = f"{type(e).__name__}: {e}"
        out.append(r)
    return out


def switch_codex_account(name: str) -> str:
    """auth.json -> auth_<current user>.json, then <name> (any sign-in file found in ~/.codex) -> auth.json.
    Both backed up first; never overwrites a file. Returns the file name the old account was saved as."""
    codex = CODEX_HOME
    cur, new = codex / "auth.json", codex / name
    if new not in extra_codex_files():  # only a file we listed: no paths from elsewhere, nothing that is not a sign-in
        raise FileNotFoundError(name)
    if cur.exists() and codex_auth_type(cur) == "apikey":  # an API key: name it so it is clearly the key
        base = "apikey" + (f"_{api_key_tail(cur)}" if api_key_tail(cur) else "")
    else:
        base = re.sub(r"[^\w.-]", "_", codex_account().split("@")[0]) if codex_account() != "?" else "old"
    old = codex / f"auth_{base}.json"
    i = 2
    while old.exists():
        old = codex / f"auth_{base}_{i}.json"
        i += 1
    bak = codex / "auth_backup" / f"{datetime.now():%Y%m%d_%H%M%S}"
    bak.mkdir(parents=True, exist_ok=True)
    if cur.exists():
        shutil.copy2(cur, bak / cur.name)
    shutil.copy2(new, bak / new.name)
    if cur.exists():
        cur.rename(old)
    try:
        new.rename(cur)
    except OSError:
        if old.exists() and not cur.exists():
            old.rename(cur)  # put the old sign-in back
        raise
    _analytics_cache.update(at=0.0, rows=None)  # analytics belong to the previous account
    return old.name


def codex_extra_segments(accts: list[dict], updated: str | None) -> list[tuple[str, str]]:
    out = []

    def line(*parts):
        out.extend(parts)
        out.append(("\n", ""))

    line(("Codex accounts", "title"))
    line(("", ""))
    for a in accts:
        line((a.get("label") or codex_file_label(a["file"]), "head"), (f"  {a['email']}", "dim"), (f"  {a['plan']}" if a.get("plan") else "", "dim"),
             *([("  ", ""), ("[Switch]", "switch:" + a["file"])]
               if a.get("apikey") or a.get("expired") or (a.get("limits") and not a.get("error")) else []))
        if a.get("apikey"):
            line(("  No usage limits (API key)", "dim"))
        if a.get("error"):
            line(("  ! " + a["error"], "err"))
        if a.get("limits"):
            line((f"  {'Limit':<6}{'Used':>6}{'Left':>7}   {'Resets at':<12}", "dim"))
            for label, pct, reset in a["limits"]:
                line((f"  {label:<6}", ""), (f"{pct:>5.0f}%", "pct_" + level(pct)),
                     (f"{100 - pct:>6.0f}%", "pct_" + level(pct)), (f"   {reset:<12}", ""))
        if a.get("limits") or a.get("resets"):
            resets = a.get("resets") or []
            if resets:
                line((f"  {'Limit resets':<26}{'Left':>5}   {'Expires':<12}", "dim"))
                for title, count, exp in resets:
                    line((f"  {title[:26]:<26}{count:>5}   {exp:<12}", ""))
            elif not a.get("error"):
                line(("  Limit resets: none", "dim"))
        line(("", ""))
    if updated:
        line((f"Updated {updated}", "dim"))
    return out


_analytics_cache = {"at": 0.0, "rows": None}


def codex_analytics(t: dict):
    """Messages, lines of code, threads and tokens of the whole account (every machine and cloud), as on the Codex app's Settings > Analytics page. Daily data, so cached 10 min."""
    if time.time() - _analytics_cache["at"] < 600:
        return _analytics_cache["rows"]
    today = datetime.now().date()
    base = "https://chatgpt.com/backend-api/wham/analytics/"
    turns, loc = {}, {}
    try:
        end = today
        for _ in range(10):  # the API allows at most 365 days per call: walk back until a range is empty
            q = f"start_date={end - timedelta(days=364)}&end_date={end}"
            got_t = get_json(base + "daily-workspace-usage-counts?" + q, codex_headers(t)).get("data") or []
            got_l = get_json(base + "code-attribution?" + q, codex_headers(t)).get("data") or []
            turns.update({r["date"]: r.get("totals") or {} for r in got_t})
            loc.update({r["date"]: ((r.get("code_attribution") or {}).get("lines_of_code") or {}) for r in got_l})
            if not got_t and not got_l:
                break
            end -= timedelta(days=365)
    except (urllib.error.URLError, ValueError, KeyError):
        return _analytics_cache["rows"]

    def series(src, value):  # dates are YYYY-MM-DD strings, so string comparison works
        firsts = [str(today), str(today - timedelta(days=6)), str(today - timedelta(days=29)),
                  str(today.replace(month=1, day=1)), ""]
        return [sum(value(v) for day, v in src.items() if day >= f) for f in firsts]

    def total(field):
        return series(turns, lambda v: v.get(field) or 0)

    rows = [("Messages", total("turns")),
            ("Lines added", series(loc, lambda v: v.get("added") or 0)),
            ("Lines removed", series(loc, lambda v: v.get("removed") or 0)),
            ("Threads", total("threads")),  # summed per day: a thread used on two days counts twice
            ("Input", total("uncached_text_input_tokens")), ("Output", total("text_output_tokens")),
            ("Cache read", total("cached_text_input_tokens")), ("Total tokens", total("text_total_tokens"))]
    _analytics_cache.update(at=time.time(), rows=rows)
    return rows


CLAUDE_LABELS = {"five_hour": "5H", "seven_day": "Week", "seven_day_opus": "Week Opus",
                 "seven_day_sonnet": "Week Sonnet"}


def post_json(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace").strip()
        raise RuntimeError(f"HTTP {e.code}{': ' + detail if detail else ''}") from None
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {}


def use_limit_reset(key: str, reset_id: str) -> None:
    """Spend one usage-limit reset, the same calls Token Watch makes. Raises with a short reason on failure."""
    if key == "codex":
        t = codex_auth()
        post_json("https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume", codex_headers(t),
                  {"credit_id": reset_id, "redeem_request_id": str(uuid.uuid4())})
        return
    o = json.loads((CLAUDE_DIR / ".credentials.json").read_text(encoding="utf-8"))["claudeAiOauth"]
    h = {"Authorization": f"Bearer {o['accessToken']}", "Accept": "application/json",
         "anthropic-beta": "oauth-2025-04-20", "User-Agent": f"claude-cli/{claude_cli_version()} (external, cli)",
         "x-app": "cli"}
    org = ((get_json("https://api.anthropic.com/api/oauth/profile", h).get("organization") or {}).get("uuid") or "")
    if not org.strip():
        raise RuntimeError("the Claude account is not in an organization")
    res = post_json(f"https://api.anthropic.com/api/organizations/{urllib.parse.quote(org.strip())}/reset_rate_limits",
                    h, {"program": "cedar_ember", "grant_id": reset_id, "request_id": str(uuid.uuid4())})
    if res.get("result") != "reset":
        raise RuntimeError(f"Claude did not apply it: {res.get('result', 'unreadable answer, it may have been applied')}"
                           + (f" ({res['reason']})" if res.get("reason") else ""))


def fetch_claude() -> dict:
    o = json.loads((CLAUDE_DIR / ".credentials.json").read_text(encoding="utf-8"))["claudeAiOauth"]
    # Same request as Token Watch: cedar_ember=1 asks for the usage-limit resets, x-app/user-agent mark a CLI client
    d = get_json("https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1", {
        "Authorization": f"Bearer {o['accessToken']}", "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json", "User-Agent": f"claude-cli/{claude_cli_version()} (external, cli)",
        "x-app": "cli"})
    r = {"h5": (d.get("five_hour") or {}).get("utilization"), "week": (d.get("seven_day") or {}).get("utilization"),
         "plan": o.get("subscriptionType"), "notes": [], "resets": [], "analytics": None,
         "limits": [(CLAUDE_LABELS.get(k, k), v["utilization"], fmt_time(v.get("resets_at")))
                    for k, v in d.items() if isinstance(v, dict) and v.get("utilization") is not None]}
    if d.get("limits"):  # the array describes the per-model limits itself; keep only 5H/Week from the old keys
        r["limits"] = [x for x in r["limits"] if x[0] in ("5H", "Week")]
    seen = {label for label, _, _ in r["limits"]}
    for i, lim in enumerate(d.get("limits") or []):  # per-model / per-surface limits, labelled as Token Watch does
        if not isinstance(lim, dict) or lim.get("percent") is None:
            continue
        kind, group = (lim.get("kind") or "").strip(), (lim.get("group") or "").strip()
        scope = lim.get("scope") or {}
        model = ((scope.get("model") or {}).get("display_name") or (scope.get("model") or {}).get("id") or "").strip()
        surface = (scope.get("surface") or "").strip()
        if kind == "session":
            label = "5H"
        elif kind == "weekly_all":
            label = "Week"
        elif group == "weekly" and (model or surface):
            label = f"{model or surface.title()}{f' ({surface})' if model and surface else ''} · Week"
        else:
            label = kind or f"Limit {i + 1}"
        if label not in seen:
            seen.add(label)
            r["limits"].append((label, lim["percent"], fmt_time(lim.get("resets_at"))))
    extra = d.get("extra_usage") or {}
    if extra.get("is_enabled"):
        r["notes"].append(f"Extra usage: {extra.get('used_credits')} / {extra.get('monthly_limit')}")
    ce = d.get("cedar_ember") or {}  # usage-limit resets
    grants = [g for g in ce.get("grants") or [] if g and not g.get("paused") and (g.get("resets_left") or 0) > 0]
    # 4th item: the grant id for [Use]; None when Claude says it cannot be used right now (as Token Watch reads it)
    r["resets"] = [(g.get("label") or "Reset", g["resets_left"], fmt_time(g.get("ends_at")),
                    g.get("id") if g.get("usable_now") is True else None)
                   for g in sorted(grants, key=lambda g: str(g.get("ends_at") or ""))]
    if not grants and ce.get("eligible") is False:
        r["notes"].append(f"Limit resets: not eligible ({ce.get('ineligible_reason', '?')})")
    elif not grants:  # Claude sends cedar_ember = null when the account has no reset to offer
        r["notes"].append("Limit resets: none available")
    return r


BACKOFF: dict = {}  # fn -> (skip-until timestamp, consecutive 429 count)
BACKOFF_STEPS = (180, 360, 720, 900)  # seconds, per consecutive 429 (same as Token Watch)
CLAUDE_EVERY = (180, 210)  # seconds, random in between: Claude rate-limits faster polling (same as Token Watch)
NEXT_FETCH: dict = {}  # fn -> earliest automatic fetch, set after a success


def retry_after(value: str | None) -> float:
    """Seconds from a Retry-After header (number or HTTP date); 0 when missing, invalid or not in the future."""
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        pass
    try:
        return max(0, parsedate_to_datetime(value).timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 0


def safe(fn, prev: dict | None, force: bool = False) -> dict:
    if prev and not force and time.time() < NEXT_FETCH.get(fn, 0):  # not due yet: keep the last numbers
        return prev
    until, n = BACKOFF.get(fn, (0, 0))
    if time.time() < until and prev:  # still cooling down after a 429: keep showing the last numbers
        return prev
    try:
        r = fn()
        r["updated"], r["error"] = f"{datetime.now():%d/%m %H:%M:%S}", None
        BACKOFF.pop(fn, None)
        if fn is fetch_claude:
            NEXT_FETCH[fn] = time.time() + random.uniform(*CLAUDE_EVERY)
        return r
    except urllib.error.HTTPError as e:
        if e.code == 429:  # Retry-After is often 0, then back off ourselves: 3, 6, 12, then 15 min max
            wait = retry_after(e.headers.get("Retry-After")) or BACKOFF_STEPS[min(n, len(BACKOFF_STEPS) - 1)]
            BACKOFF[fn] = (time.time() + wait, n + 1)
            msg = f"rate limited, retry after {datetime.fromtimestamp(time.time() + wait):%H:%M}"
        else:
            msg = {401: "token expired: use the CLI once"}.get(e.code, f"HTTP {e.code}")
    except FileNotFoundError:  # no auth / credentials file: signed out (or moved aside by a block)
        msg = "not signed in"
    except urllib.error.URLError as e:
        msg = f"offline ({getattr(e, 'reason', e)})"
    except Exception as e:  # network, bad JSON
        msg = f"{type(e).__name__}: {e}"
    r = dict(prev or {"h5": None, "week": None, "plan": None, "limits": [], "notes": [], "resets": [],
                      "analytics": None, "updated": None})
    r["error"] = msg
    return r


def human(v) -> str:
    v = float(v or 0)
    if abs(v) < 100_000:  # up to 5 digits: exact, e.g. 51,988 (still fits the 10-char columns)
        return f"{v:,.0f}"
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.1f}{unit}"
    return f"{v:,.0f}"


def popup_segments(key: str, d: dict | None, st: dict | None, ui: dict | None = None) -> list[tuple[str, str]]:
    """The hover text for one tool as (text, tag) pieces; tags style it in a tk.Text widget.
    ui: the limit-reset state of the hover (which [Use] awaits confirmation, a running request, its result)."""
    ui = ui or {}
    out = []

    def line(*parts):
        out.extend(parts)
        out.append(("\n", ""))

    name, account = HEADER[key]()
    plan = (d or {}).get("plan")
    line((name, "title"), (f"  {plan}" if plan else "", "dim"))
    if account != "?":
        line((account, "dim"))
    if d and d.get("apikey"):
        line(("Usage limits not available (API key)", "dim"))
    g = (d or {}).get("guard")
    if g:
        period = dict(LIMIT_PERIODS)[g["period"]]
        if g["used"] is not None:
            pct = 100 * g["used"] / g["limit"]
            line((f"Spending limit ({period.lower()}): ", "dim"),
                 (f"{fmt_limit(g['used'], g['kind'])} / {fmt_limit(g['limit'], g['kind'])}", "pct_" + level(pct)),
                 (f"  {pct:.0f}%", "pct_" + level(pct)))
        if g["blocked"]:
            line(("BLOCKED", "err"))
        if g.get("error"):
            line(("! " + g["error"], "err"))
    if d and d.get("error") and not d["error"].startswith("rate limited"):  # a 429 just retries later: not worth a line
        # start-up saved numbers, or 5H / Week read from the local log while the API fails: notes, not errors
        cached = d["error"].startswith(("cached from", "from local log"))
        text = ("" if cached else "! ") + d["error"]
        width = 56  # the hover column fits its tables: a long error is wrapped, never widening it
        while text:
            line((text[:width], "dim" if cached else "err"))
            text = ("  " + text[width:]) if len(text) > width else ""
    line(("", ""))

    if d and d.get("limits"):
        line((f"{'Limit':<12}{'Used':>6}{'Left':>7}   {'Resets at':<12}", "head"))
        for label, pct, reset in d["limits"]:
            line((f"{label:<12}", ""), (f"{pct:>5.0f}%", "pct_" + level(pct)),
                 (f"{100 - pct:>6.0f}%", "pct_" + level(pct)), (f"   {reset:<12}", ""))
        for note in d.get("notes") or []:
            line((note, "err" if "REACHED" in note else "dim"))
        line(("", ""))
    if d and d.get("resets"):
        line((f"{'Limit resets':<38}{'Left':>5}   {'Expires':<12}", "head"))
        busy = ui.get("busy") == key
        for title, count, exp, *rest in d["resets"]:  # 3 items in an old cache: no id, no button
            rid = rest[0] if rest else None
            btn = [("  ", ""), ("[Use]", f"use:{key}:{rid}")] if rid and not busy and not d.get("apikey") else []
            line((f"{title[:38]:<38}{count:>5}   {exp:<12}", ""), *btn)
            if rid and ui.get("confirm") == (key, rid):  # confirmation right under the row, inside the hover
                line(("  Use this reset now? It cannot be undone.  ", "warn"),
                     ("[Yes]", f"yes:{key}:{rid}"), ("  ", ""), ("[No]", "no:"))
        if busy:
            line(("  Using the reset…", "dim"))
        msg = (ui.get("msg") or {}).get(key)
        if msg:
            text, tag = msg
            while text:
                line(("  " + text[:54], tag))
                text = text[54:]
        line(("", ""))

    cols = ("Today", "7 days", "30 days", "Year", "All time")
    periods = ("today", "7d", "30d", "year", "all")
    # Two separate areas, never mixed: the account as the server reports it (Codex Analytics: every machine and
    # the cloud, but daily data that lags, so today can read 0), and this machine's logs (live, every refresh).
    server = [(label, [human(v) for v in vals]) for label, vals in (d or {}).get("analytics") or []]
    local = []
    if st:
        for label, k, fmt in (("Messages", "messages", human), ("Lines added", "lines_added", human),
                              ("Lines removed", "lines_removed", human), ("Sessions", "sessions", human),
                              ("Requests", "requests", human), ("Tool calls", "tool_calls", human),
                              ("Input", "input", human), ("Output", "output", human),
                              ("Cache read", "cache_read", human), ("Cache write", "cache_write", human),
                              ("Total tokens", "total", human), ("Cost", "cost_usd", lambda v: f"${v:,.2f}")):
            local.append((label, [fmt(st[p][k]) for p in periods]))
    hidden = set(ui.get("hidden") or ())  # rows / blocks turned off in Settings
    local = [(l, v) for l, v in local if "local:" + l not in hidden]
    server = [(l, v) for l, v in server if "server:" + l not in hidden]
    header = f"{'':<14}" + "".join(f"{c:>10}" for c in cols)
    if "local" not in hidden:
        line(("This machine (local logs, live)", "head"))
        if st and d and d.get("stats_note"):
            line((d["stats_note"], "dim"))
        if not st:
            line(("Reading local session logs for tokens / cost…", "dim"))
            line(("(first start can take ~30 s)", "dim"))
        elif local:
            line((header, "head"))
            for label, vals in local:
                line((f"{label:<14}", "dim" if label != "Cost" else ""),
                     ("".join(f"{v:>10}" for v in vals), "cost" if label == "Cost" else ""))
            if st["all"].get("unpriced") and "local:Cost" not in hidden:
                line((f"Cost leaves out {human(st['all']['unpriced'])} requests to models with no known price", "dim"))
        line(("", ""))
    if server and "server" not in hidden:  # below the live local table: the account totals lag
        line(("Account (server: all machines, updated with a delay)", "head"))
        line((header, "head"))
        for label, vals in server:
            line((f"{label:<14}", "dim"), ("".join(f"{v:>10}" for v in vals), ""))
        line(("", ""))
    if "models" in hidden and st:
        st = {**st, "7d": {**st["7d"], "top_models": []}}
    if "tools" in hidden and st:
        st = {**st, "7d": {**st["7d"], "top_tools": []}}
    if st and st["7d"]["top_models"]:
        line((f"{'Model (7 days)':<22}{'Tokens':>9}{'Cost':>10}", "head"))
        for m in st["7d"]["top_models"]:
            line((f"{m['name'][:22]:<22}{human(m['tokens']):>9}{'$' + format(m['cost'], ',.2f'):>10}", ""))
        line(("", ""))
    if st and st["7d"]["top_tools"]:
        line(("Top tools (7 days)", "head"))
        line(("  ".join(f"{n} {human(c)}" for n, c in st["7d"]["top_tools"]), "dim"))
    if d and d.get("updated"):
        line(("", ""))
        line((f"Updated {d['updated']}", "dim"))
    return out


def level(pct) -> str:
    return "hi" if pct >= 90 else "mid" if pct >= 70 else "ok"


def color(pct) -> str:
    if pct is None:
        return DIM
    if LIGHT:
        return "#c42b1c" if pct >= 90 else "#b35c00" if pct >= 70 else "#0f7b0f"
    return "#ff5c5c" if pct >= 90 else "#ffb02e" if pct >= 70 else "#4cd964"


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        # only show tools that are used on this machine (signed in, or API key with local logs)
        self.sources = [k for k in SOURCES if auth_kind(k)]
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        self.data = {}
        for k in self.sources:
            c = cached.get(k)
            if c:
                c["limits"] = [tuple(x) for x in c.get("limits") or []]
                c["resets"] = [tuple(x) for x in c.get("resets") or []]
                c["error"] = f"cached from {c.get('updated', '?')}"
                c["apikey"] = auth_kind(k) == "apikey"  # the sign-in may have changed since the cache was written
                if c.get("stats"):
                    c["stats_note"] = f"as of {c.get('stats_at', '?')} · updating…"
            self.data[k] = c
        if extra_codex_files() and cached.get("codex_extra"):
            self.data["codex_extra"] = cached["codex_extra"]
        self.rows = {}
        try:
            cfg = {**DEFAULT_FONT, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            cfg = dict(DEFAULT_FONT)
        self.font = tkfont.Font(self.root, family=cfg["family"], size=cfg["size"],
                                weight="bold" if cfg["bold"] else "normal",
                                slant="italic" if cfg["italic"] else "roman")
        self.icon_src = {"codex": Image.open(ASSETS / f"icon_codex_{'light' if LIGHT else 'dark'}.png"),
                         "claude": Image.open(ASSETS / "icon_claude.png")}
        self.icons = {}
        self.dpi = 0
        self.icon_labels = {}
        self.build_rows()
        self.tip = None
        self.reset_ui = {"confirm": None, "busy": None, "msg": {}}  # [Use] limit reset state shown in the hover
        self.root.bind("<Enter>", lambda e: self.hover_enter())  # the gaps between the labels count as the widget
        self.root.bind("<Leave>", lambda e: self.hover_leave())
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Refresh", command=self.menu_refresh)
        self.v_family = tk.StringVar(value=cfg["family"])
        self.v_size = tk.IntVar(value=cfg["size"])
        self.v_bold = tk.BooleanVar(value=cfg["bold"])
        self.v_italic = tk.BooleanVar(value=cfg["italic"])
        self.v_remaining = tk.BooleanVar(value=cfg["remaining"])
        self.v_refresh = tk.IntVar(value=cfg["refresh"])
        self.v_autostart = tk.BooleanVar(value=cfg["autostart"])
        set_autostart(cfg["autostart"])
        self.v_autoupdate = tk.BooleanVar(value=cfg["autoupdate"])
        self.hidden = [h for h in cfg.get("hover_hidden") or [] if isinstance(h, str)]
        saved = cfg.get("codex_limit") or {}
        # only a limit the user saved in the dialog counts; otherwise the current defaults apply
        self.limit = {**DEFAULT_LIMIT, **saved} if saved.get("set") else {}
        self.limit_dlg = None
        self.settings_dlg = None
        # font, size, %, refresh, start-up, update and hover rows all live in the Settings window
        self.menu.add_command(label="Settings…", command=self.open_settings)
        self.menu.add_command(label="Codex spending limit…", command=self.open_limit_dialog)
        self.menu.add_separator()
        self.menu.add_command(label=f"VibeQuota v{VERSION}", state="disabled")
        self.menu.add_command(label="Check for updates", command=lambda: self.check_update(manual=True))
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.quit)
        for w in self.root.winfo_children() + [self.root]:
            w.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
            w.bind("<Button-1>", lambda e: self.click_refresh())
        self.root.update_idletasks()
        self.hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        self.cfg = cfg  # plain copy the tray thread can read; tk variables are only touched on the tk thread
        self.embed()
        M = pystray.MenuItem
        # tray callbacks run on pystray's thread: everything is handed to tk
        self.tray = pystray.Icon(APP, Image.open(ASSETS / "app_icon.png"), APP, pystray.Menu(
            M("Refresh", lambda: self.root.after(0, self.menu_refresh)),
            pystray.Menu.SEPARATOR,
            M("Settings…", lambda: self.root.after(0, self.open_settings)),
            M("Codex spending limit…", lambda: self.root.after(0, self.open_limit_dialog)),
            pystray.Menu.SEPARATOR,
            M(f"VibeQuota v{VERSION}", None, enabled=False),
            M("Check for updates", lambda: self.root.after(0, lambda: self.check_update(manual=True))),
            pystray.Menu.SEPARATOR,
            M("Exit", lambda: self.root.after(0, self.quit))))
        self.tray.run_detached()
        if not self.sources:
            self.root.after(3000, lambda: self.notify(
                "No Codex or Claude Code sign-in (or API key) found. Sign in, then click the widget to refresh."))
        self.schedule_update_check(30_000)
        self.show_numbers()  # cached numbers right away, so a restart never shows --%
        self.refresh()

    def build_rows(self):
        """One row (icon, 5H, W) per tool in self.sources; rebuilt when a sign-in / API key appears or goes."""
        for row in self.rows.values():
            for w in row:
                w.destroy()
        for b in self.icon_labels.values():
            b.destroy()
        if getattr(self, "empty_label", None):
            self.empty_label.destroy()
            self.empty_label = None
        self.rows, self.icon_labels = {}, {}
        if not self.sources:  # nothing signed in: say so instead of an empty (invisible) widget
            self.empty_label = tk.Label(self.root, text="No Codex / Claude sign-in", bg=BG, fg=DIM, font=self.font,
                                        bd=0, padx=4, pady=0)
            self.empty_label.grid(row=0, column=0, columnspan=5, sticky="w")
            w = self.empty_label
            w.bind("<Enter>", lambda e: self.hover_enter())
            w.bind("<Leave>", lambda e: self.hover_leave())
            w.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
            w.bind("<Button-1>", lambda e: self.click_refresh())
        for r, key in enumerate(self.sources):
            b = tk.Label(self.root, bg=BG, bd=0)
            if key in self.icons:
                b.configure(image=self.icons[key])
            b.grid(row=r, column=0, padx=(2, 4), pady=0)
            # tags stay put; each number follows its tag, in a column wide enough for "100%"
            t5, h5, tw, wk = (tk.Label(self.root, text=t, bg=BG, fg=DIM, font=self.font, bd=0, padx=0, pady=0)
                              for t in ("5H", "--%", "W", "--%"))
            t5.grid(row=r, column=1, sticky="w")
            h5.grid(row=r, column=2, sticky="w", padx=(3, 0))
            tw.grid(row=r, column=3, sticky="w", padx=(4, 0))
            wk.grid(row=r, column=4, sticky="w", padx=(3, 2))
            self.rows[key] = (t5, h5, tw, wk)
            self.icon_labels[key] = b
            for w in (b, t5, h5, tw, wk):
                w.bind("<Enter>", lambda e: self.hover_enter())
                w.bind("<Leave>", lambda e: self.hover_leave())
                w.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
                w.bind("<Button-1>", lambda e: self.click_refresh())

    def sync_sources(self) -> bool:
        """Re-check which tools are signed in / have a key (cheap file reads). Changed: rebuild the rows."""
        now = [k for k in SOURCES if auth_kind(k)]
        if now == self.sources:
            return False
        for k in set(self.sources) - set(now):
            self.data.pop(k, None)
        for k in now:
            self.data.setdefault(k, None)
        self.sources = now
        self.hide_tip()
        if self.dpi:  # icons for a newly shown tool
            px = round(18 * self.dpi / 96)
            for k in now:
                if k not in self.icons:
                    self.icons[k] = ImageTk.PhotoImage(self.icon_src[k].resize((px, px), Image.LANCZOS))
        self.build_rows()
        self.show_numbers()
        return True

    def apply_font(self):
        cfg = self.save_settings()
        self.font.configure(family=cfg["family"], size=cfg["size"], weight="bold" if cfg["bold"] else "normal",
                            slant="italic" if cfg["italic"] else "roman")
        self.fit_columns()
        self.root.update_idletasks()
        self.embed(full=False)  # resize/reposition now for the new text width

    def fit_columns(self):
        # the 5H number column fits "100%" so W never moves; the last column is as wide as its text; measured from the current font so size and DPI changes follow
        self.root.grid_columnconfigure(2, minsize=self.font.measure("100%") + 3)  # + padx of the number

    def apply_dpi(self, dpi):
        """Display scale changed (100% -> 125% ...): rescale fonts (widget and hover), columns and icons."""
        self.dpi = dpi
        self.root.tk.call("tk", "scaling", dpi / 72)  # point sizes -> pixels at the new scale
        self.font.configure(size=self.cfg["size"])  # re-measure at the new scaling
        px = round(18 * dpi / 96)
        for key, b in self.icon_labels.items():
            self.icons[key] = ImageTk.PhotoImage(self.icon_src[key].resize((px, px), Image.LANCZOS))
            b.configure(image=self.icons[key])
        self.fit_columns()
        self.root.update_idletasks()

    def apply_refresh(self):
        self.save_settings()
        self.refresh()  # fetch now; update_ui then schedules the next fetch with the new interval

    def apply_autostart(self):
        set_autostart(self.v_autostart.get())
        self.save_settings()

    # ---- self-update from GitHub releases -------------------------------------------------------
    def schedule_update_check(self, delay_ms: int):
        self.root.after(delay_ms, lambda: self.check_update(manual=False))

    def check_update(self, manual: bool):
        """Look for a newer release; install it when auto update is on (or the user asked)."""
        if not manual:
            self.schedule_update_check(UPDATE_EVERY_MS)
            if not self.v_autoupdate.get():
                return

        def work():
            try:
                tag, url = latest_release()
            except Exception as e:  # offline, rate limited, no release yet
                if manual:
                    self.notify(f"Could not check for updates: {type(e).__name__}")
                return
            if not is_newer(tag, VERSION):
                if manual:
                    self.notify(f"VibeQuota v{VERSION} is the latest version.")
                return
            if not getattr(sys, "frozen", False) or not url:
                self.notify(f"VibeQuota {tag} is available on GitHub.")
                return
            self.notify(f"Updating to VibeQuota {tag}…")
            try:
                install_update(url)
            except Exception as e:
                self.notify(f"Update to {tag} failed: {type(e).__name__}: {e}")
                return
            self.root.after(0, self.restart_after_update)

        threading.Thread(target=work, daemon=True).start()

    def restart_after_update(self):
        # onefile: without the reset the new exe reuses our temp _MEI folder, which is deleted as we exit
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        subprocess.Popen([sys.executable, "--after-update"], close_fds=True, env=env,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        self.quit()

    def notify(self, text: str):
        try:
            self.tray.notify(text, APP)
        except Exception:
            pass

    def save_settings(self) -> dict:
        cfg = {"family": self.v_family.get(), "size": self.v_size.get(), "bold": self.v_bold.get(),
               "italic": self.v_italic.get(), "refresh": self.v_refresh.get(),
               "autostart": self.v_autostart.get(), "autoupdate": self.v_autoupdate.get(),
               "remaining": self.v_remaining.get(), "codex_limit": dict(self.limit),
               "hover_hidden": list(self.hidden)}
        self.cfg = cfg
        self.tray.update_menu()
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(cfg), encoding="utf-8")
        return cfg

    def quit(self):
        try:
            self.tray.stop()
            self.root.destroy()
        finally:
            os._exit(0)  # a fetch or tray thread still running would otherwise keep the process alive

    def embed(self, full=True):
        """Like TrafficMonitor: make our window a child of the taskbar so it lives inside it.
        Runs at start and on menu Refresh only (no polling). full=False just resizes for new text."""
        u = ctypes.windll.user32
        bar = u.FindWindowW("Shell_TrayWnd", None)
        if bar and u.GetParent(self.hwnd) != bar:
            u.SetWindowLongW(self.hwnd, -16, 0x40000000 | 0x10000000)  # GWL_STYLE = WS_CHILD | WS_VISIBLE
            u.SetParent(self.hwnd, bar)
            # Win11 draws its XAML taskbar over plain child windows; a layered child is composited on top.
            # The colour key also makes the background transparent.
            u.SetWindowLongW(self.hwnd, -20, u.GetWindowLongW(self.hwnd, -20) | 0x80000)  # WS_EX_LAYERED
            # key on a colour we never draw: the background stays solid (taskbar-coloured), so hovering
            # anywhere on the widget works; transparent key pixels would be click-through
            u.SetLayeredWindowAttributes(self.hwnd, 0x00010203, 0, 1)  # LWA_COLORKEY
        global BG
        dpi = u.GetDpiForWindow(bar) if bar and full else self.dpi
        if dpi and dpi != self.dpi:
            self.apply_dpi(dpi)
        bg = taskbar_colour() if full else BG  # the taskbar is translucent: its colour follows the wallpaper
        if bg != BG:
            BG = bg
            for wdg in (self.root, *self.root.winfo_children()):
                if isinstance(wdg, (tk.Tk, tk.Label)):
                    wdg.configure(bg=BG)
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x, y = taskbar_spot(w, h)
        r = wintypes.RECT()
        u.GetWindowRect(bar, ctypes.byref(r))
        u.SetWindowPos(self.hwnd, 0, x - r.left, y - r.top, w, h, 0x0040)  # HWND_TOP, SWP_SHOWWINDOW
        u.RedrawWindow(self.hwnd, None, None, 0x0001 | 0x0004 | 0x0080)  # INVALIDATE|ERASE|ALLCHILDREN

    def menu_refresh(self):
        """Menu Refresh: layout too (taskbar, tray width, display scale, colour), then the numbers."""
        self.embed()
        self.refresh(force=True)

    def refresh(self, force: bool = False):
        """force (user asked): also fetch what is not due yet; a 429 cool-down still applies."""
        if getattr(self, "_busy", False):  # a click while a refresh is running: let that one finish
            return
        self._busy = True
        self.sync_sources()  # signed in / out, or a key added, since the last refresh: show or hide that row

        def work():
            fetchers = {"codex": fetch_codex, "claude": fetch_claude}
            new = {}
            try:
                for k in list(self.sources):
                    prev = self.data.get(k)
                    local = {}
                    try:  # local tokens / tools / cost from session logs
                        local = {"stats": usage_stats.stats(k), "stats_at": f"{datetime.now():%d/%m %H:%M}",
                                 "stats_note": None}
                    except Exception:  # keep the previous table rather than an empty one
                        local = {f: (prev or {}).get(f) for f in ("stats", "stats_at", "stats_note")}
                    guard = None
                    if k == "codex":  # limit first: an unblock puts auth.json back before the API is called
                        guard = self.enforce_limit({"apikey": auth_kind(k) == "apikey", **local})
                    if auth_kind(k) == "apikey":  # no subscription limits to fetch: local stats only
                        new[k] = {"apikey": True, "h5": None, "week": None, "plan": None, "limits": [], "notes": [],
                                  "resets": [], "analytics": None, "error": None,
                                  "updated": f"{datetime.now():%d/%m %H:%M:%S}"}
                    elif k == "codex" and codex_locked():  # blocked: no sign-in to call the API with
                        new[k] = dict(prev or {"h5": None, "week": None, "plan": None, "limits": [], "notes": [],
                                               "resets": [], "analytics": None, "updated": None})
                        new[k]["error"] = "blocked by the spending limit"
                        new[k]["apikey"] = False
                    else:
                        new[k] = dict(safe(fetchers[k], prev, force))
                        new[k]["apikey"] = False  # signed in: clear a flag left from an API-key setup
                    new[k].update(local)
                    if k == "codex":
                        err = new[k].get("error") or ""
                        if not new[k].get("apikey") and err and not err.startswith("cached from"):
                            codex_limits_from_log(new[k], err)  # API failed: 5H / Week from the last local request
                        new[k]["guard"] = guard
                if extra_codex_files():  # extra Codex sign-ins: own popup column, only when such files exist
                    new["codex_extra"] = {"accounts": fetch_codex_extra(),
                                          "updated": f"{datetime.now():%d/%m %H:%M:%S}"}
                else:
                    new["codex_extra"] = None
            finally:
                self.root.after(0, self.update_ui, new)
        threading.Thread(target=work, daemon=True).start()

    # ---- Codex spending limit (API key only) ----------------------------------------------------
    def effective_limit(self) -> dict:
        """The limit the user saved; until then the default: on ($3 a day) with an API key, off with a
        ChatGPT sign-in (it has its own 5H / weekly limits), which the user can still turn on."""
        if self.limit.get("set"):
            return dict(self.limit)
        return {**DEFAULT_LIMIT, "on": auth_kind("codex") == "apikey"}

    def enforce_limit(self, d: dict) -> dict | None:
        """Runs on the refresh thread after the local stats: lock Codex when the limit is reached, unlock it
        when it is not (new day / month, higher limit, limit off). Returns what the widget and hover show."""
        lim = self.effective_limit()
        used = limit_used(d.get("stats"), lim)
        over = bool(lim["on"] and used is not None and used >= lim["value"])
        was = codex_locked()
        err = None
        try:
            if lim["on"] and used is None:
                pass  # stats not readable this time: keep the current state rather than unlock by mistake
            elif over:
                if not was or SOURCES["codex"].exists():  # also catches a fresh auth.json made while locked
                    lock_codex(lim["kill"])
            elif was:
                unlock_codex()
        except OSError as e:
            err = f"{type(e).__name__}: {e}"
        now = codex_locked()
        # Warnings as Token Watch does them: each level once per period, remembered across restarts
        # (limit_alerts.json); a new day / month, or a changed limit, starts again.
        if lim["on"] and used is not None:
            pct = 100 * used / lim["value"]
            level_now = max((s for s in (*LIMIT_WARN_STEPS, 100) if pct >= s), default=0)
            key = f"{lim['kind']}|{lim['value']:g}|{lim['period']}|{period_key(lim['period'])}"
            acked = load_alerts(key)
            acked = [a for a in acked if a <= pct]  # rolling 7/30 days fell back under a level: warn again later
            if level_now and level_now not in acked:
                st = (d.get("stats") or {}).get(lim["period"]) or {}
                unpriced = " Some usage has no known price, so the real cost is higher." \
                    if lim["kind"] == "cost" and st.get("unpriced") else ""
                what = "cost" if lim["kind"] == "cost" else "tokens"
                tail = " Codex is now blocked." if level_now >= 100 else " Codex is blocked at 100%."
                self.notify(f"Codex · {dict(LIMIT_PERIODS)[lim['period']]} {what} reached {level_now}% — "
                            f"{fmt_limit(used, lim['kind'])} / {fmt_limit(lim['value'], lim['kind'])}.{tail}{unpriced}")
                acked = sorted({*acked, *(s for s in (*LIMIT_WARN_STEPS, 100) if s <= level_now)})
            save_alerts(key, acked)
        if was and not now:
            self.notify("Codex unblocked.")
        if not lim["on"] and not now:
            return None
        return {"used": used, "limit": lim["value"], "kind": lim["kind"], "period": lim["period"],
                "blocked": now, "error": err}

    def open_settings(self):
        """One window for the widget's look and behaviour and for what the hover shows. OK applies and saves."""
        if self.settings_dlg and self.settings_dlg.winfo_exists():
            self.settings_dlg.lift()
            return
        from tkinter import ttk
        self.hide_tip()
        dlg = self.settings_dlg = tk.Toplevel(self.root)
        dlg.title(f"{APP}: Settings")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        pad = {"padx": 10, "pady": 3}

        wid = ttk.LabelFrame(dlg, text="Widget")
        wid.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=(10, 4))
        v_family = tk.StringVar(value=self.v_family.get())
        v_size = tk.StringVar(value=str(self.v_size.get()))
        v_bold = tk.BooleanVar(value=self.v_bold.get())
        v_italic = tk.BooleanVar(value=self.v_italic.get())
        v_show = tk.StringVar(value="Remaining %" if self.v_remaining.get() else "Used %")
        every = dict(REFRESH_CHOICES)
        v_every = tk.StringVar(value=every.get(self.v_refresh.get(), REFRESH_CHOICES[0][1]))
        v_auto = tk.BooleanVar(value=self.v_autostart.get())
        v_upd = tk.BooleanVar(value=self.v_autoupdate.get())
        rows = (("Font", ttk.Combobox(wid, textvariable=v_family, values=FONT_CHOICES, state="readonly", width=20)),
                ("Size", ttk.Combobox(wid, textvariable=v_size, values=[str(n) for n in SIZE_CHOICES],
                                      state="readonly", width=20)),
                ("Show", ttk.Combobox(wid, textvariable=v_show, values=["Used %", "Remaining %"],
                                      state="readonly", width=20)),
                ("Refresh every", ttk.Combobox(wid, textvariable=v_every, values=[l for _, l in REFRESH_CHOICES],
                                               state="readonly", width=20)))
        for i, (label, box) in enumerate(rows):
            ttk.Label(wid, text=label).grid(row=i, column=0, sticky="w", **pad)
            box.grid(row=i, column=1, sticky="w", padx=(0, 10), pady=3)
        style = ttk.Frame(wid)
        style.grid(row=len(rows), column=1, sticky="w", pady=3)
        ttk.Checkbutton(style, text="Bold", variable=v_bold).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(style, text="Italic", variable=v_italic).pack(side="left")
        ttk.Checkbutton(wid, text="Start with Windows", variable=v_auto).grid(
            row=len(rows) + 1, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(wid, text="Auto update", variable=v_upd).grid(
            row=len(rows) + 2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(wid, text="Codex spending limit…", command=self.open_limit_dialog).grid(
            row=len(rows) + 3, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 10))

        hov = ttk.LabelFrame(dlg, text="Hover shows")
        hov.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=(10, 4))
        shown = {}
        for i, (key, label) in enumerate(HOVER_BLOCKS):
            shown[key] = tk.BooleanVar(value=key not in self.hidden)
            ttk.Checkbutton(hov, text=label, variable=shown[key]).grid(row=i, column=0, columnspan=2,
                                                                       sticky="w", padx=10, pady=2)
        base = len(HOVER_BLOCKS)  # rows chosen apart for each table: one column each
        for col, (table, title, labels) in enumerate((("local", "Local table rows", HOVER_ROWS),
                                                      ("server", "Server table rows", HOVER_SERVER_ROWS))):
            ttk.Label(hov, text=title).grid(row=base, column=col, sticky="w", padx=10, pady=(8, 2))
            for i, label in enumerate(labels):
                k = f"{table}:{label}"
                shown[k] = tk.BooleanVar(value=k not in self.hidden)
                ttk.Checkbutton(hov, text=label, variable=shown[k]).grid(
                    row=base + 1 + i, column=col, sticky="nw", padx=10, pady=1)

        def ok():
            self.v_family.set(v_family.get())
            self.v_size.set(int(v_size.get()))
            self.v_bold.set(v_bold.get())
            self.v_italic.set(v_italic.get())
            self.v_remaining.set(v_show.get() == "Remaining %")
            new_every = next(s for s, l in REFRESH_CHOICES if l == v_every.get())
            every_changed = new_every != self.v_refresh.get()
            self.v_refresh.set(new_every)
            if v_auto.get() != self.v_autostart.get():
                self.v_autostart.set(v_auto.get())
                set_autostart(v_auto.get())
            self.v_autoupdate.set(v_upd.get())
            self.hidden = [k for k, v in shown.items() if not v.get()]
            self.apply_font()  # saves everything and resizes the widget
            self.show_numbers()
            if every_changed:
                self.refresh()
            dlg.destroy()

        bar = ttk.Frame(dlg)
        bar.grid(row=1, column=0, columnspan=2, sticky="e", padx=10, pady=(4, 10))
        ttk.Button(bar, text="OK", width=10, command=ok).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancel", width=10, command=dlg.destroy).pack(side="left")
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        dlg.geometry(f"+{(dlg.winfo_screenwidth() - dlg.winfo_reqwidth()) // 2}"
                     f"+{(dlg.winfo_screenheight() - dlg.winfo_reqheight()) // 2}")
        dlg.focus_force()

    def open_limit_dialog(self):
        if self.limit_dlg and self.limit_dlg.winfo_exists():
            self.limit_dlg.lift()
            return
        self.hide_tip()
        lim = self.effective_limit()
        dlg = self.limit_dlg = tk.Toplevel(self.root)
        dlg.title(f"{APP}: Codex spending limit")
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        pad = {"padx": 10, "pady": 4}
        v_on = tk.BooleanVar(value=lim["on"])
        v_kind = tk.StringVar(value=lim["kind"])
        v_period = tk.StringVar(value=dict(LIMIT_PERIODS)[lim["period"]])
        v_value = tk.StringVar(value=f"{lim['value']:g}")
        v_kill = tk.BooleanVar(value=lim["kill"])
        tk.Checkbutton(dlg, text="Block Codex when the limit is reached", variable=v_on).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)
        tk.Label(dlg, text="Count").grid(row=1, column=0, sticky="w", **pad)
        for i, (val, label) in enumerate(LIMIT_KINDS):
            tk.Radiobutton(dlg, text=label, variable=v_kind, value=val).grid(row=1, column=1 + i, sticky="w")
        tk.Label(dlg, text="Period").grid(row=2, column=0, sticky="w", **pad)
        from tkinter import ttk
        ttk.Combobox(dlg, textvariable=v_period, values=[l for _, l in LIMIT_PERIODS], state="readonly",
                     width=16).grid(row=2, column=1, columnspan=2, sticky="w")
        tk.Label(dlg, text="Limit").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(dlg, textvariable=v_value, width=18).grid(row=3, column=1, sticky="w")
        unit = tk.Label(dlg, text="")
        unit.grid(row=3, column=2, sticky="w")

        def show_unit(*_):  # unit follows Count: USD, or tokens
            unit.config(text="USD ($)" if v_kind.get() == "cost" else "tokens")
        v_kind.trace_add("write", show_unit)
        show_unit()
        # typing a limit means the user wants it: tick "Block Codex" (they can still untick it afterwards)
        v_value.trace_add("write", lambda *_: v_on.set(True) if v_value.get().strip() else None)
        tk.Checkbutton(dlg, text="Also close running Codex (CLI / app / VS Code) when blocking",
                       variable=v_kill).grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        msg = tk.Label(dlg, text="", fg="#c42b1c")
        msg.grid(row=6, column=0, columnspan=3, sticky="w", padx=10)

        def ok():
            try:
                value = float(v_value.get().replace(",", "").replace("$", "").strip())
                if value <= 0:
                    raise ValueError
            except ValueError:
                msg.config(text="Enter a number above 0.")
                return
            self.limit = {"on": v_on.get(), "kind": v_kind.get(), "value": value, "kill": v_kill.get(), "set": True,
                          "period": next(k for k, l in LIMIT_PERIODS if l == v_period.get())}
            self.save_settings()
            dlg.destroy()
            self.refresh(force=True)  # check the new limit now

        bar = tk.Frame(dlg)
        bar.grid(row=7, column=0, columnspan=3, sticky="e", padx=10, pady=8)
        tk.Button(bar, text="OK", width=9, command=ok).pack(side="left", padx=4)
        tk.Button(bar, text="Cancel", width=9, command=dlg.destroy).pack(side="left")
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        dlg.geometry(f"+{(dlg.winfo_screenwidth() - dlg.winfo_reqwidth()) // 2}"
                     f"+{(dlg.winfo_screenheight() - dlg.winfo_reqheight()) // 2}")
        dlg.focus_force()

    def switch_account(self, name: str):
        """Make ~/.codex/auth_<name>.json the active Codex sign-in (the current one is kept as auth_<user>.json)."""
        from tkinter import messagebox
        acct = next((a for a in ((self.data.get("codex_extra") or {}).get("accounts") or []) if a["file"] == name), {})
        self.hide_tip()
        expired = ("\n\nThis account's token has expired: Codex may ask you to sign in again after switching."
                   if acct.get("expired") else "")
        if not messagebox.askyesno(APP, f"Switch Codex to {acct.get('email', name)}?\n\n"
                                        "Close Codex (CLI / app / VS Code) first: a running Codex can write\n"
                                        "its old token back to auth.json. Reopen it after switching." + expired):
            return
        try:
            saved = switch_codex_account(name)
        except Exception as e:
            messagebox.showerror(APP, f"Switch failed, nothing changed:\n{type(e).__name__}: {e}")
            return
        self.notify(f"Codex now uses {acct.get('email', name)}. Previous account saved as {saved}.")
        self.data["codex"] = None
        self.click_refresh()

    def click_refresh(self):
        for row in self.rows.values():  # dim the numbers until the new ones arrive, so the click is visible
            for lbl in row:
                lbl.config(fg=DIM)
        self.refresh(force=True)

    def apply_remaining(self):
        self.save_settings()
        self.show_numbers()

    def show_numbers(self):
        for key, (t5, h5, tw, wk) in self.rows.items():
            d = self.data.get(key) or {}
            if d.get("apikey") or (key == "codex" and (d.get("guard") or {}).get("blocked")):
                # one label across the row instead of 5H / W: API key / spend so far / BLOCKED
                for w in (h5, tw, wk):
                    w.grid_remove()
                g = d.get("guard") if key == "codex" else None
                if g and g["blocked"]:
                    t5.config(text="BLOCKED", fg=color(100))
                elif g and g["used"] is not None:  # e.g. "$1.20 / $5": colour by share of the limit
                    t5.config(text=f"{fmt_limit(g['used'], g['kind'])} / {fmt_limit(g['limit'], g['kind'])}",
                              fg=color(100 * g["used"] / g["limit"]))
                else:
                    t5.config(text="API key", fg=DIM)
                t5.grid(columnspan=4)
                continue
            t5.config(text="5H")
            t5.grid(columnspan=1)
            for w in (h5, tw, wk):
                w.grid()
            for tag, lbl, v in ((t5, h5, d.get("h5")), (tw, wk, d.get("week"))):
                lbl.config(text="--%" if v is None else f"{(100 - v if self.cfg['remaining'] else v):.0f}%")
                for w in (tag, lbl):
                    w.config(fg=color(v))  # colour always follows % used
        self.root.update_idletasks()
        self.embed(full=False)  # width follows the numbers; the right edge stays next to the tray

    def update_ui(self, new):
        self._busy = False
        self.data = {**self.data, **new}
        self.show_numbers()
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        for k, d in new.items():  # everything shown in the hover, so a restart can show it at once
            if k == "codex_extra":
                cache[k] = d
                continue
            entry = cache.get(k) or {}
            entry["apikey"] = bool(d.get("apikey"))  # always the current kind, even when this fetch failed
            if not d.get("error"):  # quota part only when this fetch worked: replaced, not merged with old fields
                entry = {f: entry[f] for f in ("stats", "stats_at") if f in entry}
                entry.update({f: v for f, v in d.items() if f not in ("stats", "stats_at", "stats_note", "error")})
            if d.get("stats") and not d.get("stats_note"):  # table part only when freshly computed
                entry.update(stats=d["stats"], stats_at=d.get("stats_at"))
            cache[k] = entry
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
        if getattr(self, "_timer", None):
            self.root.after_cancel(self._timer)
        delay = every = self.v_refresh.get()
        left = NEXT_FETCH.get(fetch_claude, 0) - time.time()
        if every < CLAUDE_EVERY[0] and 0 < left < every:  # Claude falls due before the next tick: do not wait for it
            delay = left
        self._timer = self.root.after(int(delay * 1000) + 50, self.refresh)

    # The popup stays open while the mouse is on the widget or on the popup itself; it closes a moment
    # after the mouse has left both, so there is time to move from the widget up into the popup.
    def hover_enter(self):
        if getattr(self, "_hide_job", None):
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        if not self.tip:
            self.show_tip()

    def hover_leave(self):
        if getattr(self, "_hide_job", None):
            self.root.after_cancel(self._hide_job)
        self._hide_job = self.root.after(200, self.hide_tip)

    def show_tip(self):
        self.hide_tip()
        bg, sep = "#202020", "#3a3a3a"
        self.tip = tk.Toplevel(self.root, bg=sep)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip.bind("<Enter>", lambda e: self.hover_enter())
        self.tip.bind("<Leave>", lambda e: self.hover_leave())
        fs = self.cfg["size"]  # popup text follows the widget's Size setting (10 = default)
        mono = ("Consolas", fs)
        columns = []
        for k in self.sources:  # all tools side by side in one popup
            d = self.data.get(k)
            columns.append(popup_segments(k, d, d.get("stats") if d else None,
                                          {**self.reset_ui, "hidden": self.hidden}))
        if not self.sources:
            columns.append([("VibeQuota", "title"), ("\n", ""),
                            ("Neither Codex nor Claude Code is signed in on this machine,", "dim"), ("\n", ""),
                            ("and no API key was found.", "dim"), ("\n\n", ""),
                            ("Sign in to the Codex CLI / app, or to Claude Code (or set an API key),", ""),
                            ("\n", ""), ("then click here to refresh.", "")])
        extra = self.data.get("codex_extra")
        if extra and extra.get("accounts"):  # only when ~/.codex/auth_*.json exist
            columns.append(codex_extra_segments(extra["accounts"], extra.get("updated")))
        for i, segs in enumerate(columns):
            text = "".join(t for t, _ in segs)
            w = tk.Text(self.tip, bg=bg, fg="#e8e8e8", font=mono, bd=0, highlightthickness=0, padx=14, pady=10,
                        width=max(len(l) for l in text.splitlines() or [""]) + 1, height=text.count("\n") + 1,
                        cursor="arrow")
            w.tag_configure("title", font=("Segoe UI", fs + 2, "bold"), foreground="#ffffff")
            w.tag_configure("head", foreground="#8ab4f8", font=("Consolas", fs, "bold"))
            w.tag_configure("dim", foreground="#9a9a9a")
            w.tag_configure("err", foreground="#ff7b72")
            w.tag_configure("cost", foreground="#7ee787", font=("Consolas", fs, "bold"))
            w.tag_configure("warn", foreground="#ffb02e")
            for lvl, c in (("ok", "#7ee787"), ("mid", "#ffb02e"), ("hi", "#ff7b72")):
                w.tag_configure("pct_" + lvl, foreground=c, font=("Consolas", fs, "bold"))
            for t, tag in segs:
                if tag.startswith("switch:"):  # clickable [Switch] next to an extra Codex account
                    name = tag.split(":", 1)[1]
                    w.tag_configure(tag, foreground="#8ab4f8", font=("Consolas", fs, "bold", "underline"))
                    w.tag_bind(tag, "<Button-1>", lambda e, n=name: self.switch_account(n))
                    w.tag_bind(tag, "<Enter>", lambda e, w=w: w.config(cursor="hand2"))
                    w.tag_bind(tag, "<Leave>", lambda e, w=w: w.config(cursor="arrow"))
                elif tag.startswith(("use:", "yes:", "no:")):  # limit-reset buttons, confirmed inside the hover
                    w.tag_configure(tag, font=("Consolas", fs, "bold"),
                                    foreground={"use": "#8ab4f8", "yes": "#7ee787", "no": "#ff7b72"}[tag.split(":")[0]])
                    w.tag_bind(tag, "<Button-1>", lambda e, t=tag: self.reset_click(t))
                    w.tag_bind(tag, "<Enter>", lambda e, w=w: w.config(cursor="hand2"))
                    w.tag_bind(tag, "<Leave>", lambda e, w=w: w.config(cursor="arrow"))
                w.insert("end", t, tag or ())
            w.configure(state="disabled")
            # the bigger title font makes the text a little taller than its line count, so a tk.Text would
            # scroll by that bit on the mouse wheel: the hover never scrolls
            w.bind("<MouseWheel>", lambda e: "break")
            w.grid(row=0, column=i, sticky="ns", padx=(1 if i == 0 else 0, 1), pady=1)
            if i < len(self.sources) and self.sources[i] == "codex":
                self.add_bell(w, bg, fs)
        self.tip.update_idletasks()
        r = wintypes.RECT()  # we are a taskbar child, so ask Windows for our screen position
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        x = min(r.left, self.root.winfo_screenwidth() - self.tip.winfo_reqwidth() - 4)
        y = r.top - self.tip.winfo_reqheight() - 4  # small gap: quick to cross into the popup
        self.tip.geometry(f"+{x}+{max(0, y)}")

    def add_bell(self, w, bg, fs):
        """Big bell at the top right of the Codex column: opens the spending limit. Its colour is the state:
        grey off, green under 80%, orange from 80%, red when blocked."""
        g = (self.data.get("codex") or {}).get("guard")
        if not (g and (g["blocked"] or self.effective_limit()["on"])):
            fg, hint = "#9a9a9a", "Spending limit off"
        elif g and g["blocked"]:
            fg, hint = "#ff7b72", "Blocked"
        else:
            pct = 100 * g["used"] / g["limit"] if g and g["used"] is not None else 0
            fg = "#ffb02e" if pct >= LIMIT_WARN_STEPS[0] else "#7ee787"
            hint = f"{pct:.0f}% of limit"
        box = tk.Frame(w, bg=bg, cursor="hand2")
        # one small line level with the title: "Spending limit off 🔔"
        tk.Label(box, text=hint, font=("Segoe UI", max(fs - 1, 7)), bg=bg, fg=fg, bd=0,
                 cursor="hand2").pack(side="left", padx=(0, 3))
        tk.Label(box, text="\U0001F514", font=("Segoe UI Emoji", fs + 1), bg=bg, fg=fg, bd=0,
                 cursor="hand2").pack(side="left")
        box.place(relx=1.0, x=-14, y=12, anchor="ne")
        for x in (box, *box.winfo_children()):
            x.bind("<Button-1>", lambda e: self.open_limit_dialog())

    def reset_click(self, tag: str):
        """[Use] asks inside the hover, [Yes] spends the reset in the background, [No] cancels."""
        kind, _, rest = tag.partition(":")
        key, _, rid = rest.partition(":")
        msg = self.reset_ui["msg"]
        if kind == "use":
            self.reset_ui["confirm"] = (key, rid)
            msg.pop(key, None)
        elif kind == "no":
            self.reset_ui["confirm"] = None
        elif kind == "yes" and not self.reset_ui["busy"]:
            self.reset_ui.update(confirm=None, busy=key)

            def work():
                try:
                    use_limit_reset(key, rid)
                    result = ("Reset used. Limits are being refreshed.", "cost")
                except Exception as e:  # noqa: BLE001 - any failure is shown, nothing else to do
                    result = (f"Reset not used: {e}", "err")
                self.root.after(0, done, result)

            def done(result):
                self.reset_ui.update(busy=None)
                msg[key] = result
                if result[1] == "cost":
                    NEXT_FETCH.pop(fetch_claude, None)  # the Claude throttle must not hide the new numbers
                    self.click_refresh()
                self.rerender_tip()

            threading.Thread(target=work, daemon=True).start()
        self.rerender_tip()

    def rerender_tip(self):
        """Redraw an open hover in place. Re-creating it under the mouse fires a Leave on the old window,
        so the hide that this schedules is cancelled once the pointer is seen over the new one."""
        if not self.tip:
            return
        self.show_tip()

        def keep():
            if not self.tip:
                return
            px, py = self.root.winfo_pointerxy()
            x, y = self.tip.winfo_rootx(), self.tip.winfo_rooty()
            if x <= px < x + self.tip.winfo_width() and y <= py < y + self.tip.winfo_height():
                self.hover_enter()
        self.root.after(60, keep)

    def hide_tip(self):
        self._hide_job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def single_instance() -> bool:
    """True if we are the only VibeQuota. Right after a self-update the old copy is still exiting: wait for it."""
    k32 = ctypes.windll.kernel32
    for _ in range(40 if "--after-update" in sys.argv else 1):
        handle = k32.CreateMutexW(None, False, "VibeQuota.SingleInstance")
        if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return True
        k32.CloseHandle(handle)
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    if not single_instance():  # one widget only: a second launch (Startup + double-click, say) just exits
        sys.exit(0)
    remove_old_exe()
    App().root.mainloop()
