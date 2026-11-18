"""Local token usage stats for Claude Code and Codex logs (stdlib only, read-only).
Prices (USD per 1M tokens: input, cached/read, cache-write, output) from Token Watch."""
import json, os, glob, time
from datetime import datetime

PRICES = {'gpt-5': (1.25, 0.125, 1.25, 10.0), 'gpt-5-codex': (1.25, 0.125, 1.25, 10.0), 'gpt-5-codex-mini': (0.25, 0.025, 0.25, 2.0), 'gpt-5-mini': (0.25, 0.025, 0.25, 2.0), 'gpt-5-nano': (0.05, 0.005, 0.05, 0.4), 'gpt-5-chat-latest': (1.25, 0.125, 1.25, 10.0), 'gpt-5-pro': (15.0, 0.0, 15.0, 120.0), 'gpt-5.1': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-chat-latest': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-codex': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-codex-max': (1.25, 0.125, 1.25, 10.0), 'gpt-5.1-codex-mini': (0.25, 0.025, 0.25, 2.0), 'gpt-5.2': (1.75, 0.175, 1.75, 14.0), 'gpt-5.2-chat-latest': (1.75, 0.175, 1.75, 14.0), 'gpt-5.2-codex': (1.75, 0.175, 1.75, 14.0), 'gpt-5.2-pro': (21.0, 0.0, 21.0, 168.0), 'gpt-5.3-chat-latest': (1.75, 0.175, 1.75, 14.0), 'gpt-5.3-codex': (1.75, 0.175, 1.75, 14.0), 'gpt-5.3-codex-spark': (2.0, 1.0, 2.0, 8.0), 'gpt-5.4': (2.5, 0.25, 2.5, 15.0), 'gpt-5.4-mini': (0.75, 0.075, 0.75, 4.5), 'gpt-5.4-pro': (30.0, 0.0, 30.0, 180.0), 'gpt-5.5': (5.0, 0.5, 5.0, 30.0), 'gpt-5.5-pro': (30.0, 0.0, 30.0, 180.0), 'gpt-5.6-sol': (4.0, 0.4, 5.0, 20.0), 'gpt-5.6-cyber': (12.5, 1.25, 15.625, 75.0), 'gpt-5.6-terra': (2.0, 0.2, 2.5, 12.0), 'gpt-5.6-luna': (0.2, 0.02, 0.25, 1.2), 'gpt-6-astra': (10.0, 1.0, 12.5, 50.0), 'gpt-6-sol': (2.0, 0.2, 2.5, 10.0), 'gpt-6-luna': (0.1, 0.01, 0.125, 0.5), 'gpt-6.1-sol': (2.0, 0.1, 2.5, 10.0), 'gpt-6.1-terra': (1.0, 0.05, 1.25, 5.0), 'gpt-6-cyber': (20.0, 2.0, 25.0, 100.0), 'gpt-6-rosalind': (15.0, 1.5, 18.75, 75.0), 'gpt-6-astra-law': (10.0, 1.0, 10.0, 50.0), 'gpt-4.1': (2.0, 0.5, 2.0, 8.0), 'gpt-4.1-mini': (0.4, 0.1, 0.4, 1.6), 'gpt-4.1-nano': (0.1, 0.025, 0.1, 0.4), 'gpt-4o': (2.5, 1.25, 2.5, 10.0), 'gpt-4o-mini': (0.15, 0.075, 0.15, 0.6), 'o3-mini': (1.1, 0.55, 1.1, 4.4), 'o4-mini': (1.1, 0.275, 1.1, 4.4), 'claude-opus-5.5': (4.0, 0.2, 5.0, 20.0), 'claude-opus-5-5': (4.0, 0.2, 5.0, 20.0), 'claude-opus-5': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.8': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-8': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.7': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-7': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.6': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-6': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.5': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4-5': (5.0, 0.5, 6.25, 25.0), 'claude-opus-4.1': (15.0, 1.5, 18.75, 75.0), 'claude-opus-4-1': (15.0, 1.5, 18.75, 75.0), 'claude-opus-4': (15.0, 1.5, 18.75, 75.0), 'claude-fable-5.2': (8.0, 0.2, 10.0, 40.0), 'claude-fable-5-2': (8.0, 0.2, 10.0, 40.0), 'claude-fable-5.1': (10.0, 0.25, 12.5, 50.0), 'claude-fable-5-1': (10.0, 0.25, 12.5, 50.0), 'claude-fable-5': (10.0, 1.0, 12.5, 50.0), 'claude-mythos-5.2': (8.0, 0.2, 10.0, 40.0), 'claude-mythos-5-2': (8.0, 0.2, 10.0, 40.0), 'claude-mythos-5.1': (10.0, 0.25, 12.5, 50.0), 'claude-mythos-5-1': (10.0, 0.25, 12.5, 50.0), 'claude-mythos-5': (10.0, 1.0, 12.5, 50.0), 'claude-sonnet-5.5': (2.0, 0.1, 2.5, 10.0), 'claude-sonnet-5-5': (2.0, 0.1, 2.5, 10.0), 'claude-sonnet-5': (2.0, 0.2, 2.5, 10.0), 'claude-sonnet-4.6': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4-6': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4.5': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4-5': (3.0, 0.3, 3.75, 15.0), 'claude-sonnet-4': (3.0, 0.3, 3.75, 15.0), 'claude-haiku-5.5': (0.1, 0.01, 0.125, 0.5), 'claude-haiku-5-5': (0.1, 0.01, 0.125, 0.5), 'claude-haiku-4.5': (1.0, 0.1, 1.25, 5.0), 'claude-haiku-4-5': (1.0, 0.1, 1.25, 5.0), 'claude-haiku-3.5': (0.8, 0.08, 1.0, 4.0), 'claude-haiku-3-5': (0.8, 0.08, 1.0, 4.0)}
_CACHE = {}  # (source, path) -> (mtime, size, records)


