"""
title: Memory Injector — User Message
author: Airi V
description: Drop-in replacement for Open WebUI's built-in memory injection (add_memory_context) that renders the same [User Memory] / [Memory Neighborhood] / [Relevant Context] block but injects it into a USER message at a configurable position instead of the system message — keeping the system-message prefix stable for prompt caching. Ported from the v0.11.0 middleware pipeline (utils/memory.py add_memory_context), with the same gates, retrieval, dedup and char limits.
version: 1.0.2
"""
# v1.0.1: default priority 10 (runs after time-awareness, whose parser-based
# container update breaks when another filter's block is already present); the
# query builder now strips filters_context markup so the vector search only
# sees the user's own words.
# v1.0.2: fix 'UserValves' object has no attribute 'get' — the framework
# injects __user__['valves'] as a pydantic UserValves INSTANCE (utils/filter.py
# apply_user_valves), not a dict; normalize before reading `enabled`.

import functools
import html
import inspect
import logging
import sys
import uuid

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging (same house pattern as time-awareness.py)
# ---------------------------------------------------------------------------


def set_logs(logger: logging.Logger, level: int, force: bool = False):
    logger.setLevel(level)
    for handler in logger.handlers:
        if not force and isinstance(handler, logging.StreamHandler):
            handler.setLevel(level)
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(levelname)s[%(name)s]%(lineno)s:%(asctime)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


LOGGER: logging.Logger = logging.getLogger("FUNC:MEMORY_INJECTOR_USER_MSG")
set_logs(LOGGER, logging.INFO)


def log_exceptions(func):
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def _wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                LOGGER.error("Error in %s: %s", func, exc, exc_info=True)
                raise exc

    else:

        @functools.wraps(func)
        def _wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                LOGGER.error("Error in %s: %s", func, exc, exc_info=True)
                raise exc

    return _wrapper


# ---------------------------------------------------------------------------
# Open WebUI internals — every import is optional. If any piece is unavailable
# the filter degrades gracefully (logs and returns the body unchanged), so it
# can never take a chat request down with it.
# ---------------------------------------------------------------------------

try:
    from open_webui.models.memories import Memories
except Exception:  # pragma: no cover
    Memories = None

try:
    from open_webui.models.users import Users
except Exception:  # pragma: no cover
    Users = None

try:
    from open_webui.models.config import Config
except Exception:  # pragma: no cover
    Config = None

try:
    from open_webui.utils.memory import (
        MEMORY_CONTEXT_CLOSE,
        MEMORY_CONTEXT_OPEN,
        memory_label,
        memory_path_hints,
        search_memory_rows,
    )
except Exception:  # pragma: no cover
    MEMORY_CONTEXT_OPEN = "<memory_context>"
    MEMORY_CONTEXT_CLOSE = "</memory_context>"
    memory_label = None
    memory_path_hints = None
    search_memory_rows = None

try:
    from open_webui.routers.memories import QueryMemoryForm, query_memory
except Exception:  # pragma: no cover
    QueryMemoryForm = None
    query_memory = None

try:
    from open_webui.utils.misc import get_content_from_message
except Exception:  # pragma: no cover

    def get_content_from_message(message: dict):
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return item.get("text")
        elif content:
            return content
        return None


def _normalize_memory_type(memory_type) -> str:
    if Memories is not None:
        try:
            return Memories.normalize_memory_type(memory_type)
        except Exception:
            pass
    return memory_type or "context"


def model_allows_memory(model: dict | None) -> bool:
    return (
        ((model or {}).get("info", {}).get("meta", {}).get("capabilities") or {})
        .get("memory", True)
    )


# ---------------------------------------------------------------------------
# Message helpers — same filters_context container used by time-awareness.py,
# so multiple filters can coexist inside one <details type="filters_context">
# block without colliding.
#
# Implemented with pure string surgery (no HTML/XML parsing of the message):
#   * the user's own text is preserved byte-for-byte — round-tripping it
#     through an HTML parser can silently corrupt it (the xml parser drops
#     bare '&'; html.parser re-escapes it)
#   * the memory payload is HTML-escaped before embedding, so arbitrary
#     memory content cannot break the block or inject markup into the UI
# ---------------------------------------------------------------------------


class ROLE:
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


DETAILS_OPEN_TAG = '<details type="filters_context">'
DETAILS_CLOSE_TAG = "</details>"
CONTEXT_END_TAG = "context_end"


