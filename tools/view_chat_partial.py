"""
title: view_chat_partial
author: Airi V
description: View portions of a chat conversation — last N messages, messages within a time range, or a specific paginated range. Supports contextual expansion around matched results (before_n / after_n). Always gates access to the current user's own chats.
version: 1.2.0
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
        before_n: Optional[int] = None,
        after_n: Optional[int] = None,
        __user__: dict = None,
    ) -> str:
        """
        View a portion of a chat conversation. Supports:
        - last_n: get the tail end of a long conversation
        - start_timestamp / end_timestamp: get messages within a time range (Unix seconds). If start_timestamp is omitted, includes messages from the start of the chat. If end_timestamp is omitted, includes messages up to the end of the chat.
        - from_index + count: paginated access to the full history (after ordering chronologically)
        - roles: comma-separated list of roles to include (e.g. "user,assistant"). If omitted, all roles are returned.
        - before_n: include N messages preceding the first result as context
        - after_n: include N messages following the last result as context

        Context parameters (before_n / after_n) operate like grep's -B and -A:
        they pull from the full conversation, bypassing role and timestamp filters.
        This means context messages may include roles or timestamps that the
        primary filters exclude.

        All access is gated to the authenticated user's own chats.

        :param chat_id: The ID of the chat to retrieve.
        :param last_n: Return only the last N messages in the conversation.
        :param start_timestamp: Only include messages created at or after this Unix timestamp (seconds).
        :param end_timestamp: Only include messages created at or before this Unix timestamp (seconds).
        :param from_index: 0-based index to start returning messages from (after ordering chronologically).
        :param count: Maximum number of messages to return. Used with from_index for pagination, or alone to cap output.
        :param roles: Comma-separated list of roles to include (e.g. "user,assistant").
        :param before_n: Number of messages preceding the first result to include as context.
        :param after_n: Number of messages following the last result to include as context.
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

        # Build an ordered list by walking the parent chain from currentId.
        # full_ordered is preserved for context expansion; 'ordered' is filtered.
        current_id = history.get("currentId")
        full_ordered = self._walk_history(messages_map, current_id)
        total_messages = len(full_ordered)

        # --- Apply filters ---
        ordered = full_ordered[:]

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

        # --- Context expansion ---
        # before_n / after_n expand around the result boundaries in the full
        # (unfiltered) ordered list, matching grep's -B / -A semantics: context
        # lines may not match the filter criteria but provide surrounding context.
        context_before_count = 0
        context_after_count = 0
        if ordered and ((before_n is not None and before_n > 0) or (after_n is not None and after_n > 0)):
            # Build position map for the full ordered list
            full_id_to_pos = {}
            for i, m in enumerate(full_ordered):
                mid = m.get("id")
                if mid is not None:
                    full_id_to_pos[mid] = i

            first_pos = full_id_to_pos.get(ordered[0].get("id"))
            last_pos = full_id_to_pos.get(ordered[-1].get("id"))

            context_before = []
            context_after = []

            if first_pos is not None and before_n and before_n > 0:
                ctx_start = max(0, first_pos - before_n)
                context_before = full_ordered[ctx_start:first_pos]
                context_before_count = len(context_before)

            if last_pos is not None and after_n and after_n > 0:
                ctx_end = min(len(full_ordered), last_pos + 1 + after_n)
                context_after = full_ordered[last_pos + 1:ctx_end]
                context_after_count = len(context_after)

            ordered = context_before + ordered + context_after

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
            msg_line = f"Messages returned: {len(ordered)} of {total_messages}"
            if context_before_count or context_after_count:
                msg_line += f" ({context_before_count} before, {context_after_count} after context)"
            lines.append(msg_line)
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

            # --- v0.10+ output array format ---
            # If content is empty, try reconstructing from the structured output array
            if not content:
                output_arr = msg.get("output")
                if isinstance(output_arr, list) and output_arr:
                    content = self._reconstruct_from_output_array(output_arr)

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
    def _html_entity_encode(s: str) -> str:
        """HTML-entity encode a string for safe use as an XML/HTML attribute value."""
        return (
            s
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("'", "&#39;")
        )

    @staticmethod
    def _reconstruct_from_output_array(output_arr: list) -> str:
        """
        Reconstruct a human-readable content string from the v0.10+
        structured `output` array format.

        The output array contains typed items:
          - "message": text content [{type: "output_text", text: "..."}]
          - "function_call": tool invocation with id, call_id, name, arguments
          - "function_call_output": tool result linked by call_id
          - "reasoning": reasoning/chain-of-thought blocks

        Tool calls are rendered as <details type="tool_calls"> blocks
        (compatible with Open WebUI's ToolCallParser) so they appear as
        interactive cards rather than raw JSON.

        Returns empty string if the output array has no usable content.
        """
        # First pass: build call_id → result text map from function_call_output
        results_by_call_id: dict[str, str] = {}
        for item in output_arr:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call_output":
                call_id = item.get("call_id", "")
                output_items = item.get("output")
                if isinstance(output_items, list) and call_id:
                    texts = [
                        o.get("text", "")
                        for o in output_items
                        if isinstance(o, dict) and o.get("text")
                    ]
                    result = "\n".join(t for t in texts if t)
                    if result:
                        results_by_call_id[call_id] = result

        # Second pass: build content string in order
        parts: list[str] = []
        for item in output_arr:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")

            if item_type == "message":
                content_arr = item.get("content")
                if isinstance(content_arr, list):
                    texts = [
                        piece.get("text", "")
                        for piece in content_arr
                        if isinstance(piece, dict) and piece.get("text")
                    ]
                    text = "\n".join(t for t in texts if t)
                    if text:
                        parts.append(text)

            elif item_type == "function_call":
                name = item.get("name", "")
                if not name:
                    continue
                call_id = item.get("call_id") or item.get("id") or ""
                item_id = item.get("id") or call_id
                status = item.get("status", "completed")
                done = "true" if status == "completed" else "false"
                arguments = item.get("arguments", "")
                encoded_args = Tools._html_entity_encode(arguments)
                result_text = results_by_call_id.get(call_id, "")

                if result_text:
                    encoded_result = Tools._html_entity_encode(result_text)
                    block = (
                        f'<details type="tool_calls" id="{item_id}" '
                        f'name="{name}" done="{done}" '
                        f'arguments="{encoded_args}" '
                        f'result="{encoded_result}">\n'
                        f'<summary>Tool Executed</summary>\n'
                        f'{result_text}\n'
                        f'</details>'
                    )
                else:
                    block = (
                        f'<details type="tool_calls" id="{item_id}" '
                        f'name="{name}" done="{done}" '
                        f'arguments="{encoded_args}">\n'
                        f'<summary>Tool Executed</summary>\n'
                        f'</details>'
                    )
                parts.append(block)

            elif item_type == "reasoning":
                content_arr = item.get("content")
                if isinstance(content_arr, list):
                    texts = [
                        piece.get("text", "")
                        for piece in content_arr
                        if isinstance(piece, dict) and piece.get("text")
                    ]
                    reasoning_text = "\n".join(t for t in texts if t)
                    if reasoning_text:
                        block = (
                            '<details type="reasoning" done="true">\n'
                            '<summary>Thinking</summary>\n'
                            f'{reasoning_text}\n'
                            '</details>'
                        )
                        parts.append(block)

            # Skip function_call_output (consumed in first pass) and unknown types

        return "\n\n".join(parts) if parts else ""

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
