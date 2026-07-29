"""
title: Memories in Automation Context (for Open WebUI 0.10.1)
author: Airi V (with guidance from Aria and Iri)
version: 1.3.0
description: A complete suite of tools giving the LLM precise control over long-term user memories.
             Works in Automation contexts where built-in memory tools are unavailable.
             Updated for Open WebUI v0.10.1 API and behavior.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

# Open WebUI 0.10.1 architectural imports
from open_webui.models.memories import Memories
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (ported from open_webui/utils/memory.py)
# ---------------------------------------------------------------------------

def _fmt_timestamp(epoch_seconds: int) -> str:
    """Format an epoch-second timestamp as 'YYYY-MM-DD HH:MM' (UTC)."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _fmt_date(epoch_seconds: int) -> str:
    """Format an epoch-second timestamp as 'YYYY-MM-DD' (date only, UTC)."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%d")


def clean_memory_content(content: str | None) -> str:
    value = (content or "").strip()
    if not value:
        raise ValueError("Memory content cannot be empty")
    return value


def clean_memory_path(path: str | None) -> str | None:
    value = re.sub(r"/+", "/", (path or "").strip().strip("/"))
    if not value:
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid memory path")
    return value


def memory_vector_text(content: str, path: str | None = None) -> str:
    path = clean_memory_path(path)
    return f"{path}\n{content}" if path else content


# ---------------------------------------------------------------------------
# Path ranking (ported from open_webui/utils/memory.py _path_rank)
# ---------------------------------------------------------------------------

def _path_parts(path: str | None) -> list[str]:
    return [part for part in (path or "").split("/") if part]


def _parent_path(path: str | None) -> str | None:
    parts = _path_parts(path)
    return "/".join(parts[:-1]) if len(parts) > 1 else None


def _path_rank(memory_path: str | None, lookup_path: str | None) -> tuple | None:
    """Return a sort-key tuple indicating how close *memory_path* is to *lookup_path*.

    Lower tuples sort closer.  ``None`` means no relationship.
    """
    if not lookup_path:
        return None

    memory_path = clean_memory_path(memory_path)
    lookup_path = clean_memory_path(lookup_path)
    if not memory_path or not lookup_path:
        return None

    if memory_path == lookup_path:
        return (0, 0)
    if memory_path.startswith(f"{lookup_path}/"):
        return (1, len(_path_parts(memory_path)) - len(_path_parts(lookup_path)))
    if lookup_path.startswith(f"{memory_path}/"):
        return (2, len(_path_parts(lookup_path)) - len(_path_parts(memory_path)))
    if _parent_path(memory_path) and _parent_path(memory_path) == _parent_path(lookup_path):
        return (3, 0)

    memory_parts = set(_path_parts(memory_path))
    lookup_parts = set(_path_parts(lookup_path))
    shared = len(memory_parts & lookup_parts)
    if shared:
        return (4, -shared)
    if _path_parts(memory_path)[-1:] == _path_parts(lookup_path)[-1:]:
        return (5, 0)

    return None


def _memory_matches_query(memory, query: str) -> bool:
    value = query.strip().lower()
    if not value:
        return True
    return value in (memory.content or "").lower() or value in (memory.path or "").lower()


# ---------------------------------------------------------------------------
# Path group helpers (ported from open_webui/utils/memory.py)
# ---------------------------------------------------------------------------

def _normalize_memory_type(t: str | None) -> str:
    return "user" if t == "user" else "context"


def _memory_metadata(memory) -> dict:
    return {
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "type": memory.type,
        "path": memory.path,
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------

class Tools:
    class Valves(BaseModel):
        """Configuration valves for this tool (reserved for future use)."""
        pass

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # 1. add_memory — Create a single memory
    # ------------------------------------------------------------------

    async def add_memory(
        self, content: str, __user__: dict, __request__=None,
        type: str = "context", path: str = "",
    ) -> str:
        """
        Store a new long-term personal memory about the user.
        Use this when the user explicitly asks you to remember something or provides an enduring personal preference.

        :param content: The exact factual statement or preference to remember.
        :param type: "user" for permanent memories injected into every chat, "context" for topic-specific memories.
        :param path: Optional hierarchical path for grouping (e.g. "people/iri", "infrastructure/redis").
        :return: A JSON string with status, the new memory ID, type, and path.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            clean_content = clean_memory_content(content)
            clean_path = clean_memory_path(path)
            mem_type = _normalize_memory_type(type) if type else "context"

            # 1. Store in the core relational database
            memory = await Memories.insert_new_memory(
                user_id, clean_content, memory_type=mem_type, path=clean_path,
                meta={"created_by": "automation"},
            )
            if not memory:
                return json.dumps({"status": "error", "message": "Failed to register memory entry in database."})

            # 2. Sync to the local vector repository for semantic search
            if __request__ and hasattr(__request__.app.state, "EMBEDDING_FUNCTION"):
                try:
                    embedding_func = __request__.app.state.EMBEDDING_FUNCTION
                    vector = await embedding_func(
                        memory_vector_text(memory.content, memory.path), user=__user__,
                    )

                    await ASYNC_VECTOR_DB_CLIENT.upsert(
                        collection_name=f"user-memory-{user_id}",
                        items=[
                            {
                                "id": memory.id,
                                "text": memory_vector_text(memory.content, memory.path),
                                "vector": vector,
                                "metadata": _memory_metadata(memory),
                            }
                        ],
                    )
                except Exception as embed_err:
                    log.warning("Memory %s stored but vector embedding failed: %s", memory.id, embed_err)

            return json.dumps({
                "status": "success",
                "id": memory.id,
                "type": memory.type,
                "path": memory.path,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ------------------------------------------------------------------
    # 2. replace_memory_content — Update an existing memory by ID
    # ------------------------------------------------------------------

    async def replace_memory_content(
        self, memory_id: str, content: str, __user__: dict, __request__=None,
        type: str = "", path: str = "",
    ) -> str:
        """
        Replace the content of an existing memory with updated information.
        Use this when a user's stated preference changes, or an outdated memory requires correction.
        Ownership is verified — you can only update your own memories.

        :param memory_id: The unique ID string of the memory to modify.
        :param content: The new updated factual statement.
        :param type: Optional new type ("user" or "context"). Empty string = keep current.
        :param path: Optional new path. Empty string = keep current. Set to "/" to clear.
        :return: A JSON string with status, the memory ID, and the new content.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            clean_content = clean_memory_content(content)
            new_type = _normalize_memory_type(type) if type else None
            new_path = clean_memory_path(path) if path else None
            # Only set update_path if the caller explicitly provided a path argument
            update_path = path != ""

            memory = await Memories.update_memory_by_id_and_user_id(
                memory_id, user_id, clean_content,
                memory_type=new_type, path=new_path, update_path=update_path,
                meta={"created_by": "automation"},
            )
            if not memory:
                return json.dumps(
                    {"status": "error", "message": f"Memory with ID {memory_id} could not be found or updated."}
                )

            # Re-embed and update the vector db segment
            if __request__ and hasattr(__request__.app.state, "EMBEDDING_FUNCTION"):
                try:
                    embedding_func = __request__.app.state.EMBEDDING_FUNCTION
                    vector = await embedding_func(
                        memory_vector_text(memory.content, memory.path), user=__user__,
                    )

                    await ASYNC_VECTOR_DB_CLIENT.upsert(
                        collection_name=f"user-memory-{user_id}",
                        items=[
                            {
                                "id": memory.id,
                                "text": memory_vector_text(memory.content, memory.path),
                                "vector": vector,
                                "metadata": _memory_metadata(memory),
                            }
                        ],
                    )
                except Exception as embed_err:
                    log.warning("Memory %s updated but re-embedding failed: %s", memory.id, embed_err)

            return json.dumps({
                "status": "success",
                "id": memory.id,
                "content": memory.content,
                "type": memory.type,
                "path": memory.path,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ------------------------------------------------------------------
    # 3. delete_memory — Remove a single memory
    # ------------------------------------------------------------------

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
            result = await Memories.delete_memory_by_id_and_user_id(memory_id, user_id)
            if not result:
                return json.dumps(
                    {"status": "error", "message": f"Memory ID {memory_id} not found or permission denied."}
                )

            # Clean up the vector store entry
            vector_error = None
            try:
                await ASYNC_VECTOR_DB_CLIENT.delete(
                    collection_name=f"user-memory-{user_id}", ids=[memory_id],
                )
            except Exception as ve:
                log.warning("Memory %s deleted from DB but vector cleanup failed: %s", memory_id, ve)
                vector_error = str(ve)

            if vector_error:
                return json.dumps(
                    {"status": "success", "message": f"Memory {memory_id} deleted (vector cleanup note: {vector_error})"}
                )
            return json.dumps({"status": "success", "message": f"Memory {memory_id} deleted"})
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ------------------------------------------------------------------
    # 4. list_memories — Dump everything
    # ------------------------------------------------------------------

    async def list_memories(self, __user__: dict) -> str:
        """
        Retrieve all long-term memories currently stored for the interacting user.
        Use this to inspect or review the full set of recorded structural facts.

        :return: A JSON array of memory objects, each with id, content, type, path, created_at, and updated_at.
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
                    "type": m.type,
                    "path": m.path,
                    "created_at": _fmt_timestamp(m.created_at),
                    "updated_at": _fmt_timestamp(m.updated_at),
                }
                for m in memories
            ]
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ------------------------------------------------------------------
    # 5. search_memories — Text search + path proximity ranking
    # ------------------------------------------------------------------

    async def search_memories(
        self,
        __user__: dict,
        query: str = "",
        path: str = "",
        memory_id: str = "",
        type: str = "all",
        count: int = 20,
    ) -> str:
        """
        Search memories by text content and/or hierarchical path proximity.
        This is NOT vector/semantic search — it uses substring matching and path ranking.

        Use this to:
        - Browse memories near a path (just set path, leave query empty)
        - Find memories containing a specific keyword (set query)
        - Read a specific memory by ID (set memory_id)

        :param query: Optional substring to match against content and path (case-insensitive).
        :param path: Optional path to find memories hierarchically near (children, parents, siblings, shared segments).
        :param memory_id: Optional exact memory ID to retrieve.
        :param type: Filter by type: "user", "context", or "all" (default).
        :param count: Maximum results to return (default 20, max 100).
        :return: A JSON array of matching memories sorted by path proximity then recency.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            memories = await Memories.get_memories_by_user_id(user_id)
            if not memories:
                return json.dumps([])

            rows = list(memories)

            # Filter by exact memory_id
            if memory_id:
                rows = [m for m in rows if m.id == memory_id]

            # Filter by type
            if type != "all":
                rows = [m for m in rows if m.type == type]

            # Filter by path proximity + text fallback
            lookup_path = clean_memory_path(path)
            if lookup_path:
                basename = _path_parts(lookup_path)[-1] if _path_parts(lookup_path) else lookup_path

                def related(memory) -> bool:
                    rank = _path_rank(memory.path, lookup_path)
                    if rank is not None:
                        return True
                    haystack = f"{memory.path or ''}\n{memory.content or ''}".lower()
                    return lookup_path.lower() in haystack or basename.lower() in haystack

                rows = [m for m in rows if related(m)]

            # Filter by query substring
            query = (query or "").strip()
            if query:
                rows = [m for m in rows if _memory_matches_query(m, query)]

            # Sort by path rank (closer = higher), then recency
            def sort_key(memory):
                rank = _path_rank(memory.path, lookup_path) if lookup_path else None
                return rank if rank is not None else (9, 0), -(memory.updated_at or 0)

            rows = sorted(rows, key=sort_key)[: max(1, min(count or 20, 100))]

            result = [
                {
                    "id": m.id,
                    "content": m.content,
                    "type": m.type,
                    "path": m.path,
                    "created_at": _fmt_timestamp(m.created_at),
                    "updated_at": _fmt_timestamp(m.updated_at),
                }
                for m in rows
            ]
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ------------------------------------------------------------------
    # 6. update_memory — Batch operations (NEW in 0.10.1)
    # ------------------------------------------------------------------

    async def update_memory(self, operations: list[dict], __user__: dict, __request__=None) -> str:
        """
        Apply a batch of memory changes in a single atomic operation.
        Use this for bulk memory audits — add new compact memories while removing old ones.

        Operation shapes:
          {"action": "add", "content": "...", "type": "user"|"context", "path": "..."}
          {"action": "replace", "id": "...", "content": "...", "type": "...", "path": "..."}
          {"action": "move", "id": "...", "path": "..."}
          {"action": "remove", "id": "..."}

        :param operations: List of operation dicts to apply.
        :return: A JSON array of result dicts, one per operation.
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        if not operations:
            return json.dumps({"status": "error", "message": "No memory operations provided."})

        # Validate operations before touching the database
        validated = []
        for op in operations:
            try:
                action = op.get("action")
                if action not in ("add", "replace", "move", "remove"):
                    raise ValueError(f"Unsupported memory operation: {action}")

                entry = {"action": action}
                if action == "add":
                    entry["content"] = clean_memory_content(op.get("content"))
                    entry["type"] = _normalize_memory_type(op.get("type"))
                    entry["path"] = clean_memory_path(op.get("path"))
                elif action == "replace":
                    if not op.get("id"):
                        raise ValueError("Memory id is required for replace")
                    entry["id"] = op["id"]
                    entry["content"] = clean_memory_content(op.get("content"))
                    if op.get("type") is not None:
                        entry["type"] = _normalize_memory_type(op.get("type"))
                    # Only include path in the operation if the caller explicitly provided it.
                    # Upstream v0.10.1 has a bug where model_dump() includes default None,
                    # causing path to be cleared even when not intended.
                    if op.get("path") is not None:
                        entry["path"] = clean_memory_path(op.get("path"))
                elif action == "move":
                    if not op.get("id"):
                        raise ValueError("Memory id is required for move")
                    entry["id"] = op["id"]
                    entry["path"] = clean_memory_path(op.get("path"))
                elif action == "remove":
                    if not op.get("id"):
                        raise ValueError("Memory id is required for remove")
                    entry["id"] = op["id"]

                validated.append(entry)
            except Exception as e:
                return json.dumps({"status": "error", "message": str(e)})

        try:
            # Apply operations via the model's transactional batch method
            results = await Memories.apply_memory_operations(user_id, validated)
        except ValueError as e:
            return json.dumps({"status": "error", "message": str(e)})
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

        # Batch upsert/delete vector embeddings
        upsert_items = []
        delete_ids = []
        for result in results:
            memory = result.get("memory")
            if hasattr(memory, "id"):
                # It's a MemoryModel — convert for embedding ops
                if result.get("status") in ("created", "updated"):
                    if __request__ and hasattr(__request__.app.state, "EMBEDDING_FUNCTION"):
                        try:
                            embedding_func = __request__.app.state.EMBEDDING_FUNCTION
                            vector = await embedding_func(
                                memory_vector_text(memory.content, memory.path), user=__user__,
                            )
                            upsert_items.append({
                                "id": memory.id,
                                "text": memory_vector_text(memory.content, memory.path),
                                "vector": vector,
                                "metadata": _memory_metadata(memory),
                            })
                        except Exception as embed_err:
                            log.warning("Memory %s re-embedding failed: %s", memory.id, embed_err)
                if result.get("status") == "deleted" and result.get("id"):
                    delete_ids.append(result["id"])
                # Serialize MemoryModel to dict for JSON response
                result["memory"] = {
                    "id": memory.id,
                    "content": memory.content,
                    "type": memory.type,
                    "path": memory.path,
                    "user_id": memory.user_id,
                    "meta": memory.meta,
                }

        if upsert_items:
            try:
                await ASYNC_VECTOR_DB_CLIENT.upsert(
                    collection_name=f"user-memory-{user_id}", items=upsert_items,
                )
            except Exception as e:
                log.warning("Batch vector upsert failed: %s", e)

        if delete_ids:
            try:
                await ASYNC_VECTOR_DB_CLIENT.delete(
                    collection_name=f"user-memory-{user_id}", ids=delete_ids,
                )
            except Exception as e:
                log.warning("Batch vector delete failed: %s", e)

        return json.dumps(results, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # 7. list_memory_paths — Discover the path hierarchy (NEW in 0.10.1)
    # ------------------------------------------------------------------

    async def list_memory_paths(
        self,
        __user__: dict,
        query: str = "",
        type: str = "all",
        count: int = 100,
    ) -> str:
        """
        List all unique memory paths grouped by (path, type), with child paths and memory counts.
        Use this to see the overall structure/organization of memories.

        :param query: Optional text to filter paths by substring match on path or content.
        :param type: Filter by type: "user", "context", or "all" (default).
        :param count: Maximum number of path groups to return (default 100).
        :return: A JSON object with "paths" (list of path entries) and "count".
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            memories = await Memories.get_memories_by_user_id(user_id)
            if not memories:
                return json.dumps({"paths": [], "count": 0})

            rows = list(memories)

            # Filter by type
            if type != "all":
                rows = [m for m in rows if m.type == type]

            # Apply text filter (substring on path + content)
            query_str = (query or "").strip().lower()
            if query_str:
                def _matches(m) -> bool:
                    return (
                        query_str in (m.path or "").lower()
                        or query_str in (m.content or "").lower()
                    )
                rows = [m for m in rows if _matches(m)]

            # Group by (path, type)
            from collections import defaultdict
            groups_map: dict[tuple, dict] = {}

            for m in rows:
                key = (m.path, m.type)
                if key not in groups_map:
                    groups_map[key] = {
                        "path": m.path,
                        "type": m.type,
                        "count": 0,
                        "updated_at": 0,
                        "children": [],
                    }
                g = groups_map[key]
                g["count"] += 1
                if (m.updated_at or 0) > g["updated_at"]:
                    g["updated_at"] = m.updated_at

            # Discover children (one level deep) for each group
            all_paths = set(m.path for m in rows if m.path)
            for g in groups_map.values():
                parent = g["path"]
                if not parent:
                    continue
                children = sorted(
                    p for p in all_paths
                    if p.startswith(f"{parent}/") and "/" not in p[len(parent) + 1:]
                )[:20]
                g["children"] = children

            # Sort by most recently updated
            groups = sorted(groups_map.values(), key=lambda g: -(g["updated_at"] or 0))[:count]

            return json.dumps({"paths": groups, "count": len(groups_map)}, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ------------------------------------------------------------------
    # 8. read_memory_path — Read memories at a specific path (NEW in 0.10.1)
    # ------------------------------------------------------------------

    async def read_memory_path(
        self,
        path: str,
        __user__: dict,
        type: str = "all",
        include_children: bool = True,
        count: int = 50,
    ) -> str:
        """
        Read all memories at a specific path, including parent and child paths.
        Use this after list_memory_paths to drill into a specific location.

        :param path: The memory path to read (e.g. "people/iri").
        :param type: Filter by type: "user", "context", or "all" (default).
        :param include_children: If True (default), also include memories under child paths.
        :param count: Maximum memories to return (default 50).
        :return: A JSON object with "path" (the lookup path), "memories" (list), "parents" (list of parent paths), and "children" (list of child paths).
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            lookup = clean_memory_path(path)
            if not lookup:
                return json.dumps({"status": "error", "message": "Path is required."})

            memories = await Memories.get_memories_by_user_id(user_id)
            if not memories:
                return json.dumps({"path": lookup, "memories": [], "parents": [], "children": []})

            rows = list(memories)

            # Filter by type
            if type != "all":
                rows = [m for m in rows if m.type == type]

            # Compute parent paths
            parts = _path_parts(lookup)
            parents = ["/".join(parts[:i]) for i in range(1, len(parts))]

            # Compute child paths
            child_prefix = f"{lookup}/"
            all_paths = set(m.path for m in rows if m.path)
            children = sorted(
                p for p in all_paths
                if p.startswith(child_prefix)
            )

            # Filter memories: exact path + parents + optionally children
            def in_scope(m) -> bool:
                mp = m.path or ""
                if mp == lookup:
                    return True
                if mp in parents:
                    return True
                if include_children and mp.startswith(child_prefix):
                    return True
                return False

            matched = [m for m in rows if in_scope(m)]

            # Sort by proximity then recency
            def sort_key(m):
                rank = _path_rank(m.path, lookup)
                return (rank if rank is not None else (9, 0)), -(m.updated_at or 0)

            matched = sorted(matched, key=sort_key)[:count]

            result_memories = [
                {
                    "id": m.id,
                    "content": m.content,
                    "type": m.type,
                    "path": m.path,
                    "user_id": m.user_id,
                    "meta": m.meta,
                    "created_at": m.created_at,
                    "updated_at": m.updated_at,
                }
                for m in matched
            ]

            return json.dumps({
                "path": lookup,
                "memories": result_memories,
                "parents": parents,
                "children": children,
            }, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})