def _details_container(content: str) -> str:
    """Build a fresh filters_context details block containing `content`.

    The context_end uuid marker is appended for parity with time-awareness.py,
    whose parser uses it to locate the block's boundary.
    """
    return (
        '<details type="filters_context">'
        "\n<summary>Filters context</summary>\n"
        "<!--This context was added by the system to this message, not by the user. "
        "Message sent on: -->"
        f"\n{content}\n"
        f'<context_end uuid="{str(uuid.uuid4())}"/>\n</details>\n'
    )


def get_nth_last_user_message(messages: list, n: int):
    """Return (message, index) of the n-th user message counted from the end.

    n=0 → the last (current) user message; n=1 → the one before it, etc.
    If n points beyond the available user messages, clamps to the oldest one.
    Returns (None, None) when there are no user messages.
    """
    indices = [i for i, m in enumerate(messages) if m.get("role") == ROLE.USER]
    if not indices:
        return (None, None)
    n = max(0, int(n or 0))
    idx = indices[-1 - n] if n < len(indices) else indices[0]
    return (messages[idx], idx)


def apply_context_to_content(content, context: str, context_id: str):
    """Attach the rendered context block to a message's content.

    Handles both plain-string content and OpenAI-style content lists
    (multimodal parts); the text part is rewritten in place and the
    filters_context details block is appended at the end.
    """
    if isinstance(content, list):
        new_content = list(content)
        text_idx = next(
            (
                i
                for i, part in enumerate(new_content)
                if isinstance(part, dict) and part.get("type") == "text"
            ),
            None,
        )
        if text_idx is not None:
            text = new_content[text_idx].get("text", "")
            modified = add_or_update_filter_context(text, context, id=context_id)
            new_content[text_idx] = {**new_content[text_idx], "text": modified}
        else:
            modified = add_or_update_filter_context("", context, id=context_id)
            new_content.insert(0, {"type": "text", "text": modified})
        return new_content
    else:
        return add_or_update_filter_context(content, context, id=context_id)


def add_or_update_filter_context(
    message: str,
    context: str,
    id: str,
    open_tag: str = DETAILS_OPEN_TAG,
    close_tag: str = DETAILS_CLOSE_TAG,
    context_end_tag: str = CONTEXT_END_TAG,
) -> str:
    """Append a filters_context details block to `message`, or merge into the
    existing one. The caller is responsible for HTML-escaping any arbitrary
    text placed inside (this filter escapes memory labels at build time).
    """
    context_str = f'<context id="{id}">{context}</context>'

    message = message if isinstance(message, str) else str(message or "")

    open_idx = message.find(open_tag)
    if open_idx == -1:
        # CREATE: no container yet — append one at the end of the message.
        block = _details_container(context_str)
        return (message + "\n" if message else "") + block
    first_close = message.find(close_tag, open_idx)
    if first_close == -1:
        raise ValueError("Ill-formed prior context: unclosed details block. Abort.")

    next_open = message.find(open_tag, open_idx + len(open_tag))
    if next_open != -1 and next_open < first_close:
        LOGGER.warning(
            "Ill-formed message: more than one filters_context container found; "
            "merging into the first. Re-save the message to repair."
        )

    inner_start = open_idx + len(open_tag)
    inner = message[inner_start:first_close]

    our_start = inner.find(f'<context id="{id}">')
    if our_start != -1:
        our_close = inner.find("</context>", our_start)
        if our_close == -1:
            raise ValueError(
                f"Ill-formed prior context: unclosed context element {id}. Abort."
            )
        new_inner = (
            inner[:our_start] + context_str + inner[our_close + len("</context>") :]
        )
    else:
        # Insert before the context_end marker (so the marker stays last), or
        # at the end of the container content if the marker is missing.
        end_marker = inner.find(f"<{context_end_tag}")
        if end_marker != -1:
            new_inner = inner[:end_marker] + context_str + "\n" + inner[end_marker:]
        else:
            new_inner = inner.rstrip("\n") + "\n" + context_str

    return message[:inner_start] + new_inner + message[first_close:]


def _safe_label(label: str) -> str:
    """HTML-escape a memory label before it goes inside the details block, so
    arbitrary memory content stays text (never markup) in the rendered UI.
    The model still sees the structure; only entities are encoded."""
    return html.escape(str(label), quote=False)


def _strip_filter_context(text: str) -> str:
    """Remove any filters_context details blocks from a message text, so the
    vector-search query is built from the user's actual words — not markup
    that other filters (or this one, on a retry) attached to the message.
    """
    out = text
    while True:
        start = out.find(DETAILS_OPEN_TAG)
        if start == -1:
            return out
        end = out.find(DETAILS_CLOSE_TAG, start)
        if end == -1:
            return out
        out = out[:start] + out[end + len(DETAILS_CLOSE_TAG) :]


