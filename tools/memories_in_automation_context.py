"""
title: Memories in Automation Context
author: Airi V
version: 1.1.0
description: A complete suite of tools giving the LLM precise control over long-term user memories.
             Works in Automation contexts where built-in memory tools are unavailable.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

# Open WebUI 0.9.6 architectural imports
from open_webui.models.memories import Memories
from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT

log = logging.getLogger(__name__)


def _fmt_timestamp(epoch_seconds: int) -> str:
    """Format an epoch-second timestamp as 'YYYY-MM-DD HH:MM' (UTC)."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _fmt_date(epoch_seconds: int) -> str:
    """Format an epoch-second timestamp as 'YYYY-MM-DD' (date only, UTC)."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%d")


class Tools:
    class Valves(BaseModel):
        """Configuration valves for this tool (reserved for future use)."""
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def add_memory(self, content: str, __user__: dict, __request__=None) -> str:
        """
        Store a new long-term personal memory about the user.
        Use this when the user explicitly asks you to remember something or provides an enduring personal preference.

        :param content: The exact factual statement or preference to remember.
        :return: A JSON string with status and the new memory ID.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # 1. Store in the core relational database
            memory = await Memories.insert_new_memory(user_id, content)
            if not memory:
                return json.dumps({"status": "error", "message": "Failed to register memory entry in database."})

            # 2. Sync to the local vector repository for semantic search
            if __request__ and hasattr(__request__.app.state, "EMBEDDING_FUNCTION"):
                try:
                    embedding_func = __request__.app.state.EMBEDDING_FUNCTION
                    # The embedding function is async and accepts an optional user= kwarg
                    # (used for user-info forwarding on external engines; ignored on local ones)
                    vector = await embedding_func(content, user=__user__)

                    VECTOR_DB_CLIENT.upsert(
                        collection_name=f"user-memory-{user_id}",
                        items=[
                            {
                                "id": memory.id,
                                "text": memory.content,
                                "vector": vector,
                                "metadata": {
                                    "created_at": memory.created_at,
                                    "updated_at": memory.updated_at,
                                },
                            }
                        ],
                    )
                except Exception as embed_err:
                    # Embedding failed but the DB record is intact — still report success
                    # but log the embedding failure for diagnostics
                    log.warning("Memory %s stored but vector embedding failed: %s", memory.id, embed_err)

            return json.dumps({"status": "success", "id": memory.id})
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def replace_memory_content(
        self, memory_id: str, content: str, __user__: dict, __request__=None
    ) -> str:
        """
        Replace the content of an existing memory with updated information.
        Use this when a user's stated preference changes, or an outdated memory requires correction.
        Ownership is verified — you can only update your own memories.

        :param memory_id: The unique ID string of the memory to modify.
        :param content: The new updated factual statement.
        :return: A JSON string with status, the memory ID, and the new content.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # 1. Update text content in the relational DB — validates ownership via user_id
            memory = await Memories.update_memory_by_id_and_user_id(memory_id, user_id, content)
            if not memory:
                return json.dumps(
                    {"status": "error", "message": f"Memory with ID {memory_id} could not be found or updated."}
                )

            # 2. Re-embed and update the vector db segment
            if __request__ and hasattr(__request__.app.state, "EMBEDDING_FUNCTION"):
                try:
                    embedding_func = __request__.app.state.EMBEDDING_FUNCTION
                    vector = await embedding_func(content, user=__user__)

                    VECTOR_DB_CLIENT.upsert(
                        collection_name=f"user-memory-{user_id}",
                        items=[
                            {
                                "id": memory.id,
                                "text": memory.content,
                                "vector": vector,
                                "metadata": {
                                    "created_at": memory.created_at,
                                    "updated_at": memory.updated_at,
                                },
                            }
                        ],
                    )
                except Exception as embed_err:
                    log.warning("Memory %s updated but re-embedding failed: %s", memory.id, embed_err)

            return json.dumps({"status": "success", "id": memory.id, "content": memory.content})
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def delete_memory(self, memory_id: str, __user__: dict) -> str:
        """
        Permanently delete a specific memory by its ID.
        Use this when the user explicitly requests you to forget a piece of information.
        Ownership is verified — you can only delete your own memories.

        :param memory_id: The unique ID string of the targeted memory.
        :return: A JSON string with status and a descriptive message.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # Enforce authorization boundary checks by validating both ID and user ownership
            result = await Memories.delete_memory_by_id_and_user_id(memory_id, user_id)
            if not result:
                return json.dumps(
                    {"status": "error", "message": f"Memory ID {memory_id} not found or permission denied."}
                )

            # Clean up the vector store entry — best-effort (if this fails the relational
            # record is already gone, but we log and report what happened)
            vector_error = None
            try:
                VECTOR_DB_CLIENT.delete(
                    collection_name=f"user-memory-{user_id}", ids=[memory_id]
                )
            except Exception as ve:
                log.warning("Memory %s deleted from DB but vector cleanup failed: %s", memory_id, ve)
                vector_error = str(ve)

            if vector_error:
                return json.dumps(
                    {
                        "status": "success",
                        "message": f"Memory {memory_id} deleted (vector cleanup note: {vector_error})",
                    }
                )
            return json.dumps({"status": "success", "message": f"Memory {memory_id} deleted"})
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def list_memories(self, __user__: dict) -> str:
        """
        Retrieve all long-term memories currently stored for the interacting user.
        Use this to inspect or review the full set of recorded structural facts.

        :return: A JSON array of memory objects, each with id, content, created_at, and updated_at.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            memories = await Memories.get_memories_by_user_id(user_id)
            if not memories:
                return json.dumps([])

            result = [
                {
                    "id": m.id,
                    "content": m.content,
                    "created_at": _fmt_timestamp(m.created_at),
                    "updated_at": _fmt_timestamp(m.updated_at),
                }
                for m in memories
            ]
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def search_memories(
        self, query: str, __user__: dict, limit: int = 5, __request__=None
    ) -> str:
        """
        Perform a semantic vector search across the user's long-term memory archive.
        Use this when you need to explicitly recall contextual facts related to a keyword or concept.

        :param query: The search term or conceptual phrase to query.
        :param limit: Maximum number of relevant memories to retrieve (default: 5).
        :return: A JSON array of matching memories, each with id, date, and content.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        if not __request__ or not hasattr(__request__.app.state, "EMBEDDING_FUNCTION"):
            return json.dumps({"status": "error", "message": "Core application embedding function is unavailable."})

        try:
            embedding_func = __request__.app.state.EMBEDDING_FUNCTION
            query_vector = await embedding_func(query, user=__user__)

            # Vector DB search queries return a multi-dimensional SearchResult batch
            search_res = VECTOR_DB_CLIENT.search(
                collection_name=f"user-memory-{user_id}",
                vectors=[query_vector],
                limit=limit,
            )

            # Defensive structural checks — the result shape can differ across vector DB backends
            if (
                not search_res
                or not hasattr(search_res, "documents")
                or not hasattr(search_res, "ids")
                or not search_res.documents
                or not search_res.ids
                or not search_res.documents[0]
            ):
                return json.dumps([])

            # Map vector results back to database records to get accurate timestamps
            # (the vector store metadata may be stale)
            result_items = []
            for i in range(min(len(search_res.documents[0]), len(search_res.ids[0]))):
                mem_id = search_res.ids[0][i]
                text = search_res.documents[0][i]

                # Try to get the DB record for an accurate date
                db_memory = await Memories.get_memory_by_id(mem_id) if hasattr(Memories, "get_memory_by_id") else None

                date_str = _fmt_date(db_memory.created_at) if db_memory else None

                item = {"id": mem_id, "content": text}
                if date_str:
                    item["date"] = date_str
                result_items.append(item)

            return json.dumps(result_items, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})
