"""Local token usage stats for Claude Code and Codex logs (stdlib only, read-only).
Prices (USD per 1M tokens: input, cached/read, cache-write, output) from Token Watch."""
import json, os, glob, time
from datetime import datetime

PRICES = {'gpt-5': (1.25, 0.125, 1.25, 10.0), 'gpt-5-codex': (1.25, 0.125, 1.25, 10.0), 'gpt-5-codex-mini': (0.25, 0.025, 0.25, 2.0), 'gpt-5-mini': (0.25, 0.025, 0.25, 2.0), 'gpt-5-nano': (0.05, 0.005, 0.05, 0.4), 'gpt-5-chat-latest': (1.25, 0.125, 1.25, 10.0), 'gpt-5-pro': (15.0, 0.0, 15.0, 120.0), 'gpt-5.1': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-chat-latest': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-codex': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-codex-max': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-codex-mini': (0.25, 0.025, 0.25, 2.0), 'gpt-5.2': (1.75, 0.175, 1.75, 14.0), 'gpt-5.2-chat-latest': (1.75, 0.175, 1.75, 14.0), 'gpt-5.2-codex': (1.75, 0.175, 1.75, 14.0), 'gpt-5.2-pro': (21.0, 0.0, 21.0, 168.0), 'gpt-5.3-chat-latest': (1.75, 0.175, 1.75, 14.0), 'gpt-5.3-codex': (1.75, 0.175, 1.75, 14.0), 'gpt-5.3-codex-spark': (2.0, 1.0, 2.0, 8.0), 'gpt-5.4': (2.5, 0.25, 2.5, 15.0), 'gpt-5.4-mini': (0.75, 0.075, 0.75, 4.5), 'gpt-5.4-pro': (30.0, 0.0, 30.0, 180.0), 'gpt-5.5': (5.0, 0.5, 5.0, 30.0), 'gpt-5.5-pro': (30.0, 0.0, 30.0, 180.0), 'gpt-5.6-sol': (4.0, 0.4, 5.0, 20.0), 'gpt-5.6-cyber': (12.5, 1.25, 15.625, 75.0), 'gpt-5.6-terra': (2.0, 0.2, 2.5, 12.0), 'gpt-5.6-luna': (0.2, 0.02, 0.25, 1.2), 'gpt-6-astra': (10.0, 1.0, 12.5, 50.0), 'gpt-6-sol': (2.0, 0.2, 2.5, 10.0), 'gpt-6-luna': (0.1, 0.01, 0.125, 0.5), 'gpt-6.1-sol': (2.0, 0.1, 2.5, 10.0), 'gpt-6.2-sol': (2.0, 0.1, 2.5, 10.0), 'gpt-6.2-astra': (8.0, 0.4, 10.0, 40.0), 'gpt-6.2-astra-law': (8.0, 0.4, 10.0, 40.0), 'gpt-6.1-terra': (1.0, 0.05, 1.25, 5.0), 'gpt-6-cyber': (20.0, 2.0, 25.0, 100.0), 'gpt-6-rosalind': (15.0, 1.5, 18.75, 75.0), 'gpt-6-astra-law': (10.0, 1.0, 10.0, 50.0), 'gpt-4.1': (2.0, 0.5, 2.0, 8.0), 'gpt-4.1-mini': (0.4, 0.1, 0.4, 1.6), 'gpt-4.1-nano': (0.1, 0.025, 0.1, 0.4), 'gpt-4o': (2.5, 1.25, 2.5, 10.0), 'gpt-4o-mini': (0.15, 0.075, 0.15, 0.6), 'o3-mini': (1.1, 0.55, 1.1, 4.4), 'o4-mini': (1.1, 0.275, 1.1, 4.4), 'claude-opus-5.5': (4.0, 0.2, 5.0, 20.0), 'claude-opus-5-5': (4.0, 0.2, 5.0, 20.0), 'claude-opus-5': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.8': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-8': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.7': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-7': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.6': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-6': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.5': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-5': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.1': (15.0, 1.5, 18.75, 75.0), 'claude-opus-4-1': (15.0, 1.5, 18.75, 75.0), 'claude-opus-4': (15.0, 1.5, 18.75, 75.0), 'claude-fable-5.2': (8.0, 0.2, 10.0, 40.0), 'claude-fable-5-2': (8.0, 0.2, 10.0, 40.0), 'claude-fable-5.1': (10.0, 0.25, 12.5, 50.0), 'claude-fable-5-1': (10.0, 0.25, 12.5, 50.0), 'claude-fable-5': (10.0, 1.0, 12.5, 50.0), 'claude-mythos-5.2': (8.0, 0.2, 10.0, 40.0), 'claude-mythos-5-2': (8.0, 0.2, 10.0, 40.0), 'claude-mythos-5.1': (10.0, 0.25, 12.5, 50.0), 'claude-mythos-5-1': (10.0, 0.25, 12.5, 50.0), 'claude-mythos-5': (10.0, 1.0, 12.5, 50.0), 'claude-sonnet-5.5': (2.0, 0.1, 2.5, 10.0), 'claude-sonnet-5-5': (2.0, 0.1, 2.5, 10.0), 'claude-sonnet-5': (2.0, 0.2, 2.5, 10.0), 'claude-sonnet-4.6': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4-6': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4.5': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4-5': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4': (3.0, 0.3, 3.75, 15.0), 'claude-haiku-5.5': (0.1, 0.01, 0.125, 0.5), 'claude-haiku-5-5': (0.1, 0.01, 0.125, 0.5), 'claude-haiku-4.5': (1.0, 0.1, 1.25, 5.0), 'claude-haiku-4-5': (1.0, 0.1, 1.25, 5.0), 'claude-haiku-3.5': (0.8, 0.08, 1.0, 4.0), 'claude-haiku-3-5': (0.8, 0.08, 1.0, 4.0)}
_CACHE = {}  # (source, path) -> {mtime, size, offset, head, parser, recs}


# Token Watch's "-long-context" tier, per 1M (input, cached, cache_write, output). A session/model whose request
# context ever exceeds LONG_CONTEXT_TOKENS is priced at this tier for the whole session, as Token Watch does.
LONG_CONTEXT_TOKENS = 272_000
LONG_PRICES = {
    "gpt-6-luna": (0.2, 0.02, 0.25, 0.75), "gpt-6-sol": (4.0, 0.4, 5.0, 15.0), "gpt-6.1-sol": (4.0, 0.2, 5.0, 15.0),
    "gpt-6.1-terra": (2.0, 0.1, 2.5, 7.5), "gpt-6.2-sol": (4.0, 0.2, 5.0, 15.0), "gpt-6.2-astra": (16.0, 0.8, 20.0, 60.0), "gpt-6-astra": (20.0, 2.0, 25.0, 75.0), "gpt-6-cyber": (40.0, 4.0, 50.0, 150.0),
    "gpt-5.6-sol": (8.0, 0.8, 10.0, 30.0), "gpt-5.6-terra": (4.0, 0.4, 5.0, 18.0), "gpt-5.6-luna": (0.4, 0.04, 0.5, 1.8),
    "gpt-5.6-cyber": (25.0, 2.5, 31.25, 112.5), "gpt-5.5": (10.0, 1.0, 10.0, 45.0), "gpt-5.4": (5.0, 0.5, 5.0, 22.5),
    "claude-haiku-5-5": (0.5, 0.05, 0.625, 2.5), "claude-haiku-5.5": (0.5, 0.05, 0.625, 2.5),
}


def _price(model, long_context=False):
    m = (model or "").lower()
    if long_context:
        base = next((k for k in sorted(LONG_PRICES, key=len, reverse=True) if m == k or m.startswith(k + "-")), None)
        if base:
            return LONG_PRICES[base]
    if m in PRICES:
        return PRICES[m]
    best = None
    for k in PRICES:
        if m.startswith(k) and (best is None or len(k) > len(best)):
            best = k
    if best is None:
        n = m.replace("-", ".")
        for k in PRICES:
            if n.startswith(k.replace("-", ".")) and (best is None or len(k) > len(best)):
                best = k
    return PRICES[best] if best else None


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _claude_user_event(d, m):
    """('E', ts, prompts, lines_added, lines_removed) for a typed prompt or an edit result; else None."""
    ts = _ts(d.get("timestamp") or "")
    if ts is None or d.get("isMeta") or d.get("isSidechain"):
        return None
    r = d.get("toolUseResult")
    if isinstance(r, dict) and ("structuredPatch" in r or r.get("type") == "create"):
        hunks = r.get("structuredPatch") or []
        lines = [l for h in hunks if isinstance(h, dict) for l in h.get("lines") or [] if isinstance(l, str)]
        add = sum(l.startswith("+") for l in lines)
        rem = sum(l.startswith("-") for l in lines)
        if not hunks and r.get("type") == "create":
            add = len((r.get("content") or "").splitlines())
        return ("E", ts, 0, add, rem)
    c = m.get("content")
    if isinstance(c, list):  # a prompt with images etc.; tool results are lists of tool_result blocks
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):  # "<command-..." etc. are not prompts
        return ("E", ts, 1, 0, 0)
    return None


