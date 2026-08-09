"""
title: Time Awareness
author: @abhiraaid (modified by Airi V)
description: pass current time data on each message via filters/context
version: 1.2.4-airi9
"""
# v1.1.0-airi4 (2026-08-08, fix conversation):
#   * add_or_update_filter_context rewritten as pure string surgery (vendored
#     from memory-injector-user-message.py v1.0.2) — the old BeautifulSoup-xml
#     parser silently failed on `user text + <details>` (two XML roots) and
#     fell into the CREATE branch, appending a SECOND container whenever
#     another filter's filters_context block was already present; re-serializing
#     also corrupted user text (xml drops bare '&', html.parser re-escapes).
#     See references/time-awareness-filters-context-rca.md.
#   * handler params renamed user -> __user__ (v0.11.0 convention). With the
#     old name the framework never populated the arg: the user `enabled` valve
#     was dead AND the outlet's user_id guard always short-circuited — the
#     outlet never ran, so blocks were never persisted.
#   * _user_valves_enabled normalizes the pydantic UserValves INSTANCE the
#     framework injects (mirrors the injector's v1.0.2 fix).
#   * rendered timestamp html.escaped before placement inside markup (a custom
#     format_string valve could contain <, >, &).
#
# v1.2.0-airi5 (2026-08-08, Iri's design question):
#   * OUTLET REMOVED. The outlet existed to persist timestamps on historical
#     messages, but (a) it never ran in v0.11.0 (dead `user` param), and (b)
#     its purpose is fully served by the inlet: every user message in the
#     current request is annotated with ITS OWN timestamp — from the DB for
#     saved chats (Chats.get_messages_map_by_chat_id; the backend's
#     load_messages_from_db strips `timestamp` from request messages), from
#     the message object for temp/API chats, with a "now" fallback for the
#     current message only. Deterministic per message -> prompt-cache-safe.
#     Nothing is persisted: no markup in chat history or search.
#   * CONTAINER PREPENDED (block before the user's text). The original reason
#     for appending was DB searchability (every message began with a long
#     preamble) — moot now that nothing persists. Prepending keeps the user's
#     words later in the window, where later context carries more weight.
#     The memory injector's merge is position-agnostic; it now also prepends
#     on CREATE so the protocol has one position rule.
#
# v1.2.1-airi6 (2026-08-08, Iri's question: "why is there a UUID in the block?"):
#   * context_end uuid ATTRIBUTE REMOVED. The uuid was a locator for the
#     parser-based update path (`_remove_context` scanned the raw message for
#     the uuid string to find the block's end) — deleted when string surgery
#     replaced the parser. Nothing reads the value; the `<context_end/>`
#     ELEMENT stays as the merge anchor (filters insert before it). Removing
#     it also removes the per-call-randomness hazard outright (no seeding
#     needed).
#
# v1.2.2-airi7 (2026-08-08, live test — Iri: historical messages not annotated):
#   * chat_id now resolved from up to three sources: the declared __metadata__
#     param (server-side metadata the framework passes to declared params),
#     body['metadata'] (set by the router before process_chat_payload), and
#     body['chat_id']. The router POPS chat_id out of form_data into metadata,
#     so which source is populated at inlet time is path-dependent.
#   * timestamps coerced to epoch seconds (int/float, numeric string, or
#     datetime object) before fromtimestamp.
#   * INFO diagnostics: chat_id source, map size, and per-message decision
#     (db / payload / now / skipped) — the next live test should show exactly
#     which branch fails.
#
# v1.2.3-airi8 (2026-08-09, root cause from Aria's log):
#   * ROOT CAUSE FOUND: the backend strips message ids before inlets run —
#     strip_compaction_fields (middleware.py, before the inlet) pops 'id'
#     from every message, and load_messages_from_db strips 'timestamp' (its
#     whitelist is id/role/content/output/files/contextSummary/usage). So a
#     saved-chat request body has messages with NEITHER id NOR timestamp —
#     id-based lookup always missed. (Aria's log: 'id=None' on every message,
#     map loaded 12/12 with ts.)
#   * FIX: position matching. The filter rebuilds the same parent-link chain
#     the backend loaded (walk parentId from __metadata__.user_message_id —
#     which is NOT stripped) and matches user messages by ordinal. Id-based
#     lookup remains as a fast path when ids ARE present (temp/API paths).
#     Payload timestamps survive strip_compaction_fields (it pops id/usage/
#     contextSummary, not timestamp) — still the temp/API fallback.
#
# v1.2.4-airi9 (2026-08-09, Aria's ask):
#   * debug_logging valve (default False) gates the per-message diagnostics:
#     off -> logged at DEBUG (suppressed by the module's INFO level); on ->
#     logged at INFO. Degradation warnings (map lookup failed/empty, Chats
#     unavailable, chain/body count mismatch) are never gated.

