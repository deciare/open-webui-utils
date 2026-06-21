"""
title: view_chat_partial
author: Airi V
description: View portions of a chat conversation — last N messages, messages within a time range, or a specific paginated range. Always gates access to the current user's own chats.
version: 1.1.0
"""

from typing import Optional
from pydantic import BaseModel, Field
import logging
import time
from datetime import datetime, timezone

try:
    from open_webui.models.chats import Chats
except ImportError:
    Chats = None

log = logging.getLogger("view_chat_partial")


class Tools:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        max_messages: int = Field(
            default=200,
            description="Hard upper limit on messages returned per call, regardless of parameters. Safety valve against context overflow.",
        )

    async def view_chat_partial(
        self,
        chat_id: str,
        last_n: Optional[int] = None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        from_index: Optional[int] = None,
        count: Optional[int] = None,
        roles: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """
        View a portion of a chat conversation. Supports:
        - last_n: get the tail end of a long conversation
        - start_timestamp / end_timestamp: get messages within a time range (Unix seconds). If start_timestamp is omitted, includes messages from the start of the chat. If end_timestamp is omitted, includes messages up to the end of the chat.
        - from_index + count: paginated access to the full history (after ordering chronologically)
        - roles: comma-separated list of roles to include (e.g. "user,assistant"). If omitted, all roles are returned.

        All access is gated to the authenticated user's own chats.

        :param chat_id: The ID of the chat to retrieve.
        :param last_n: Return only the last N messages in the conversation.
        :param start_timestamp: Only include messages created at or after this Unix timestamp (seconds).
        :param end_timestamp: Only include messages created at or before this Unix timestamp (seconds).
        :param from_index: 0-based index to start returning messages from (after ordering chronologically).
        :param count: Maximum number of messages to return. Used with from_index for pagination, or alone to cap output.
        :param roles: Comma-separated list of roles to include (e.g. "user,assistant").
        """

        if Chats is None:
            return "Error: Chats model not available. This tool requires Open WebUI."

        if not __user__ or not __user__.get("id"):
            return "Error: User context not available."

        user_id = __user__["id"]

        # --- Fetch the chat ---
        try:
            chat = await Chats.get_chat_by_id(chat_id)
        except Exception as e:
            log.error("Failed to fetch chat %s: %s", chat_id, e)
            return f"Error: Could not retrieve chat. {e}"

        if chat is None:
            return f"Error: Chat '{chat_id}' not found."

        # --- Ownership gate ---
        if chat.user_id != user_id:
            log.warning(
                "User %s attempted to access chat %s owned by %s",
                user_id,
                chat_id,
                chat.user_id,
            )
            return "Error: Access denied. You can only view your own chats."

        # --- Extract messages ---
        history = (chat.chat or {}).get("history", {})
        messages_map = history.get("messages", {})

        if not messages_map:
            return f"Chat '{chat.title or chat_id}' has no messages."

        # Build an ordered list by walking the parent chain from currentId
        current_id = history.get("currentId")
        ordered = self._walk_history(messages_map, current_id)
        total_messages = len(ordered)

        # --- Apply filters ---

        # Filter by roles
        if roles and isinstance(roles, str):
            allowed = {r.strip().lower() for r in roles.split(",")}
            ordered = [m for m in ordered if m.get("role", "").lower() in allowed]

        # Filter by timestamp range
        if start_timestamp is not None and isinstance(start_timestamp, (int, float)):
            ordered = [
                m for m in ordered
                if m.get("timestamp") is not None and m["timestamp"] >= start_timestamp
            ]
        if end_timestamp is not None and isinstance(end_timestamp, (int, float)):
            ordered = [
                m for m in ordered
                if m.get("timestamp") is not None and m["timestamp"] <= end_timestamp
            ]

        # Apply last_n (takes precedence — slices from the end)
        if last_n is not None and isinstance(last_n, int) and last_n > 0:
            ordered = ordered[-last_n:]

        # Apply from_index + count (pagination)
        if from_index is not None or count is not None:
            start = from_index if from_index is not None and isinstance(from_index, int) else 0
            end = start + count if count is not None and isinstance(count, int) else len(ordered)
            ordered = ordered[start:end]

        # Apply hard cap from valves
        if len(ordered) > self.valves.max_messages:
            overflow = len(ordered) - self.valves.max_messages
            ordered = ordered[-self.valves.max_messages:]
            cap_note = f"\n\n[Output capped: {overflow} messages truncated by max_messages limit ({self.valves.max_messages}).]"
        else:
            cap_note = ""

        if not ordered:
            return f"Chat '{chat.title or chat_id}': no messages matched your filters (total messages in chat: {total_messages})."

        # --- Format output ---
        lines = [f"# Chat: {chat.title or chat_id}"]
        if len(ordered) == total_messages:
            lines.append(f"Messages returned: {len(ordered)}")
        else:
            lines.append(f"Messages returned: {len(ordered)} of {total_messages}")
        if start_timestamp:
            dt = datetime.fromtimestamp(start_timestamp, tz=timezone.utc).isoformat()
            lines.append(f"From: {dt}")
        if end_timestamp:
            dt = datetime.fromtimestamp(end_timestamp, tz=timezone.utc).isoformat()
            lines.append(f"To: {dt}")
        lines.append("")
        lines.append("---")
        lines.append("")

        for msg in ordered:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ts = msg.get("timestamp")
            ts_str = ""
            if ts:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                ts_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")

            header = f"**[{role}]**"
            if ts_str:
                header += f"  _{ts_str}_"

            # Handle content that might be a list (multi-part messages)
            if isinstance(content, list):
                text_parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                content = "\n".join(text_parts)

            # Truncate very long individual messages
            if isinstance(content, str) and len(content) > 10000:
                content = content[:10000] + f"\n... [truncated, {len(content)} chars total]"

            lines.append(header)
            lines.append(content if content else "_(empty)_")
            lines.append("")

        return "\n".join(lines) + cap_note

    @staticmethod
    def _walk_history(messages_map: dict, start_id: str | None) -> list[dict]:
        """
        Walk the message chain from start_id backwards via parentId,
        then reverse to get chronological order.

        Falls back to timestamp-based sorting if the parent chain is broken.
        """
        if not messages_map:
            return []

        # Try parent-chain walk first
        if start_id and start_id in messages_map:
            chain = []
            current = start_id
            visited = set()
            while current and current in messages_map and current not in visited:
                visited.add(current)
                msg = messages_map[current]
                chain.append(msg)
                current = msg.get("parentId")

            if chain:
                chain.reverse()
                # Any messages not in the chain (orphans) get appended at the end
                chain_ids = set(m.get("id") for m in chain)
                orphans = [
                    msg for mid, msg in messages_map.items()
                    if mid not in chain_ids and isinstance(msg, dict) and msg.get("role")
                ]
                if orphans:
                    orphans.sort(key=lambda m: m.get("timestamp", 0))
                    chain.extend(orphans)
                return chain

        # Fallback: just sort everything by timestamp
        all_msgs = [
            msg for msg in messages_map.values()
            if isinstance(msg, dict) and msg.get("role")
        ]
        all_msgs.sort(key=lambda m: m.get("timestamp", 0))
        return all_msgs
