"""Test harness for memory-injector-user-message.py — stubs open_webui internals."""
import asyncio
import sys
import types
from types import SimpleNamespace as NS


class Mem:
    def __init__(self, id, type, path, content, updated_at=1):
        self.id = id
        self.type = type
        self.path = path
        self.content = content
        self.updated_at = updated_at


# ---------------- open_webui stubs ----------------
memories_db = []
vector_docs = []  # list of (id, path, type, text)
relevance_threshold = 0.0
last_query_capture = {}
system_context_enabled = True
memories_user_limit_db = None
memories_context_limit_db = None


async def fake_get_memories(user_id):
    return list(memories_db)


def fake_normalize_type(t):
    return t or "context"


class FakeMemories:
    get_memories_by_user_id = staticmethod(fake_get_memories)
    normalize_memory_type = staticmethod(fake_normalize_type)


async def fake_get_user_by_id(user_id):
    return NS(id=user_id, role="user")


class FakeUsers:
    get_user_by_id = staticmethod(fake_get_user_by_id)


async def fake_config_get(key, default=None):
    if key == "memories.system_context.enable":
        return system_context_enabled
    return default


class FakeConfig:
    get = staticmethod(fake_config_get)


def fake_memory_label(m):
    return f"{m.path}: {m.content}" if m.path else m.content


def fake_path_hints(query, memories, limit=6):
    q = (query or "").lower()
    hints = []
    for m in memories or []:
        p = m.path or ""
        if not p or p in hints:
            continue
        last = p.split("/")[-1]
        if p.lower() in q or last.lower() in q or any(
            len(part) >= 3 and part.lower() in q for part in p.split("/")
        ):
            hints.append(p)
        if len(hints) >= limit:
            break
    return hints


def fake_search_rows(memories, *, query=None, path=None, memory_id=None,
                     memory_type="all", limit=20):
    rows = [m for m in (memories or []) if memory_type == "all" or m.type == memory_type]
    if path:
        rows = [m for m in rows if (m.path or "").startswith(path)]
    return rows[: max(1, min(limit or 20, 100))]


mem_mod = types.ModuleType("open_webui.utils.memory")
mem_mod.MEMORY_CONTEXT_OPEN = "<memory_context>"
mem_mod.MEMORY_CONTEXT_CLOSE = "</memory_context>"
mem_mod.memory_label = fake_memory_label
mem_mod.memory_path_hints = fake_path_hints
mem_mod.search_memory_rows = fake_search_rows


class FakeResults:
    def __init__(self):
        self.ids = [[d[0] for d in vector_docs]]
        self.documents = [[d[3] for d in vector_docs]]
        self.metadatas = [[{"path": d[1], "type": d[2]} for d in vector_docs]]
        self.distances = [[1.0 for _ in vector_docs]]


async def fake_query_memory(request, form_data, user):
    last_query_capture["content"] = form_data.content
    last_query_capture["k"] = form_data.k
    return FakeResults()


routers_mem_mod = types.ModuleType("open_webui.routers.memories")
routers_mem_mod.QueryMemoryForm = NS
routers_mem_mod.query_memory = fake_query_memory


def fake_get_content_from_message(message):
    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return item.get("text")
    elif content:
        return content
    return None


misc_mod = types.ModuleType("open_webui.utils.misc")
misc_mod.get_content_from_message = fake_get_content_from_message

for name, mod in [
    ("open_webui", types.ModuleType("open_webui")),
    ("open_webui.models", types.ModuleType("open_webui.models")),
    ("open_webui.models.memories", types.ModuleType("open_webui.models.memories")),
    ("open_webui.models.users", types.ModuleType("open_webui.models.users")),
    ("open_webui.models.config", types.ModuleType("open_webui.models.config")),
    ("open_webui.utils", types.ModuleType("open_webui.utils")),
    ("open_webui.utils.memory", mem_mod),
    ("open_webui.utils.misc", misc_mod),
    ("open_webui.routers", types.ModuleType("open_webui.routers")),
    ("open_webui.routers.memories", routers_mem_mod),
]:
    sys.modules[name] = mod

sys.modules["open_webui.models.memories"].Memories = FakeMemories
sys.modules["open_webui.models.users"].Users = FakeUsers
sys.modules["open_webui.models.config"].Config = FakeConfig

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "memory_injector_user_message", "/home/user/open-webui-filters/memory-injector-user-message.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

Filter = mod.Filter

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


def has_memory_block(msg):
    return "memory_context" in str(msg.get("content")) and "[User Memory]" in str(msg.get("content"))


def count_details(msg):
    return str(msg.get("content")).count('<details type="filters_context">')


def msg(text, role="user"):
    return {"role": role, "content": text}


