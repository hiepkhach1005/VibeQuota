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
import sys
import subprocess
import threading
import winreg
from ctypes import wintypes
import time
import tkinter as tk
import tkinter.font as tkfont

import pystray
from PIL import Image

import usage_stats
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, so the widget lands on the taskbar

HOME = Path.home()
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
SOURCES = {"codex": HOME / ".codex" / "auth.json", "claude": HOME / ".claude" / ".credentials.json"}


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
VERSION = "1.0.0"  # keep in step with the release tag (v1.0.0): the updater compares the two
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
                "refresh": 60,  # seconds; Token Watch also refreshes quota every minute
                "autostart": True, "autoupdate": True}
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
    c = ctypes.windll.gdi32.GetPixel(dc, (tray.left or r.right - 300) - 2, r.bottom - 3)
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
    return left - w - 8, work.bottom + (screen_h - work.bottom - h) // 2


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
    return json.loads((HOME / ".codex" / "auth.json").read_text(encoding="utf-8"))["tokens"]


def codex_headers(t: dict) -> dict:
    return {"Authorization": f"Bearer {t['access_token']}", "ChatGPT-Account-Id": t.get("account_id", ""),
            "Accept": "application/json", "originator": "codex_cli_rs", "User-Agent": "vibequota"}


def codex_account() -> str:
    try:
        claims = json.loads(base64.urlsafe_b64decode(codex_auth()["id_token"].split(".")[1] + "=="))
        return claims.get("email", "?")
    except (OSError, ValueError, KeyError, IndexError):
        return "?"


