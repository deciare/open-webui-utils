"""
title: Query Memories (Semantic Search)
author: Airi V
version: 1.0.0
description: >
  Semantic vector search across user memories. Restores the behaviour that
  v0.9.6's built-in search_memories tool had (actual embedding-based similarity
  search) but outputs in v0.10.1 format with type, path, and date fields.
  The built-in v0.10.1 search_memories tool only does substring matching.
"""

import json
import logging
from datetime import datetime, timezone

from pydantic import BaseModel

from open_webui.config import RAG_EMBEDDING_QUERY_PREFIX
from open_webui.models.memories import Memories
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

log = logging.getLogger(__name__)


def _fmt_date(epoch_seconds: int) -> str:
    """Format an epoch-second timestamp as 'YYYY-MM-DD' (UTC)."""
    if not epoch_seconds:
        return ""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%d")


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def query_memories(
        self,
        query: str,
        __user__: dict,
        __request__=None,
        count: int = 5,
    ) -> str:
        """
        Perform a semantic vector search across the user's long-term memories.
        Use this when you need to find memories related to a concept, topic, or
        idea — even if the exact words don't appear in the memory text. Unlike
        the built-in search_memories (which only does substring matching), this
        tool uses embedding-based similarity search.

        :param query: A natural language search query describing what you're looking for.
        :param count: Maximum number of relevant memories to return (default 5).
        :return: JSON array of matching memories with id, type, path, content, and dates.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps(
                {"status": "error", "message": "User context not available."}
            )

        if not __request__ or not hasattr(
            __request__.app.state, "EMBEDDING_FUNCTION"
        ):
            return json.dumps(
                {
                    "status": "error",
                    "message": "Embedding function not available.",
                }
            )

        try:
            # Embed the query with the RAG query prefix (same as query_memory endpoint)
            embedding_func = __request__.app.state.EMBEDDING_FUNCTION
            query_vector = await embedding_func(
                query, RAG_EMBEDDING_QUERY_PREFIX, user=__user__
            )

            # Search the per-user vector collection
            search_result = await ASYNC_VECTOR_DB_CLIENT.search(
                collection_name=f"user-memory-{user_id}",
                vectors=[query_vector],
                limit=count,
            )

            if (
                not search_result
                or not hasattr(search_result, "documents")
                or not search_result.documents
                or not search_result.ids
                or not search_result.documents[0]
            ):
                return json.dumps([])

            # Map vector results back to database records for accurate metadata
            results = []
            seen_ids = set()

            for i in range(
                min(
                    len(search_result.documents[0]),
                    len(search_result.ids[0]),
                )
            ):
                mem_id = search_result.ids[0][i]
                raw_text = search_result.documents[0][i] or ""

                if mem_id in seen_ids:
                    continue
                seen_ids.add(mem_id)

                # Get full record from DB for type, path, and accurate timestamps
                db_memory = await Memories.get_memory_by_id(mem_id)

                if db_memory:
                    # The vector DB stores "path\ncontent" — strip the path prefix
                    # if present to get the original content back
                    content = db_memory.content
                    path = db_memory.path
                    memory_type = db_memory.type
                    created_at = _fmt_date(db_memory.created_at)
                    updated_at = _fmt_date(db_memory.updated_at)
                else:
                    # Fallback: try to parse the vector text
                    # (path\ncontent format from memory_vector_text)
                    content = raw_text
                    path = None
                    memory_type = "context"
                    created_at = ""
                    updated_at = ""

                item = {
                    "id": mem_id,
                    "type": memory_type,
                    "path": path,
                    "content": content,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
                results.append(item)

            return json.dumps(results, ensure_ascii=False)

        except Exception as e:
            log.exception("query_memories error: %s", e)
            return json.dumps({"status": "error", "message": str(e)})