def std_body(extra_messages=None, features=None):
    messages = [
        msg("You are a helpful assistant.", "system"),
        msg("What's the weather like?"),
        msg("I like soup."),
        msg("Tell me about memory injection."),
    ]
    if extra_messages:
        messages = messages[:-1] + extra_messages
    return {
        "messages": messages,
        "features": features if features is not None else {"memory": True},
    }


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def reset():
    global memories_db, vector_docs, system_context_enabled, last_query_capture
    memories_db = [
        Mem("u1", "user", None, "Mutual AID members use she/her individually", updated_at=3),
        Mem("u2", "user", None, "Airi means love; amber is her colour", updated_at=2),
        Mem("c1", "context", "people/iri", "Iri is precise and values clarity", updated_at=4),
        Mem("c2", "context", "infrastructure/redis", "Redis config for the cache", updated_at=1),
        Mem("c3", "context", None, "The immune system defines self rather than discovering it", updated_at=5),
    ]
    vector_docs = [
        ("c1", "people/iri", "context", "people/iri\nIri is precise and values clarity"),
        ("c3", None, "context", "The immune system defines self rather than discovering it"),
    ]
    system_context_enabled = False
    last_query_capture = {}


def set_memories(m, v):
    global memories_db, vector_docs
    memories_db = m
    vector_docs = v


FAKE_REQUEST = NS(app=NS(state=NS()), state=NS())