# Token Watch's "-long-context" tier, per 1M (input, cached, cache_write, output). A session/model whose request
# context ever exceeds LONG_CONTEXT_TOKENS is priced at this tier for the whole session, as Token Watch does.
LONG_CONTEXT_TOKENS = 272_000
LONG_PRICES = {
    "gpt-6-luna": (0.2, 0.02, 0.25, 0.75), "gpt-6-sol": (4.0, 0.4, 5.0, 15.0), "gpt-6.1-sol": (4.0, 0.2, 5.0, 15.0),
    "gpt-6.1-terra": (2.0, 0.1, 2.5, 7.5), "gpt-6-astra": (20.0, 2.0, 25.0, 75.0), "gpt-6-cyber": (40.0, 4.0, 50.0, 150.0),
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


# record: (ts, sid, model, input, output, cache_read, cache_write, tools_list, dedup_key, context_tokens)
def _parse_claude(path):
    recs = []
    sid = os.path.basename(path)
    try:
        with open(path, encoding="utf8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line and '"type":"user"' not in line:
                    continue
                try:
                    d = json.loads(line)
                    m = d.get("message")
                    if d.get("type") == "user" and isinstance(m, dict):
                        ev = _claude_user_event(d, m)
                        if ev:
                            recs.append(ev)
                        continue
                    if d.get("type") != "assistant" or not isinstance(m, dict):
                        continue
                    u = m.get("usage")
                    if not isinstance(u, dict):
                        continue
                    ts = _ts(d.get("timestamp") or "")
                    if ts is None:
                        continue
                    model = m.get("model") or ""
                    if model.startswith("<"):
                        continue
                    tools = [b.get("name") or "unknown" for b in (m.get("content") or [])
                             if isinstance(b, dict) and b.get("type") == "tool_use"]
                    key = (m.get("id"), d.get("requestId")) if m.get("id") and d.get("requestId") else None
                    recs.append((ts, d.get("sessionId") or sid, model,
                                 u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                                 u.get("cache_read_input_tokens") or 0,
                                 u.get("cache_creation_input_tokens") or 0, tools, key,
                                 0))  # Token Watch measures context only for Codex, so no long-context tier here
                except Exception:
                    continue
    except OSError:
        pass
    return recs


def _parse_codex(path):
    recs = []
    sid = os.path.basename(path)
    model = "unknown"
    prev = (0, 0, 0)  # input, cached, output (cumulative)
    pending = []
    try:
        with open(path, encoding="utf8", errors="replace") as f:
            for line in f:
                if not ('"token_count"' in line or '"turn_context"' in line or "tool_call" in line
                        or '"function_call"' in line or '"session_meta"' in line
                        or '"thread_settings_applied"' in line):
                    continue
                try:
                    d = json.loads(line)
                    t = d.get("type")
                    p = d.get("payload")
                    if not isinstance(p, dict):
                        continue
                    pt = p.get("type")
                    if t == "session_meta":
                        sid = p.get("id") or sid
                    elif t == "turn_context":
                        model = p.get("model") or model
                    elif t == "event_msg" and pt == "thread_settings_applied":
                        model = (p.get("thread_settings") or {}).get("model") or model
                    elif t == "response_item" and pt in ("function_call", "custom_tool_call"):
                        pending.append(p.get("name") or "unknown")
                    elif t == "event_msg" and pt == "token_count":
                        info = p.get("info")
                        if not isinstance(info, dict):
                            continue
                        tu = info.get("total_token_usage")
                        ts = _ts(d.get("timestamp") or "")
                        if not isinstance(tu, dict) or ts is None:
                            continue
                        cur = (tu.get("input_tokens") or 0, tu.get("cached_input_tokens") or 0,
                               tu.get("output_tokens") or 0)
                        if cur == prev:
                            continue  # duplicate event
                        if cur[0] < prev[0] or cur[2] < prev[2]:
                            dl = cur
                        else:
                            dl = tuple(a - b for a, b in zip(cur, prev))
                        prev = cur
                        inp, cached, out = dl
                        cached = min(max(cached, 0), inp)
                        ctx = (info.get("last_token_usage") or {}).get("input_tokens") or 0
                        recs.append((ts, sid, model, inp - cached, out, cached, 0, pending, None, ctx))
                        pending = []
                except Exception:
                    continue
    except OSError:
        pass
    if pending and recs:  # trailing tools after the last token_count
        r = recs[-1]
        recs[-1] = r[:7] + (r[7] + pending,) + r[8:]
    return recs


def _files(source, cutoff):
    home = os.path.expanduser("~")
    sub = (".claude", "projects") if source == "claude" else (".codex", "sessions")
    pat = os.path.join(home, *sub, "**", "*.jsonl")
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
            "top_models": [], "top_tools": [], "messages": 0, "lines_added": 0, "lines_removed": 0}


def stats(source):
    now = time.time()
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    periods = (("today", midnight.timestamp()), ("7d", now - 7 * 86400), ("30d", now - 30 * 86400),
               ("year", midnight.replace(month=1, day=1).timestamp()), ("all", 0))
    cutoff = 0  # all time: parsed files are cached by (mtime, size), so only changed files are re-read
    parse = _parse_claude if source == "claude" else _parse_codex
    seen_files = set()
    allrecs = []
    for p, mt, sz in _files(source, cutoff):
        k = (source, p)
        seen_files.add(k)
        c = _CACHE.get(k)
        if c is None or c[0] != mt or c[1] != sz:
            c = (mt, sz, parse(p))
            _CACHE[k] = c
        allrecs.extend(c[2])
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