def codex_home():
    """Codex's folder: $CODEX_HOME when set (as the Codex CLI does), else ~/.codex."""
    return os.path.expanduser(os.environ.get("CODEX_HOME") or os.path.join("~", ".codex"))


def claude_dir():
    """Claude Code's folder: $CLAUDE_CONFIG_DIR when set (as Claude Code does), else ~/.claude."""
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude"))


# Parsers keep their state between calls, so a growing log is read from where the last read stopped
# (byte offset) instead of from the start every refresh; only appended lines are fed to them.
# record: (ts, sid, model, input, output, cache_read, cache_write, tools_list, dedup_key, context_tokens)
class _ClaudeParser:
    def __init__(self, path):
        self.sid = os.path.basename(path)
        self.recs = []

    def feed(self, line):
        if '"usage"' not in line and '"type":"user"' not in line:
            return
        try:
            d = json.loads(line)
            m = d.get("message")
            if d.get("type") == "user" and isinstance(m, dict):
                ev = _claude_user_event(d, m)
                if ev:
                    self.recs.append(ev)
                return
            if d.get("type") != "assistant" or not isinstance(m, dict):
                return
            u = m.get("usage")
            if not isinstance(u, dict):
                return
            ts = _ts(d.get("timestamp") or "")
            if ts is None:
                return
            model = m.get("model") or ""
            if model.startswith("<"):
                return
            tools = [b.get("name") or "unknown" for b in (m.get("content") or [])
                     if isinstance(b, dict) and b.get("type") == "tool_use"]
            key = (m.get("id"), d.get("requestId")) if m.get("id") and d.get("requestId") else None
            self.recs.append((ts, d.get("sessionId") or self.sid, model,
                              u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                              u.get("cache_read_input_tokens") or 0,
                              u.get("cache_creation_input_tokens") or 0, tools, key,
                              0))  # Token Watch measures context only for Codex, so no long-context tier here
        except Exception:
            pass

    def records(self):
        return self.recs


