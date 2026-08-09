"""Test harness for time-awareness.py v1.2.0 — per-message timestamp
annotation (inlet-only, no outlet), prepended container, valve handling.

Covers the design decided in the Aug 8 fix conversation (Iri): the outlet is
removed; every user message in the request is annotated with ITS OWN timestamp
(DB for saved chats, message-object fallback for temp/API, "now" for the
current message), prepended so the user's words come later in the window.
"""
import asyncio
import importlib.util
import sys
import types

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ---- open_webui stubs (guarded imports in the filter fall back to None;
# ---- stubbing lets us exercise the DB timestamp path) ----
class FakeChats:
    message_map = None  # test sets this: {message_id: {"timestamp": epoch}}

    @classmethod
    async def get_messages_map_by_chat_id(cls, chat_id):
        return cls.message_map


chats_mod = types.ModuleType("open_webui.models.chats")
chats_mod.Chats = FakeChats
chat_id_mod = types.ModuleType("open_webui.utils.chat_id")
chat_id_mod.is_saved_chat_id = lambda cid: not (cid or "").startswith(
    ("temporary:", "local:", "channel:")
)
for name, mod in [
    ("open_webui", types.ModuleType("open_webui")),
    ("open_webui.models", types.ModuleType("open_webui.models")),
    ("open_webui.models.chats", chats_mod),
    ("open_webui.utils", types.ModuleType("open_webui.utils")),
    ("open_webui.utils.chat_id", chat_id_mod),
]:
    sys.modules[name] = mod

spec = importlib.util.spec_from_file_location(
    "time_awareness", "/home/user/open-webui-filters/time-awareness.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

Filter = mod.Filter


def count_details(s):
    return str(s).count('<details type="filters_context">')


def msg(text, role="user", mid=None, ts=None):
    m = {"role": role, "content": text}
    if mid:
        m["id"] = mid
    if ts is not None:
        m["timestamp"] = ts
    return m


def std_body(chat_id="c-1", messages=None):
    if messages is None:
        messages = [
            msg("You are a helpful assistant.", "system", "s0"),
            msg("What's the weather like?", "user", "m1", 1786194000),
            msg("It's sunny.", "assistant", "a1"),
            msg("I like soup.", "user", "m2", 1786208400),
        ]
    return {"messages": messages, "metadata": {"chat_id": chat_id}}


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


TA_BLOCK = '<context id="time_awareness">Saturday 2026-08-08 19:00:00 EDT</context>'
MEM_BLOCK = '<context id="memory_context"><memory_context>[User Memory]…</memory_context></context>'
# Legacy-format container (pre-v1.2.1, carries the uuid attribute) — merging
# into it must keep working. Newly created containers carry <context_end/>
# with no attribute.
INJECTOR_CONTAINER = (
    '<details type="filters_context">\n<summary>Filters context</summary>\n'
    "<!--This context was added by the system to this message, not by the user. Message sent on: -->\n"
    + MEM_BLOCK
    + '\n<context_end uuid="65b58901-1623-4470-a7ce-2d4fd99fd296"/>\n</details>\n'
)


async def main():
    print("== container surgery (position) ==")
    out = mod.add_or_update_filter_context("I like soup.", TA_BLOCK, id="time_awareness", prepend=True)
    check("CREATE prepend: block before user text", out.startswith('<details type="filters_context">') and out.endswith("I like soup.\n"), out[:60])
    check("CREATE prepend: one container", count_details(out) == 1)
    check("CREATE: context_end anchor, no uuid attribute", "<context_end/>" in out and '<context_end uuid="' not in out, out[:120])
    out_a = mod.add_or_update_filter_context("I like soup.", TA_BLOCK, id="time_awareness")
    check("CREATE append (default False) still works", out_a.startswith("I like soup."))
    check("UPDATE same id keeps container position", out_a.startswith("I like soup."))

    merged = mod.add_or_update_filter_context(
        "I like soup.\n" + INJECTOR_CONTAINER, TA_BLOCK, id="time_awareness", prepend=True
    )
    check("merge into existing container: in place, one container", count_details(merged) == 1)
    check("merge: both contexts present", "memory_context" in merged and "time_awareness" in merged)

    amp = "AT&T <soup> & more"
    out3 = mod.add_or_update_filter_context(amp, TA_BLOCK, id="time_awareness", prepend=True)
    check("byte preservation: & and < survive CREATE", amp in out3)

    empty = mod.add_or_update_filter_context("", TA_BLOCK, id="time_awareness", prepend=True)
    check("empty message: container only, no leading junk", empty.startswith('<details type="filters_context">'))

    print("== inlet: per-message timestamps (saved chat, DB map) ==")
    FakeChats.message_map = {
        "m1": {"timestamp": 1786194000},
        "m2": {"timestamp": 1786208400},
    }
    f = Filter()
    f.valves.timezone = "America/Toronto"
    kw = dict(__user__={"id": "u_abc", "valves": Filter.UserValves(enabled=True)})

    out = await f.inlet(std_body(), None, **kw)
    m1, m2 = out["messages"][1], out["messages"][3]
    check("historical user message annotated", "time_awareness" in str(m1["content"]))
    check("current user message annotated", "time_awareness" in str(m2["content"]))
    check("system/assistant untouched", "time_awareness" not in str(out["messages"][0]["content"]) and "time_awareness" not in str(out["messages"][2]["content"]))
    check("one container per message", count_details(m1["content"]) == 1 and count_details(m2["content"]) == 1)
    check("each message gets its own timestamp", "09:00:00" in str(m1["content"]) and "13:00:00" in str(m2["content"]), str(m1["content"])[:80])
    check("block prepended on both", str(m1["content"]).startswith("<details") and str(m2["content"]).startswith("<details"))

    print("== inlet: determinism (cache safety) ==")
    out_a = await f.inlet(std_body(), None, **kw)
    out_b = await f.inlet(std_body(), None, **kw)
    check("same input -> byte-identical output", out_a == out_b)

    print("== inlet: fallbacks ==")
    # temp chat: no DB map -> payload timestamps
    FakeChats.message_map = None
    body = std_body(chat_id="temporary:x")
    out = await f.inlet(body, None, **kw)
    m1, m2 = out["messages"][1], out["messages"][3]
    check("temp chat: payload timestamps used", "time_awareness" in str(m1["content"]) and "09:00:00" in str(m1["content"]))

    # no timestamps anywhere: current message gets "now", historicals skipped
    body = std_body(chat_id="temporary:x", messages=[
        msg("sys", "system"),
        msg("old question", "user", "x1"),
        msg("old answer", "assistant"),
        msg("fresh question", "user", "x2"),
    ])
    out = await f.inlet(body, None, **kw)
    check("no ts: historical skipped, current annotated", "time_awareness" not in str(out["messages"][1]["content"]) and "time_awareness" in str(out["messages"][3]["content"]))

    print("== chat_id resolution + ts coercion ==")
    # __metadata__ is the primary source (server-side metadata)
    FakeChats.message_map = {"m1": {"timestamp": 1786194000}, "m2": {"timestamp": 1786208400}}
    body = std_body()
    del body["metadata"]  # no body metadata at all
    out = await f.inlet(body, None, __user__={"id": "u", "valves": Filter.UserValves(enabled=True)}, __metadata__={"chat_id": "c-1"})
    check("chat_id from __metadata__", "time_awareness" in str(out["messages"][1]["content"]))

    # datetime and numeric-string timestamps coerce to epoch
    import datetime as dt
    FakeChats.message_map = {
        "m1": {"timestamp": dt.datetime(2026, 8, 8, 13, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=-4)))},
        "m2": {"timestamp": "1786208400"},
    }
    out = await f.inlet(std_body(), None, **kw)
    m1, m2 = out["messages"][1], out["messages"][3]
    check("datetime ts coerced", "time_awareness" in str(m1["content"]) and "13:00:00" in str(m1["content"]), str(m1["content"])[:80])
    check("numeric-string ts coerced", "time_awareness" in str(m2["content"]) and "13:00:00" in str(m2["content"]), str(m2["content"])[:80])
    FakeChats.message_map = None

    print("== backend id-stripping: position matching via parent chain ==")
    # The backend's strip_compaction_fields POPS 'id' from every request
    # message before inlets run, and load_messages_from_db strips 'timestamp'
    # — so saved-chat bodies have neither. The filter must match by ordinal
    # against the DB parent chain rebuilt from user_message_id.
    FakeChats.message_map = {
        "m1": {"id": "m1", "role": "user", "timestamp": 1786194000, "parentId": None},
        "a1": {"id": "a1", "role": "assistant", "timestamp": 1786195000, "parentId": "m1"},
        "m2": {"id": "m2", "role": "user", "timestamp": 1786208400, "parentId": "a1"},
    }
    body = std_body()
    for m in body["messages"]:
        m.pop("id", None)  # what the backend does
    body["metadata"].pop("chat_id", None)
    out = await f.inlet(
        body, None,
        __user__={"id": "u", "valves": Filter.UserValves(enabled=True)},
        __metadata__={"chat_id": "c-1", "user_message_id": "m2"},
    )
    m1, m2 = out["messages"][1], out["messages"][3]
    check("id-less historical annotated via chain", "time_awareness" in str(m1["content"]) and "09:00:00" in str(m1["content"]), str(m1["content"])[:80])
    check("id-less current annotated via chain", "time_awareness" in str(m2["content"]) and "13:00:00" in str(m2["content"]), str(m2["content"])[:80])
    check("id-less: one container each", count_details(m1["content"]) == 1 and count_details(m2["content"]) == 1)
    check("id-less: deterministic", (await f.inlet(dict(body), None, __user__={"id": "u", "valves": Filter.UserValves(enabled=True)}, __metadata__={"chat_id": "c-1", "user_message_id": "m2"})) == out)
    FakeChats.message_map = None

    print("== inlet: valve ==")
    FakeChats.message_map = None
    out = await f.inlet(std_body(), None, __user__={"id": "u", "valves": Filter.UserValves(enabled=False)})
    check("user valve disabled (pydantic) -> unchanged", "time_awareness" not in str(out["messages"][-1]["content"]))
    out = await f.inlet(std_body(), None, __user__={"id": "u", "valves": {"enabled": False}})
    check("user valve disabled (dict) -> unchanged", "time_awareness" not in str(out["messages"][-1]["content"]))
    out = await f.inlet(std_body(), None, __user__=None)
    check("__user__ None -> default enabled", "time_awareness" in str(out["messages"][-1]["content"]))
    out = await f.inlet({"messages": []}, None, **kw)
    check("no messages -> unchanged", out == {"messages": []})

    print("== debug_logging valve gates diagnostics ==")
    import logging as _logging

    class Capture(_logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    FakeChats.message_map = {"m1": {"timestamp": 1786194000}, "m2": {"timestamp": 1786208400}}
    capture = Capture()
    capture.setLevel(_logging.INFO)
    mod.LOGGER.addHandler(capture)
    try:
        fv_off = Filter()
        fv_off.valves.timezone = "America/Toronto"
        await fv_off.inlet(std_body(), None, **kw)
        infos_off = [r.getMessage() for r in capture.records if r.levelno >= _logging.INFO]
        check("valve off: no INFO diagnostics", not any(m.startswith("TA: ") for m in infos_off), infos_off[:3])
        capture.records.clear()
        fv_on = Filter()
        fv_on.valves.timezone = "America/Toronto"
        fv_on.valves.debug_logging = True
        await fv_on.inlet(std_body(), None, **kw)
        infos_on = [r.getMessage() for r in capture.records if r.levelno >= _logging.INFO]
        check("valve on: diagnostics at INFO", any("TA: annotated idx=3" in m for m in infos_on), infos_on[:3])
    finally:
        mod.LOGGER.removeHandler(capture)
    FakeChats.message_map = None

    print("== multimodal ==")
    body = std_body(messages=[
        msg("sys", "system"),
        {"role": "user", "id": "m9", "timestamp": 1786194000, "content": [
            {"type": "text", "text": "Tell me the time."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]},
    ])
    FakeChats.message_map = {"m9": {"timestamp": 1786194000}}
    out = await f.inlet(body, None, **kw)
    parts = out["messages"][1]["content"]
    check("list kept", isinstance(parts, list) and len(parts) == 2)
    check("text part carries block (prepended)", parts[0]["text"].startswith("<details") and "time_awareness" in parts[0]["text"])
    check("image part untouched", parts[1]["type"] == "image_url")

    print("== escaping ==")
    f2 = Filter()
    f2.valves.format_string = "%A <b>&</b>"
    FakeChats.message_map = {"m1": {"timestamp": 1786194000}, "m2": {"timestamp": 1786208400}}
    out = await f2.inlet(std_body(), None, **kw)
    content = str(out["messages"][3]["content"])
    check("escape: < > & escaped in container content", "&lt;b&gt;&amp;&lt;/b&gt;" in content)

    print("== no outlet ==")
    check("outlet handler removed", not hasattr(Filter(), "outlet"))
    check("no _queries state", not hasattr(Filter(), "_queries"))

    print("== cross-filter interop (memory injector) ==")
    inj_spec = importlib.util.spec_from_file_location(
        "memory_injector_user_message", "/home/user/open-webui-filters/memory-injector-user-message.py"
    )
    inj = importlib.util.module_from_spec(inj_spec)
    inj_spec.loader.exec_module(inj)
    ta_out = mod.add_or_update_filter_context("I like soup.", TA_BLOCK, id="time_awareness", prepend=True)
    merged = inj.add_or_update_filter_context(ta_out, "<memory_context>[User Memory]…</memory_context>", id="memory_context", prepend=True)
    check("injector merges into prepended TA container: one container", count_details(merged) == 1, merged)
    check("injector merges: both contexts, user text last", "time_awareness" in merged and "memory_context" in merged and merged.endswith("I like soup.\n"))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run(main()))