from pydantic import BaseModel, Field
import time
import sys
import datetime
import zoneinfo
import logging
import functools
import inspect
import html

# ---------------------------------------------------------------------------
# Open WebUI internals — every import is optional; if unavailable the filter
# degrades (falls back to message-object timestamps / current time).
# ---------------------------------------------------------------------------
try:
    from open_webui.models.chats import Chats
except Exception:
    Chats = None
try:
    from open_webui.utils.chat_id import is_saved_chat_id
except Exception:
    is_saved_chat_id = None


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


LOGGER: logging.Logger = logging.getLogger("FUNC:TIME_AWARENESS")
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


class ROLE:
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


# ---------------------------------------------------------------------------
# filters_context container — pure string surgery (NO parser).
#
# Vendored from memory-injector-user-message.py (v1.0.2), which proved the
# format. OWUI loads each function under its own module name (function_{id}),
# so a cross-filter import would fail at runtime — hence the copy; keep the
# helpers here in sync with the injector's.
#
# Why not BeautifulSoup: parsing the message with the xml parser requires a
# single root element — a real message is `user text + <details>…</details>`,
# which is two roots, so the parse silently fails (empty tree) and the
# CREATE branch appends a SECOND container (duplicate blocks). Re-serializing
# also corrupts user text (xml drops bare '&'; html.parser re-escapes it).
# String surgery preserves user text byte-for-byte.
#
# Position: filters create the block BEFORE the user's text (prepend=True) so
# the user's words come later in the window (later context carries more
# weight). Merges happen in place, wherever the existing container sits.
# ---------------------------------------------------------------------------


DETAILS_OPEN_TAG = '<details type="filters_context">'
DETAILS_CLOSE_TAG = "</details>"
CONTEXT_END_TAG = "context_end"


def _details_container(content: str) -> str:
    """Build a fresh filters_context details block containing `content`.

    The `<context_end/>` element is kept as the container's tail anchor:
    filters merging into an existing block insert their `<context id=...>`
    element BEFORE it, so the anchor stays last. The element carries no
    attribute — the old `uuid="..."` was a locator for the parser-based
    update path (`_remove_context` found the block end by scanning for the
    uuid string); that path was deleted, nothing reads the value, and a
    per-call random uuid would break prompt-cache stability. (Iri's catch,
    Aug 8.)
    """
    return (
        '<details type="filters_context">'
        "\n<summary>Filters context</summary>\n"
        "<!--This context was added by the system to this message, not by the user. "
        "Message sent on: -->"
        f"\n{content}\n"
        "<context_end/>\n</details>\n"
    )


def add_or_update_filter_context(
    message: str,
    context: str,
    id: str,
    open_tag: str = DETAILS_OPEN_TAG,
    close_tag: str = DETAILS_CLOSE_TAG,
    context_end_tag: str = CONTEXT_END_TAG,
    prepend: bool = False,
) -> str:
    """Append (or prepend, with prepend=True) a filters_context details block
    to `message`, or merge into the existing one. The caller is responsible
    for HTML-escaping any arbitrary text placed inside (this filter escapes
    its rendered timestamps).
    """
    context_str = f'<context id="{id}">{context}</context>'

    message = message if isinstance(message, str) else str(message or "")

    open_idx = message.find(open_tag)
    if open_idx == -1:
        # CREATE: no container yet — add one at the chosen end of the message.
        block = _details_container(context_str)
        if prepend:
            return block + (message + "\n" if message else "")
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


def _user_valves_enabled(__user__: dict | None) -> bool:
    """Resolve the user-level `enabled` valve.

    The framework injects the user's valves into __user__['valves'] as a
    pydantic UserValves INSTANCE (utils/filter.py apply_user_valves), not a
    dict — normalize before reading, mirroring the memory injector's v1.0.2
    pattern (regression for 'UserValves' object has no attribute 'get').
    """
    raw = (__user__ or {}).get("valves") or {}
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    elif hasattr(raw, "dict"):
        raw = raw.dict()
    return bool(raw.get("enabled", True))