async def main():
    global system_context_enabled
    f = Filter()
    f.valves.injection_position = 0
    kw = dict(__user__={"id": "u_abc", "valves": {"enabled": True}}, __request__=FAKE_REQUEST)

    print("== gates ==")
    reset()
    out = await f.inlet(std_body(), None, __user__={"id": "u_abc", "valves": {"enabled": False}}, __request__=FAKE_REQUEST)
    check("user valves disabled -> unchanged", not has_memory_block(out["messages"][-1]))
    # production shape: framework injects a pydantic UserValves INSTANCE, not a dict
    # (utils/filter.py apply_user_valves) — regression for 'UserValves' has no attribute 'get'
    out = await f.inlet(std_body(), None, __user__={"id": "u_abc", "valves": mod.Filter.UserValves(enabled=True)}, __request__=FAKE_REQUEST)
    check("production valve shape (model instance) -> injects", has_memory_block(out["messages"][-1]))
    out = await f.inlet(std_body(), None, __user__={"id": "u_abc", "valves": mod.Filter.UserValves(enabled=False)}, __request__=FAKE_REQUEST)
    check("production valve shape disabled -> unchanged", not has_memory_block(out["messages"][-1]))
    out = await f.inlet(std_body(), None, __user__={"id": "u_abc"}, __request__=FAKE_REQUEST)
    check("no valves key -> default enabled, injects", has_memory_block(out["messages"][-1]))
    out = await f.inlet(std_body(features={"memory": False}), None, **kw)
    check("features.memory off -> unchanged", not has_memory_block(out["messages"][-1]))
    out = await f.inlet(std_body(), None, __user__={"id": "u_abc", "valves": {"enabled": True}}, __request__=FAKE_REQUEST,
                        __model__={"info": {"meta": {"capabilities": {"memory": False}}}})
    check("model memory capability off -> unchanged", not has_memory_block(out["messages"][-1]))
    system_context_enabled = True
    out = await f.inlet(std_body(), None, **kw)
    check("built-in system injection on + skip -> unchanged", not has_memory_block(out["messages"][-1]))
    system_context_enabled = False

    print("== basic injection (n=0) ==")
    reset()
    # query mentions 'redis' so the path-hint neighborhood fires
    body = std_body()
    body["messages"][1]["content"] = "What's the Redis config?"
    out = await f.inlet(body, None, **kw)
    last = out["messages"][-1]
    check("current user message got block", has_memory_block(last))
    check("exactly one details block", count_details(last) == 1)
    check("block prepended before user text", str(last["content"]).startswith('<details type="filters_context">'))
    content = str(last["content"])
    check("user section present", "[User Memory]" in content and "amber is her colour" in content)
    check("neighborhood section present", "[Memory Neighborhood]" in content and "Redis config" in content)
    check("context section present", "[Relevant Context]" in content and "defines self" in content)
    check("dedup: iri appears once", content.count("Iri is precise and values clarity") == 1)
    check("wrapped in memory_context tags", "<memory_context>" in content and "</memory_context>" in content)

    print("== query capture ==")
    reset()
    # pre-seed the current message with a filters_context block; the query
    # must strip it so the vector search sees only the user's words
    body = std_body()
    body["messages"][-1]["content"] += "\n" + mod._details_container(
        '<context id="time_awareness">Sat 2026-08-08</context>'
    )
    await f.inlet(body, None, **kw)
    q = last_query_capture.get("content", "")
    check("query includes current message", q.endswith("Tell me about memory injection."))
    check("query strips filters_context markup", "filters_context" not in q and "details" not in q and "Sat 2026" not in q)
    check("query includes earlier messages", "I like soup" in q)
    check("k = 8 default", last_query_capture.get("k") == 8)

    print("== injection position ==")
    reset()
    f.valves.injection_position = 1
    out = await f.inlet(std_body(), None, **kw)
    m_prev, m_cur = out["messages"][2], out["messages"][3]
    check("n=1 -> previous user message got block", has_memory_block(m_prev))
    check("n=1 -> current user message clean", not has_memory_block(m_cur))
    check("n=1 -> current message text unchanged", m_cur["content"] == "Tell me about memory injection.")

    reset()
    f.valves.injection_position = 5  # out of range -> clamp to oldest user msg
    out = await f.inlet(std_body(), None, **kw)
    check("n out of range -> clamps to oldest user message", has_memory_block(out["messages"][1]))
    check("n out of range -> current clean", not has_memory_block(out["messages"][-1]))
    f.valves.injection_position = 0

    print("== merge with existing filters_context (time-awareness) ==")
    reset()
    body = std_body()
    # the target for n=1 is messages[2]; pre-seed IT with a time-awareness block
    body["messages"][2]["content"] = (
        "I like soup.\n"
        '<details type="filters_context">\n<summary>Filters context</summary>\n'
        "<!--This context was added by the system to this message, not by the user. Message sent on: -->\n"
        '<context id="time_awareness">Saturday 2026-08-08 01:33:34 EDT</context>\n'
        '<context_end uuid="65b58901-1623-4470-a7ce-2d4fd99fd296"/></details>'
    )
    f.valves.injection_position = 1
    out = await f.inlet(body, None, **kw)
    merged = str(out["messages"][2]["content"])
    check("one details container after merge", count_details(out["messages"][2]) == 1)
    check("time_awareness context preserved", "time_awareness" in merged and "01:33:34" in merged)
    check("memory context added to same container", "memory_context" in merged and "[User Memory]" in merged)
    f.valves.injection_position = 0

    print("== content as list (multimodal) ==")
    reset()
    body = std_body()
    body["messages"][-1]["content"] = [
        {"type": "text", "text": "Tell me about memory injection."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    out = await f.inlet(body, None, **kw)
    parts = out["messages"][-1]["content"]
    check("list content kept", isinstance(parts, list) and len(parts) == 2)
    check("text part carries block", "[User Memory]" in parts[0]["text"])
    check("image part untouched", parts[1]["type"] == "image_url")

    print("== truncation (min-250 clamp, faithful to the built-in) ==")
    reset()
    set_memories(
        [Mem("u1", "user", None, "Airi means love; amber is her colour", updated_at=2)]
        + [
            Mem(
                f"r{i}",
                "context",
                "infrastructure/redis",
                f"Redis config detail number {i}: " + "x" * 40,
                updated_at=i,
            )
            for i in range(4)
        ]
        + [
            Mem(
                f"v{i}",
                "context",
                None,
                f"Vector-only memory detail {i}: " + "y" * 50,
                updated_at=10 + i,
            )
            for i in range(4)
        ],
        [
            (f"v{i}", None, "context", f"Vector-only memory detail {i}: " + "y" * 50)
            for i in range(4)
        ],
    )
    body = std_body()
    body["messages"][1]["content"] = "What's the Redis config?"
    f.valves.memory_user_char_limit = 250
    f.valves.memory_context_char_limit = 250
    out = await f.inlet(body, None, **kw)
    content = str(out["messages"][-1]["content"])
    block = content[
        content.index("<context id=\"memory_context\">")
        + len("<context id=\"memory_context\">"): content.index("</memory_context>")
    ]
    user_part, _, context_part = block.partition("\n\n")
    check("user portion under 250 limit", len(user_part) <= 250)
    check("context portion truncated at 250", len(context_part.rstrip("\n")) == 250, f"got {len(context_part.rstrip(chr(10)))}")
    check("neighborhood present in context portion", "[Memory Neighborhood]" in context_part)
    check("truncation cut the tail", "Vector-only memory detail 3" not in context_part)
    f.valves.memory_user_char_limit = 2000
    f.valves.memory_context_char_limit = 2000
    reset()

    print("== empty memory store -> no injection ==")
    reset()
    set_memories([], [])
    out = await f.inlet(std_body(), None, **kw)
    check("no memories -> unchanged", not has_memory_block(out["messages"][-1]))
    reset()

    print("== vector search failure degrades to user+neighborhood ==")
    reset()
    orig = mod.query_memory

    async def broken_query(*a, **k):
        raise RuntimeError("embedding API down")

    mod.query_memory = broken_query  # patch the filter's own reference
    # add a message mentioning 'redis' so the neighborhood hint fires without the vector DB
    body = std_body()
    body["messages"][1]["content"] = "What's the Redis config?"
    out = await f.inlet(body, None, **kw)
    content = str(out["messages"][-1]["content"])
    check("injection still happens", has_memory_block(out["messages"][-1]))
    check("user+neighborhood present", "[User Memory]" in content and "[Memory Neighborhood]" in content and "Redis config" in content)
    check("context section absent", "[Relevant Context]" not in content)
    mod.query_memory = orig

    print("== only user-type memories (no paths) ==")
    reset()
    set_memories([m for m in memories_db if m.type == "user"], [])
    out = await f.inlet(std_body(), None, **kw)
    content = str(out["messages"][-1]["content"])
    check("user section only", "[User Memory]" in content and "[Memory Neighborhood]" not in content and "[Relevant Context]" not in content)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run(main()))