def _user_valves_enabled(__user__: dict | None) -> bool:
    """Resolve the user-level `enabled` valve.

    The framework injects the user's valves into __user__['valves'] as a
    pydantic UserValves INSTANCE (utils/filter.py apply_user_valves), not a
    dict — normalize before reading, mirroring adaptive_memory's pattern.
    """
    raw = (__user__ or {}).get("valves") or {}
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    elif hasattr(raw, "dict"):
        raw = raw.dict()
    return bool(raw.get("enabled", True))


# ---------------------------------------------------------------------------
# The filter
# ---------------------------------------------------------------------------


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=10,
            description="Lower values run first. Default 10: runs AFTER time-awareness (priority -10), whose parser-based container update breaks when another filter's filters_context block is already present on the message. This filter merges into existing blocks safely in either order, but keep it after time-awareness so that filter never re-parses a block it didn't create.",
        )
        memory_user_char_limit: int = Field(
            default=2000,
            ge=250,
            description="Max characters for the [User Memory] section (mirrors MEMORIES_USER_CHAR_LIMIT / memories.user_char_limit; the built-in clamps at 250 min).",
        )
        memory_context_char_limit: int = Field(
            default=2000,
            ge=250,
            description="Max characters for the [Memory Neighborhood] + [Relevant Context] sections (mirrors MEMORIES_CONTEXT_CHAR_LIMIT / memories.context_char_limit; the built-in clamps at 250 min).",
        )
        injection_position: int = Field(
            default=0,
            description="Which user message to inject into, counting backwards from the end: 0 = the user message for THIS turn, 1 = the previous user message, 2 = the one before that, etc. Clamps to the oldest available user message when out of range.",
        )
        vector_search_k: int = Field(
            default=8,
            ge=1,
            description="Number of vector-search results pulled for the [Relevant Context] section (the built-in hardcodes 8).",
        )
        skip_if_system_context_enabled: bool = Field(
            default=True,
            description="If Open WebUI's built-in system-message injection (memories.system_context.enable) is ON, skip entirely to avoid double-injection. Deploy with the built-in disabled (ENABLE_MEMORY_SYSTEM_CONTEXT=False) so this filter is the only injector.",
        )

    class UserValves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="Enable/disable memory injection for this user.",
        )

    CONTEXT_ID = "memory_context"

    def __init__(self):
        self.valves = self.Valves()
        self.uservalves = self.UserValves()

    # -- retrieval + rendering: faithful port of v0.11.0 add_memory_context --
    async def _build_memory_context(self, body: dict, request, user_id: str) -> str | None:
        """Reproduce the built-in memory-context render. Returns the wrapped
        block (<memory_context>…</memory_context>) or None when there is
        nothing to inject (mirroring the built-in's early returns).
        """
        # 1. Build the query from the last ≤7 non-empty user messages,
        #    tail-truncated to 4000 chars — same as the built-in.
        messages = body.get("messages") or []
        user_messages = []
        for message in reversed(messages):
            if message.get("role") != ROLE.USER:
                continue
            content = get_content_from_message(message)
            if isinstance(content, str) and content.strip():
                user_messages.append(_strip_filter_context(content).strip())
            if len(user_messages) >= 7:
                break

        query = "\n\n".join(reversed(user_messages))[-4000:]
        if not query:
            return None

        # 2. Load all memories for the user.
        all_memories = []
        if Memories is not None:
            try:
                all_memories = await Memories.get_memories_by_user_id(user_id) or []
            except Exception as e:
                LOGGER.debug("memories load failed: %s", e)

        # 3. Vector search — same call the built-in makes, same try/except
        #    degradation (vector results are optional; user+neighborhood remain).
        results = None
        if query_memory is not None and request is not None:
            try:
                user_model = None
                if Users is not None:
                    try:
                        user_model = await Users.get_user_by_id(user_id)
                    except Exception as e:
                        LOGGER.debug("user model lookup failed: %s", e)
                if user_model is not None:
                    results = await query_memory(
                        request,
                        QueryMemoryForm(
                            content=query, k=max(1, self.valves.vector_search_k)
                        ),
                        user_model,
                    )
            except Exception as e:
                LOGGER.debug("vector search failed: %s", e)

        # 4. Build the three sections, deduping by memory id across them.
        sections = {"user": [], "neighborhood": [], "context": []}
        if memory_label is not None and memory_path_hints is not None and search_memory_rows is not None:
            seen_ids = set()
            for memory in sorted(
                [m for m in all_memories if m.type == "user"],
                key=lambda item: (item.path or "", item.updated_at),
            ):
                seen_ids.add(memory.id)
                sections["user"].append(_safe_label(memory_label(memory)))

            for hint in memory_path_hints(query, all_memories):
                for memory in search_memory_rows(
                    all_memories,
                    path=hint,
                    memory_type="context",
                    limit=4,
                ):
                    if memory.id in seen_ids:
                        continue
                    seen_ids.add(memory.id)
                    sections["neighborhood"].append(_safe_label(memory_label(memory)))

            if results and hasattr(results, "documents") and results.documents:
                for doc_idx, doc in enumerate(results.documents[0]):
                    if not doc:
                        continue
                    metadata = {}
                    if (
                        results.metadatas
                        and results.metadatas[0]
                        and len(results.metadatas[0]) > doc_idx
                    ):
                        metadata = results.metadatas[0][doc_idx] or {}
                    memory_id = None
                    if (
                        results.ids
                        and results.ids[0]
                        and len(results.ids[0]) > doc_idx
                    ):
                        memory_id = results.ids[0][doc_idx]
                    if memory_id and memory_id in seen_ids:
                        continue
                    if memory_id:
                        seen_ids.add(memory_id)
                    content = str(doc)
                    if metadata.get("path") and content.startswith(
                        f'{metadata.get("path")}\n'
                    ):
                        content = content[len(metadata.get("path")) + 1 :]
                    label = (
                        f'{metadata.get("path")}: {content}'
                        if metadata.get("path")
                        else content
                    )
                    sections[_normalize_memory_type(metadata.get("type"))].append(
                        _safe_label(label)
                    )

        # 5. Render with per-section char limits (same split + truncation as
        #    the built-in: user section gets user_limit, the rest context_limit).
        parts = []
        if sections["user"]:
            parts.append(
                "[User Memory]\n" + "\n".join(f"- {m}" for m in sections["user"])
            )
        if sections["neighborhood"]:
            parts.append(
                "[Memory Neighborhood]\n"
                + "\n".join(f"- {m}" for m in sections["neighborhood"])
            )
        if sections["context"]:
            parts.append(
                "[Relevant Context]\n"
                + "\n".join(f"- {m}" for m in sections["context"])
            )
        if not parts:
            return None

        user_limit = max(250, int(self.valves.memory_user_char_limit or 2000))
        context_limit = max(250, int(self.valves.memory_context_char_limit or 2000))

        user_parts = [p for p in parts if p.startswith("[User Memory]")]
        context_parts = [p for p in parts if not p.startswith("[User Memory]")]
        rendered = "\n\n".join(
            [
                "\n\n".join(user_parts)[:user_limit],
                "\n\n".join(context_parts)[:context_limit],
            ]
        ).strip()
        if not rendered:
            return None

        return f"{MEMORY_CONTEXT_OPEN}\n{rendered}\n{MEMORY_CONTEXT_CLOSE}"

    @log_exceptions
    async def inlet(
        self,
        body: dict,
        __event_emitter__,
        __user__: dict = None,
        __model__: dict = None,
        __request__=None,
    ) -> dict:
        # -- gates, mirroring the built-in's conditions ---------------------
        if __user__ is None:
            return body
        if not _user_valves_enabled(__user__):
            return body
        user_id = str(__user__.get("id") or "").strip()
        if not user_id:
            return body
        messages = body.get("messages")
        if not messages:
            return body
        # Feature gate: the built-in only injects when the client sent
        # features.memory=true. Same here.
        if not body.get("features", {}).get("memory"):
            return body
        # Model gate: built-in skips models whose capabilities say no memory.
        if not model_allows_memory(__model__):
            return body
        # Double-injection guard: if the built-in system-message injection is
        # still on, do nothing — the correct deployment disables it and lets
        # this filter be the only injector.
        if self.valves.skip_if_system_context_enabled and Config is not None:
            try:
                if await Config.get("memories.system_context.enable", True):
                    LOGGER.info(
                        "Built-in system-context memory injection is enabled; skipping "
                        "(set memories.system_context.enable=False or ENABLE_MEMORY_SYSTEM_CONTEXT=False "
                        "to use this filter)"
                    )
                    return body
            except Exception as e:
                LOGGER.debug("system_context config check failed: %s", e)

        # -- retrieve, render, inject --------------------------------------
        context = await self._build_memory_context(body, __request__, user_id)
        if context is None:
            return body

        target, target_idx = get_nth_last_user_message(
            messages, self.valves.injection_position
        )
        if target is None:
            return body
        target["content"] = apply_context_to_content(
            target["content"], context, self.CONTEXT_ID
        )
        LOGGER.info(
            "Injected memory context into user message at index %d (position n=%d)",
            target_idx,
            self.valves.injection_position,
        )
        return body