def _text_lines(s):
    return len(s.splitlines()) if isinstance(s, str) else 0


def _diff_lines(diff):
    """(added, removed) of a unified diff or an apply_patch body; file headers are not lines."""
    add = rem = 0
    for l in (diff or "").splitlines():
        if l.startswith(("+++", "---", "***")):
            continue
        if l.startswith("+"):
            add += 1
        elif l.startswith("-"):
            rem += 1
    return add, rem


def _codex_file_change(changes):
    """(added, removed) of a FileChange item: {path: {type: add|delete|update, content | unified_diff}}."""
    add = rem = 0
    for c in (changes or {}).values() if isinstance(changes, dict) else ():
        if not isinstance(c, dict):
            continue
        kind = c.get("type")
        if kind == "add":
            add += _text_lines(c.get("content"))
        elif kind == "delete":
            rem += _text_lines(c.get("content"))
        else:
            a, r = _diff_lines(c.get("unified_diff") or c.get("diff") or "")
            add, rem = add + a, rem + r
    return add, rem


class _CodexParser:
    """Token records as Token Watch reads them (cumulative total_token_usage turned into per-request deltas,
    last_token_usage when there is no total), plus 'E' events for typed prompts and edited lines, so an
    API-key setup (no Analytics API) still gets messages and lines of code. Also keeps the last rate_limits
    snapshot (5H / weekly % as the server reported it), used when the usage API cannot be reached."""

    def __init__(self, path):
        self.sid = os.path.basename(path)
        self.model = "unknown"
        self.prev = (0, 0, 0)  # input, cached, output (cumulative)
        self.pending = []
        self.recs = []
        self.rate = None  # (ts, rate_limits dict) of the newest token_count that carried one
        # Newer Codex writes item_completed (UserMessage / FileChange); older Codex writes user_message events
        # and apply_patch calls. Collect both and keep one kind per file so nothing is counted twice.
        self.new_msgs, self.old_msgs, self.new_edits, self.old_edits = [], [], [], []

    def feed(self, line):
        if not ('"token_count"' in line or '"turn_context"' in line or "tool_call" in line
                or '"function_call"' in line or '"session_meta"' in line
                or '"thread_settings_applied"' in line or '"item_completed"' in line
                or '"user_message"' in line):
            return
        try:
            d = json.loads(line)
            t = d.get("type")
            p = d.get("payload")
            if not isinstance(p, dict):
                return
            pt = p.get("type")
            if t == "session_meta":
                self.sid = p.get("id") or self.sid
            elif t == "turn_context":
                self.model = p.get("model") or self.model
            elif t == "event_msg" and pt == "thread_settings_applied":
                self.model = (p.get("thread_settings") or {}).get("model") or self.model
            elif t == "event_msg" and pt == "item_completed":
                item = p.get("item") or {}
                ts = _ts(d.get("timestamp") or "")
                if ts is None:
                    return
                if item.get("type") == "UserMessage":
                    self.new_msgs.append(("E", ts, 1, 0, 0))
                elif item.get("type") == "FileChange":
                    add, rem = _codex_file_change(item.get("changes"))
                    self.new_edits.append(("E", ts, 0, add, rem))
            elif t == "event_msg" and pt == "user_message":
                ts = _ts(d.get("timestamp") or "")
                if ts is not None and (p.get("message") or "").strip():
                    self.old_msgs.append(("E", ts, 1, 0, 0))
            elif t == "response_item" and pt in ("function_call", "custom_tool_call"):
                name = p.get("name") or "unknown"
                self.pending.append(name)
                if name == "apply_patch":
                    ts = _ts(d.get("timestamp") or "")
                    body = p.get("input") or ""
                    if not isinstance(body, str) or "*** Begin Patch" not in body:  # function_call form
                        try:
                            a = json.loads(p.get("arguments") or "{}")
                            body = a.get("input") or " ".join(a.get("command") or [])
                        except Exception:
                            body = ""
                    if ts is not None and body:
                        add, rem = _diff_lines(body)
                        self.old_edits.append(("E", ts, 0, add, rem))
            elif t == "event_msg" and pt == "token_count":
                ts = _ts(d.get("timestamp") or "")
                if ts is None:
                    return
                rl = p.get("rate_limits")
                if isinstance(rl, dict) and (rl.get("primary") or rl.get("secondary")):
                    self.rate = (ts, rl)
                info = p.get("info")
                if not isinstance(info, dict):
                    return
                tu = info.get("total_token_usage")
                lu = info.get("last_token_usage")
                if isinstance(tu, dict):
                    cur = (tu.get("input_tokens") or 0, tu.get("cached_input_tokens") or 0,
                           tu.get("output_tokens") or 0)
                    if cur == self.prev:
                        return  # duplicate event
                    if sum(cur) < sum(self.prev):  # totals went back: a new/reset counter, take it as a delta
                        dl = cur
                    else:
                        dl = tuple(max(a - b, 0) for a, b in zip(cur, self.prev))
                    self.prev = cur
                elif isinstance(lu, dict):
                    dl = (lu.get("input_tokens") or 0, lu.get("cached_input_tokens") or 0,
                          lu.get("output_tokens") or 0)
                else:
                    return
                inp, cached, out = dl
                if inp + out <= 0:
                    return
                cached = min(max(cached, 0), inp)
                ctx = (lu or {}).get("input_tokens") or 0
                self.recs.append((ts, self.sid, self.model, inp - cached, out, cached, 0, self.pending, None, ctx))
                self.pending = []
        except Exception:
            pass

    def records(self):
        recs = list(self.recs)
        if self.pending and recs:  # trailing tools after the last token_count
            r = recs[-1]
            recs[-1] = r[:7] + (r[7] + self.pending,) + r[8:]
        return recs + (self.new_msgs or self.old_msgs) + (self.new_edits or self.old_edits)