def claude_account() -> str:
    try:
        return (json.loads((HOME / ".claude.json").read_text(encoding="utf-8")).get("oauthAccount") or {}).get(
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
        r["resets"] = [(c.get("title") or "Reset", 1, fmt_time(c.get("expires_at")))
                       for c in sorted(avail, key=lambda c: str(c.get("expires_at") or ""))]
        if not r["resets"]:
            r["resets"] = [("Reset", d["rate_limit_reset_credits"]["available_count"], "-")]
    r["analytics"] = codex_analytics(t)
    return r


_analytics_cache = {"at": 0.0, "rows": None}


def codex_analytics(t: dict):
    """Messages and lines of code, as on the Codex app's Settings > Analytics page. Daily data, so cached 10 min."""
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
            turns.update({r["date"]: (r.get("totals") or {}).get("turns") or 0 for r in got_t})
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

    rows = [("Messages", series(turns, lambda v: v)),
            ("Lines added", series(loc, lambda v: v.get("added") or 0)),
            ("Lines removed", series(loc, lambda v: v.get("removed") or 0))]
    _analytics_cache.update(at=time.time(), rows=rows)
    return rows


CLAUDE_LABELS = {"five_hour": "5H", "seven_day": "Week", "seven_day_opus": "Week Opus",
                 "seven_day_sonnet": "Week Sonnet"}


def fetch_claude() -> dict:
    o = json.loads((HOME / ".claude" / ".credentials.json").read_text(encoding="utf-8"))["claudeAiOauth"]
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
    r["resets"] = [(g.get("label") or "Reset", g["resets_left"], fmt_time(g.get("ends_at")))
                   for g in sorted(grants, key=lambda g: str(g.get("ends_at") or ""))]
    if not grants and ce.get("eligible") is False:
        r["notes"].append(f"Limit resets: not eligible ({ce.get('ineligible_reason', '?')})")
    elif not grants:  # Claude sends cedar_ember = null when the account has no reset to offer
        r["notes"].append("Limit resets: none available")
    return r


BACKOFF: dict = {}  # fn -> (skip-until timestamp, consecutive 429 count)


def safe(fn, prev: dict | None) -> dict:
    until, n = BACKOFF.get(fn, (0, 0))
    if time.time() < until and prev:  # still cooling down after a 429: keep showing the last numbers
        return prev
    try:
        r = fn()
        r["updated"], r["error"] = f"{datetime.now():%d/%m %H:%M:%S}", None
        BACKOFF.pop(fn, None)
        return r
    except urllib.error.HTTPError as e:
        if e.code == 429:  # Retry-After is always 0, so back off ourselves: 2, 4, 8, then 15 min max
            wait = min(900, 120 * 2 ** n)
            BACKOFF[fn] = (time.time() + wait, n + 1)
            msg = f"rate limited, retry after {datetime.fromtimestamp(time.time() + wait):%H:%M}"
        else:
            msg = {401: "token expired: use the CLI once"}.get(e.code, f"HTTP {e.code}")
    except Exception as e:  # missing file, network, bad JSON
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


def popup_segments(key: str, d: dict | None, st: dict | None) -> list[tuple[str, str]]:
    """The hover text for one tool as (text, tag) pieces; tags style it in a tk.Text widget."""
    out = []

    def line(*parts):
        out.extend(parts)
        out.append(("\n", ""))

    name, account = HEADER[key]()
    plan = (d or {}).get("plan")
    line((name, "title"), (f"  {plan}" if plan else "", "dim"))
    line((account, "dim"))
    if d and d.get("error"):
        cached = d["error"].startswith("cached from")  # start-up: saved numbers until the first fetch
        line((("" if cached else "! ") + d["error"], "dim" if cached else "err"))
    line(("", ""))

    if d and d.get("limits"):
        line((f"{'Limit':<12}{'Used':>6}   {'Resets at':<12}", "head"))
        for label, pct, reset in d["limits"]:
            line((f"{label:<12}", ""), (f"{pct:>5.0f}%", "pct_" + level(pct)), (f"   {reset:<12}", ""))
        for note in d.get("notes") or []:
            line((note, "err" if "REACHED" in note else "dim"))
        line(("", ""))
    if d and d.get("resets"):
        line((f"{'Limit resets':<38}{'Left':>5}   {'Expires':<12}", "head"))
        for title, count, exp in d["resets"]:
            line((f"{title[:38]:<38}{count:>5}   {exp:<12}", ""))
        line(("", ""))

    cols = ("Today", "7 days", "30 days", "Year", "All time")
    periods = ("today", "7d", "30d", "year", "all")
    rows = []
    if d and d.get("analytics"):  # Codex: from the Analytics API
        rows += [(label, [human(v) for v in vals]) for label, vals in d["analytics"]]
    elif st:  # Claude Code: no API, so counted from the local logs (typed prompts, edit patches)
        rows += [(label, [human(st[p][k]) for p in periods]) for label, k in
                 (("Messages", "messages"), ("Lines added", "lines_added"), ("Lines removed", "lines_removed"))]
    if st:
        for label, k, fmt in (("Sessions", "sessions", human), ("Requests", "requests", human),
                              ("Tool calls", "tool_calls", human), ("Input", "input", human),
                              ("Output", "output", human), ("Cache read", "cache_read", human),
                              ("Cache write", "cache_write", human), ("Total tokens", "total", human),
                              ("Cost", "cost_usd", lambda v: f"${v:,.2f}")):
            rows.append((label, [fmt(st[p][k]) for p in periods]))
    if st and d and d.get("stats_note"):
        line((d["stats_note"], "dim"))
    if not st:
        line(("Reading local session logs for tokens / cost…", "dim"))
        line(("(first start can take ~30 s)", "dim"))
        line(("", ""))
    if rows:
        line((f"{'':<14}" + "".join(f"{c:>10}" for c in cols), "head"))
        for label, vals in rows:
            line((f"{label:<14}", "dim" if label != "Cost" else ""),
                 ("".join(f"{v:>10}" for v in vals), "cost" if label == "Cost" else ""))
        line(("", ""))
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
        # only show tools that are installed (signed in) on this machine
        self.sources = [k for k, f in SOURCES.items() if f.exists()]
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
                if c.get("stats"):
                    c["stats_note"] = f"as of {c.get('stats_at', '?')} · updating…"
            self.data[k] = c
        self.rows = {}
        try:
            cfg = {**DEFAULT_FONT, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            cfg = dict(DEFAULT_FONT)
        self.font = tkfont.Font(self.root, family=cfg["family"], size=cfg["size"],
                                weight="bold" if cfg["bold"] else "normal",
                                slant="italic" if cfg["italic"] else "roman")
        self.icons = {"codex": tk.PhotoImage(file=str(ASSETS / f"icon_codex_{'light' if LIGHT else 'dark'}.png")),
                      "claude": tk.PhotoImage(file=str(ASSETS / "icon_claude.png"))}
        for r, key in enumerate(self.sources):
            b = tk.Label(self.root, image=self.icons[key], bg=BG, bd=0)
            b.grid(row=r, column=0, padx=(2, 4), pady=0)
            h5 = tk.Label(self.root, text="5H --%", bg=BG, fg=DIM, font=self.font, width=7, anchor="e", bd=0, pady=0)
            wk = tk.Label(self.root, text="W --%", bg=BG, fg=DIM, font=self.font, width=6, anchor="e", bd=0, pady=0)
            h5.grid(row=r, column=1)
            wk.grid(row=r, column=2, padx=(0, 2))
            self.rows[key] = (h5, wk)
            for w in (b, h5, wk):
                w.bind("<Enter>", lambda e, k=key: self.show_tip(k))
                w.bind("<Leave>", lambda e: self.hide_tip())
        self.tip = None
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Refresh", command=self.refresh)
        self.v_family = tk.StringVar(value=cfg["family"])
        self.v_size = tk.IntVar(value=cfg["size"])
        self.v_bold = tk.BooleanVar(value=cfg["bold"])
        self.v_italic = tk.BooleanVar(value=cfg["italic"])
        fam = tk.Menu(self.menu, tearoff=0)
        for f in FONT_CHOICES:
            fam.add_radiobutton(label=f, variable=self.v_family, value=f, command=self.apply_font)
        size = tk.Menu(self.menu, tearoff=0)
        for n in SIZE_CHOICES:
            size.add_radiobutton(label=str(n), variable=self.v_size, value=n, command=self.apply_font)
        self.menu.add_separator()
        self.menu.add_cascade(label="Font", menu=fam)
        self.menu.add_cascade(label="Size", menu=size)
        self.menu.add_checkbutton(label="Bold", variable=self.v_bold, command=self.apply_font)
        self.menu.add_checkbutton(label="Italic", variable=self.v_italic, command=self.apply_font)
        self.v_refresh = tk.IntVar(value=cfg["refresh"])
        every = tk.Menu(self.menu, tearoff=0)
        for sec, label in REFRESH_CHOICES:
            every.add_radiobutton(label=label, variable=self.v_refresh, value=sec, command=self.apply_refresh)
        self.menu.add_cascade(label="Refresh every", menu=every)
        self.v_autostart = tk.BooleanVar(value=cfg["autostart"])
        self.menu.add_checkbutton(label="Start with Windows", variable=self.v_autostart, command=self.apply_autostart)
        set_autostart(cfg["autostart"])
        self.v_autoupdate = tk.BooleanVar(value=cfg["autoupdate"])
        self.menu.add_separator()
        self.menu.add_command(label=f"VibeQuota v{VERSION}", state="disabled")
        self.menu.add_command(label="Check for updates", command=lambda: self.check_update(manual=True))
        self.menu.add_checkbutton(label="Auto update", variable=self.v_autoupdate, command=self.save_settings)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.quit)
        for w in self.root.winfo_children() + [self.root]:
            w.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
            w.bind("<Button-1>", lambda e: self.click_refresh())
        self.root.update_idletasks()
        self.hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        self.embed()
        self.cfg = cfg  # plain copy the tray thread can read; tk variables are only touched on the tk thread
        M = pystray.MenuItem

        def tray_set(var, value, then=None):  # tray callbacks run on pystray's thread: hand the change to tk
            return lambda: self.root.after(0, lambda: (var.set(value), (then or self.apply_font)()))

        self.tray = pystray.Icon(APP, Image.open(ASSETS / "app_icon.png"), APP, pystray.Menu(
            M("Refresh", lambda: self.root.after(0, self.refresh)),
            pystray.Menu.SEPARATOR,
            M("Font", pystray.Menu(*[M(f, tray_set(self.v_family, f), radio=True,
                                       checked=lambda i, f=f: self.cfg["family"] == f) for f in FONT_CHOICES])),
            M("Size", pystray.Menu(*[M(str(n), tray_set(self.v_size, n), radio=True,
                                       checked=lambda i, n=n: self.cfg["size"] == n) for n in SIZE_CHOICES])),
            M("Bold", lambda: self.root.after(0, lambda: (self.v_bold.set(not self.v_bold.get()), self.apply_font())),
              checked=lambda i: self.cfg["bold"]),
            M("Italic", lambda: self.root.after(0, lambda: (self.v_italic.set(not self.v_italic.get()), self.apply_font())),
              checked=lambda i: self.cfg["italic"]),
            M("Start with Windows",
              lambda: self.root.after(0, lambda: (self.v_autostart.set(not self.v_autostart.get()),
                                                  self.apply_autostart())),
              checked=lambda i: self.cfg["autostart"]),
            M("Refresh every", pystray.Menu(*[M(label, tray_set(self.v_refresh, sec, self.apply_refresh), radio=True,
                                                checked=lambda i, sec=sec: self.cfg["refresh"] == sec)
                                              for sec, label in REFRESH_CHOICES])),
            pystray.Menu.SEPARATOR,
            M(f"VibeQuota v{VERSION}", None, enabled=False),
            M("Check for updates", lambda: self.root.after(0, lambda: self.check_update(manual=True))),
            M("Auto update", lambda: self.root.after(0, lambda: (self.v_autoupdate.set(not self.v_autoupdate.get()),
                                                                 self.save_settings())),
              checked=lambda i: self.cfg["autoupdate"]),
            pystray.Menu.SEPARATOR,
            M("Exit", lambda: self.root.after(0, self.quit))))
        self.tray.run_detached()
        self.schedule_update_check(30_000)
        self.show_numbers()  # cached numbers right away, so a restart never shows --%
        self.refresh()

    def apply_font(self):
        cfg = self.save_settings()
        self.font.configure(family=cfg["family"], size=cfg["size"], weight="bold" if cfg["bold"] else "normal",
                            slant="italic" if cfg["italic"] else "roman")
        self.root.update_idletasks()
        self.embed(reschedule=False)  # resize/reposition now for the new text width

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
        subprocess.Popen([sys.executable, "--after-update"], close_fds=True,
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
               "autostart": self.v_autostart.get(), "autoupdate": self.v_autoupdate.get()}
        self.cfg = cfg
        self.tray.update_menu()
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(cfg), encoding="utf-8")
        return cfg

    def quit(self):
        self.tray.stop()
        self.root.destroy()

    def embed(self, reschedule=True):
        """Like TrafficMonitor: make our window a child of the taskbar so it lives inside it."""
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
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x, y = taskbar_spot(w, h)
        r = wintypes.RECT()
        u.GetWindowRect(bar, ctypes.byref(r))
        u.SetWindowPos(self.hwnd, 0, x - r.left, y - r.top, w, h, 0x0040)  # HWND_TOP, SWP_SHOWWINDOW
        u.RedrawWindow(self.hwnd, None, None, 0x0001 | 0x0004 | 0x0080)  # INVALIDATE|ERASE|ALLCHILDREN
        if reschedule:
            self.root.after(5000, self.embed)  # tray width changes; Explorer restarts recreate the taskbar

    def refresh(self):
        if getattr(self, "_busy", False):  # a click while a refresh is running: let that one finish
            return
        self._busy = True

        def work():
            fetchers = {"codex": fetch_codex, "claude": fetch_claude}
            new = {}
            try:
                for k in self.sources:
                    prev = self.data[k]
                    new[k] = dict(safe(fetchers[k], prev))
                    try:  # local tokens / tools / cost from session logs
                        new[k]["stats"] = usage_stats.stats(k)
                        new[k]["stats_at"] = f"{datetime.now():%d/%m %H:%M}"
                        new[k]["stats_note"] = None
                    except Exception:  # keep the previous table rather than an empty one
                        for f in ("stats", "stats_at", "stats_note"):
                            new[k][f] = (prev or {}).get(f)
            finally:
                self.root.after(0, self.update_ui, new)
        threading.Thread(target=work, daemon=True).start()

    def click_refresh(self):
        for h5, wk in self.rows.values():  # dim the numbers until the new ones arrive, so the click is visible
            h5.config(fg=DIM)
            wk.config(fg=DIM)
        self.refresh()

    def show_numbers(self):
        for key, (h5, wk) in self.rows.items():
            d = self.data.get(key) or {}
            for lbl, tag, v in ((h5, "5H", d.get("h5")), (wk, "W", d.get("week"))):
                lbl.config(text=f"{tag} --%" if v is None else f"{tag} {v:.0f}%", fg=color(v))

    def update_ui(self, new):
        self._busy = False
        self.data = {**self.data, **new}
        self.show_numbers()
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        for k, d in new.items():  # everything shown in the hover, so a restart can show it at once
            entry = cache.get(k) or {}
            if not d.get("error"):  # quota part only when this fetch worked
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
        self._timer = self.root.after(self.v_refresh.get() * 1000, self.refresh)

    def show_tip(self, key):
        self.hide_tip()
        bg, sep = "#202020", "#3a3a3a"
        self.tip = tk.Toplevel(self.root, bg=sep)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        mono = ("Consolas", 10)
        for i, k in enumerate(self.sources):  # all tools side by side in one popup
            d = self.data.get(k)
            segs = popup_segments(k, d, d.get("stats") if d else None)
            text = "".join(t for t, _ in segs)
            w = tk.Text(self.tip, bg=bg, fg="#e8e8e8", font=mono, bd=0, highlightthickness=0, padx=14, pady=10,
                        width=max(len(l) for l in text.splitlines() or [""]) + 1, height=text.count("\n") + 1,
                        cursor="arrow")
            w.tag_configure("title", font=("Segoe UI", 12, "bold"), foreground="#ffffff")
            w.tag_configure("head", foreground="#8ab4f8", font=("Consolas", 10, "bold"))
            w.tag_configure("dim", foreground="#9a9a9a")
            w.tag_configure("err", foreground="#ff7b72")
            w.tag_configure("cost", foreground="#7ee787", font=("Consolas", 10, "bold"))
            for lvl, c in (("ok", "#7ee787"), ("mid", "#ffb02e"), ("hi", "#ff7b72")):
                w.tag_configure("pct_" + lvl, foreground=c, font=("Consolas", 10, "bold"))
            for t, tag in segs:
                w.insert("end", t, tag or ())
            w.configure(state="disabled")
            w.grid(row=0, column=i, sticky="ns", padx=(1 if i == 0 else 0, 1), pady=1)
        self.tip.update_idletasks()
        r = wintypes.RECT()  # we are a taskbar child, so ask Windows for our screen position
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        x = min(r.left, self.root.winfo_screenwidth() - self.tip.winfo_reqwidth() - 4)
        y = r.top - self.tip.winfo_reqheight() - 12
        self.tip.geometry(f"+{x}+{max(0, y)}")

    def hide_tip(self):
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