def apply_context_to_content(content, context: str, context_id: str, prepend: bool = False):
    """Attach the rendered context block to a message's content.

    Handles both plain-string content and OpenAI-style content lists
    (multimodal parts); the text part is rewritten in place.
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
            modified = add_or_update_filter_context(
                text, context, id=context_id, prepend=prepend
            )
            new_content[text_idx] = {**new_content[text_idx], "text": modified}
        else:
            modified = add_or_update_filter_context(
                "", context, id=context_id, prepend=prepend
            )
            new_content.insert(0, {"type": "text", "text": modified})
        return new_content
    else:
        return add_or_update_filter_context(
            content, context, id=context_id, prepend=prepend
        )


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=-10,
            description="Lower values run first.",
        )
        timezone: str = Field(
            default="UTC",
            description="The timezone to use for the timestamp (e.g., 'America/New_York', 'Europe/Paris', 'Asia/Tokyo').",
        )
        format_string: str = Field(
            default="%A %Y-%m-%d %H:%M:%S %Z",
            description="The strftime format code for rendering the timestamp.",
        )
        debug_logging: bool = Field(
            default=False,
            description="Log per-message diagnostics (chat_id source, DB map size, per-message decisions) at INFO. Off: diagnostics log at DEBUG and are suppressed.",
        )

    class UserValves(BaseModel):
        enabled: bool = Field(
            default=True,
            description="Enable/disable time awareness for this user.",
        )

    CONTEXT_ID = "time_awareness"

    def __init__(self):
        self.valves = self.Valves()
        self.uservalves = self.UserValves()

    def _diag(self, msg, *args):
        """Diagnostic logging — INFO when the debug_logging valve is on,
        DEBUG (suppressed) otherwise. Degradation warnings are never gated.
        """
        if self.valves.debug_logging:
            LOGGER.info(msg, *args)
        else:
            LOGGER.debug(msg, *args)

    async def get_time_context(self, timestamp: int = None) -> str:
        try:
            tz = zoneinfo.ZoneInfo(self.valves.timezone)
        except Exception:
            tz = zoneinfo.ZoneInfo("UTC")
        fmt = self.valves.format_string

        if timestamp is None:
            date = datetime.datetime.now(tz)
        else:
            # pass tz= so fromtimestamp respects the timezone
            date = datetime.datetime.fromtimestamp(timestamp, tz=tz)
        return date.strftime(fmt)

    async def _message_timestamp_map(self, chat_id: str) -> dict:
        """message_id -> timestamp for a saved chat, from the DB.

        The backend's load_messages_from_db strips `timestamp` from the
        request messages (whitelist: id/role/content/output/files/
        contextSummary/usage), so the filter looks it up itself. Same query
        the backend just ran — acceptable on a home instance; an alternative
        would be adding `timestamp` to that whitelist (backend patch).
        """
        if not chat_id:
            self._diag("TA: no chat_id to look up timestamps for")
            return {}, {}
        if Chats is None:
            LOGGER.warning("TA: Chats import unavailable — no DB timestamp map")
            return {}, {}
        if is_saved_chat_id is not None and not is_saved_chat_id(chat_id):
            self._diag("TA: chat %s is not a saved chat — no DB timestamp map", chat_id)
            return {}, {}
        try:
            msgs_map = await Chats.get_messages_map_by_chat_id(chat_id)
        except Exception as e:
            LOGGER.warning("TA: timestamp map lookup failed for %s: %s", chat_id, e)
            return {}, {}
        if not msgs_map:
            LOGGER.warning("TA: timestamp map empty for %s", chat_id)
            return {}, {}
        ts_map = {
            mid: (m or {}).get("timestamp")
            for mid, m in msgs_map.items()
        }
        self._diag(
            "TA: db timestamp map for %s: %d messages, %d with ts",
            chat_id, len(ts_map), sum(1 for v in ts_map.values() if v is not None),
        )
        return ts_map, msgs_map

    async def _chain_user_timestamps(self, msgs_map: dict, user_message_id) -> list:
        """Timestamps of user messages root→current, from the DB parent chain.

        The backend strips `id` from request messages before inlets run
        (strip_compaction_fields pops 'id'), so the filter cannot match by
        message id. Instead it rebuilds the same chain the backend loaded
        (get_message_list walks parentId links from the current message) and
        matches user messages by ordinal.
        """
        if not msgs_map or not user_message_id:
            return []
        ts = []
        mid = user_message_id
        visited = set()
        while mid and mid not in visited:
            m = msgs_map.get(mid)
            if not m:
                break
            visited.add(mid)
            if m.get("role") == ROLE.USER:
                ts.append(m.get("timestamp"))
            mid = m.get("parentId")
        ts.reverse()
        self._diag(
            "TA: chain from user_message_id=%r: %d user timestamps (root→current)",
            user_message_id, len(ts),
        )
        return ts

    @staticmethod
    def _coerce_ts(ts) -> float | None:
        """Normalize a DB/payload timestamp to epoch seconds.

        Handles int/float, numeric strings, and datetime objects (some DB
        backends return the created_at column as a datetime).
        """
        if ts is None:
            return None
        if isinstance(ts, (int, float)):
            return float(ts)
        if isinstance(ts, datetime.datetime):
            return ts.timestamp()
        if isinstance(ts, str):
            try:
                return float(ts)
            except ValueError:
                return None
        return None

    @log_exceptions
    async def inlet(
        self,
        body: dict,
        __event_emitter__,
        __user__: dict = None,
        __metadata__: dict = None,
    ) -> dict:
        if not _user_valves_enabled(__user__):
            return body
        messages = body.get("messages")
        if not messages:
            return body

        user_indices = [i for i, m in enumerate(messages) if m.get("role") == ROLE.USER]
        if not user_indices:
            return body

        # Saved chats: real per-message timestamps from the DB. Temp/API
        # chats: client timestamps on the message objects, if present.
        # The router POPS chat_id out of form_data into metadata, so which
        # source is populated at inlet time is path-dependent — try all.
        chat_id = (
            (__metadata__ or {}).get("chat_id")
            or (body.get("metadata") or {}).get("chat_id")
            or body.get("chat_id")
        )
        self._diag(
            "TA: inlet on %d messages, %d user msgs, chat_id=%r (metadata=%s, __metadata__=%s)",
            len(messages), len(user_indices), chat_id,
            bool(body.get("metadata")), bool(__metadata__),
        )
        user_message_id = (
            (__metadata__ or {}).get("user_message_id")
            or (body.get("metadata") or {}).get("user_message_id")
        )
        ts_map, msgs_map = await self._message_timestamp_map(chat_id)
        chain_ts = await self._chain_user_timestamps(msgs_map or {}, user_message_id)
        # Warn only on genuine misalignment: an empty chain is the known
        # degradation path (no user_message_id anchor), not a mismatch.
        if chain_ts and len(chain_ts) != len(user_indices):
            LOGGER.warning(
                "TA: chain user count %d != body user count %d",
                len(chain_ts), len(user_indices),
            )

        for ordinal, i in enumerate(user_indices):
            m = messages[i]
            ts = None
            source = None
            mid = m.get("id")
            if mid is not None:
                # ids survive on temp/API paths — fast path
                ts = ts_map.get(mid)
                source = "db-id"
            elif ordinal < len(chain_ts):
                # saved chats: backend strips ids — match by position
                ts = chain_ts[ordinal]
                source = "db-chain"
            if ts is None:
                ts = m.get("timestamp")
                source = "payload"
            if ts is None and i == user_indices[-1]:
                # current message with no resolvable timestamp: it was sent now
                ts = time.time()
                source = "now"
            if ts is None:
                self._diag(
                    "TA: skipping message idx=%d id=%r (no timestamp)", i, mid
                )
                # historical message, no timestamp available — leave it clean
                continue
            ts = self._coerce_ts(ts)
            if ts is None:
                self._diag(
                    "TA: skipping message idx=%d id=%r (unusable ts %r)",
                    i, mid, m.get("timestamp"),
                )
                continue
            # html.escape: the timestamp is placed inside markup; a custom
            # format_string valve could contain <, >, & — keep it text.
            context = html.escape(await self.get_time_context(ts), quote=False)
            m["content"] = apply_context_to_content(
                m["content"], context, self.CONTEXT_ID, prepend=True
            )
            self._diag(
                "TA: annotated idx=%d id=%r from %s -> %s",
                i, mid, source, context,
            )
        return body


def get_last_message(messages, role):
    for i, m in enumerate(reversed(messages)):
        if m.get("role") == role:
            return (m, len(messages) - i - 1)
    return (None, None)