_HEAD = 256  # first bytes of a file, kept to notice a log that was rewritten rather than appended to


def _read_new(path, size, entry, make):
    """Feed the lines appended since the last read to the file's parser. A new file, a smaller file or a
    changed start means it was replaced: start again from byte 0. Only whole lines are read; a line still
    being written stays for the next time."""
    try:
        with open(path, "rb") as f:
            head = f.read(_HEAD)
            if entry is None or size < entry["offset"] or head[:len(entry["head"])] != entry["head"]:
                entry = {"offset": 0, "head": b"", "parser": make(path)}
            if len(entry["head"]) < _HEAD:
                entry["head"] = head
            f.seek(entry["offset"])
            chunk = f.read(max(size - entry["offset"], 0))
    except OSError:
        return entry
    end = chunk.rfind(b"\n") + 1
    rest = chunk[end:]
    if rest.strip():  # last line without a newline: take it only if it is already complete JSON
        try:
            json.loads(rest)
            end = len(chunk)
        except ValueError:
            pass
    for line in chunk[:end].decode("utf8", errors="replace").splitlines():
        entry["parser"].feed(line)
    entry["offset"] += end
    return entry


def codex_rate_limits():
    """Newest rate_limits snapshot in the Codex logs read so far: (timestamp, rate_limits) or None.
    Only this machine's last request: used when the usage API cannot be reached."""
    best = None
    for (src, _), e in _CACHE.items():
        r = getattr(e["parser"], "rate", None) if src == "codex" else None
        if r and (best is None or r[0] > best[0]):
            best = r
    return best


def _files(source, cutoff):
    sub = os.path.join(claude_dir(), "projects") if source == "claude" else os.path.join(codex_home(), "sessions")
    pat = os.path.join(sub, "**", "*.jsonl")
    for p in glob.iglob(pat, recursive=True):
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime >= cutoff:
            yield p, st.st_mtime, st.st_size


def _empty():
    return {"sessions": 0, "requests": 0, "input": 0, "output": 0, "cache_read": 0,
            "cache_write": 0, "total": 0, "tool_calls": 0, "cost_usd": 0.0,
            "top_models": [], "top_tools": [], "unpriced": 0, "messages": 0, "lines_added": 0, "lines_removed": 0}


def stats(source):
    now = time.time()
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    periods = (("today", midnight.timestamp()), ("7d", now - 7 * 86400), ("30d", now - 30 * 86400),
               ("month", midnight.replace(day=1).timestamp()),
               ("year", midnight.replace(month=1, day=1).timestamp()), ("all", 0))
    cutoff = 0  # all time: parsed files are cached by (mtime, size), so only changed files are re-read
    make = _ClaudeParser if source == "claude" else _CodexParser
    seen_files = set()
    allrecs = []
    for p, mt, sz in _files(source, cutoff):
        k = (source, p)
        seen_files.add(k)
        e = _CACHE.get(k)
        if e is None or e["mtime"] != mt or e["size"] != sz:  # new or grown: read only what was appended
            e = _read_new(p, sz, e, make)
            if e is None:  # could not open it this time
                continue
            e["mtime"], e["size"] = mt, sz
            e["recs"] = e["parser"].records()
            _CACHE[k] = e
        allrecs.extend(e["recs"])
    for k in [k for k in _CACHE if k[0] == source and k not in seen_files]:
        del _CACHE[k]
    events = [r for r in allrecs if r[0] == "E"]
    allrecs = sorted((r for r in allrecs if r[0] != "E"), key=lambda r: r[0])
    long_ctx = {(r[1], r[2]) for r in allrecs if r[9] > LONG_CONTEXT_TOKENS}
    out = {}
    for name, start in periods:
        r = _empty()
        sess, models, tools, seen = set(), {}, {}, set()
        for ts, sid, model, i, o, cr, cw, tl, key, _ctx in allrecs:
            if ts < start or ts > now + 3600:
                continue
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            pr = _price(model, (sid, model) in long_ctx)
            cost = ((i * pr[0] + cr * pr[1] + cw * pr[2] + o * pr[3]) / 1e6) if pr else 0.0
            if not pr:
                r["unpriced"] += 1  # cost counted as 0: the estimate is low (Token Watch's "no mapped price")
            sess.add(sid)
            r["requests"] += 1
            r["input"] += i
            r["output"] += o
            r["cache_read"] += cr
            r["cache_write"] += cw
            r["cost_usd"] += cost
            r["tool_calls"] += len(tl)
            for n in tl:
                tools[n] = tools.get(n, 0) + 1
            mm = models.setdefault(model or "unknown", [0, 0.0])
            mm[0] += i + o + cr + cw
            mm[1] += cost
        for _, ts, n, add, rem in events:
            if start <= ts <= now + 3600:
                r["messages"] += n
                r["lines_added"] += add
                r["lines_removed"] += rem
        r["sessions"] = len(sess)
        r["total"] = r["input"] + r["output"] + r["cache_read"] + r["cache_write"]
        r["top_models"] = [{"name": n, "tokens": v[0], "cost": v[1]} for n, v in
                           sorted(models.items(), key=lambda x: (-x[1][1], -x[1][0]))[:3]]
        r["top_tools"] = sorted(tools.items(), key=lambda x: -x[1])[:5]
        out[name] = r
    return out


def _n(v):
    for lim, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= lim:
            x = v / lim
            return ("%.1f" % x if x < 100 else "%.0f" % x) + suf
    return str(int(v))


def _section(title, r):
    L = [title,
         "  Sessions %s  Requests %s  Tools %s" % (_n(r["sessions"]), _n(r["requests"]), _n(r["tool_calls"])),
         "  Total %s  Cost $%.2f" % (_n(r["total"]), r["cost_usd"]),
         "  In %s  Out %s" % (_n(r["input"]), _n(r["output"])),
         "  Cache R %s  W %s" % (_n(r["cache_read"]), _n(r["cache_write"]))]
    if r["top_models"]:
        L.append("  Models:")
        for m in r["top_models"]:
            L.append("   %-18.18s %6s $%.2f" % (m["name"], _n(m["tokens"]), m["cost"]))
    if r["top_tools"]:
        L.append("  Tools: " + ", ".join("%s %s" % (n[:14], _n(c)) for n, c in r["top_tools"]))
    return "\n".join(L)


def render(source):
    try:
        s = stats(source)
    except Exception as e:
        return "usage unavailable: %s" % type(e).__name__
    return _section("Today", s["today"]) + "\n" + _section("Last 7 days", s["7d"])
