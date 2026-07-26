"""
title: Per-User System Prompt Injector
author: Airi V
version: 0.3.0
description: >
  Constructs a system prompt from up to 10 designated prompt files
  (filesystem or knowledge base), then combines it with the model-level
  and user-level system prompts with configurable positioning.
  Delegates to the standard Open WebUI pipeline — no custom model needed.

  Implemented as a filter function.  One instance serves all models and
  users.  Users configure their own prompt file paths via UserValves;
  the admin configures positioning via Valves.

  Designed for multi-user instances where each AI assistant needs a
  private system prompt assembled from admin-curated files with optional
  user overrides, without leaking prompt content between users.
required_open_webui_version: 0.9.0
"""

from pathlib import Path, PurePosixPath
from typing import Optional, List, Dict
import logging

from pydantic import BaseModel, Field

logger = logging.getLogger("filter:per-user-system-prompt")

# ── Constants ────────────────────────────────────────────────────────────

_FILE_KEYS: List[str] = [
    "FILE_1", "FILE_2", "FILE_3", "FILE_4", "FILE_5",
    "FILE_6", "FILE_7", "FILE_8", "FILE_9", "FILE_10",
]

_FILE_SEPARATOR = "\n\n---\n\n"
_PROMPT_SEPARATOR = "\n\n"


class Filter:
    """Inject a per-user system prompt as an inlet filter.

    Architecture
    ------------
    The filter runs during the inlet phase, before the request reaches
    the model.  It:

    1.  Reads up to 10 prompt files from paths the user configures in
        their UserValves (FILE_1 … FILE_10).
    2.  Assembles them in order with ``---`` separators.
    3.  Combines the file-assembled prompt with the model+user system
        prompt (which Open WebUI already merged before inlet filters
        run) according to the admin-configured ``MODEL_POSITION`` valve.
    4.  Injects the result as the system message in the request body.

    Because this is a **filter** rather than a **pipe**, one function
    instance works for every model.  No per-model copies, no hardcoded
    model IDs, no Workspace model wrapper.

    Setup
    -----
    1.  Import this filter (Admin Panel → Functions → Import).
    2.  Enable it (toggle on in Functions list).
    3.  Optionally attach to specific models via the model's Filters
        tab, or leave as a global filter to run on all models.
    4.  Each user sets their own file paths via UserValves.
    5.  Admin sets MODEL_POSITION to control prompt ordering.

    File sources
    ------------
    Each FILE_N valve accepts either:

    -   **Filesystem path** (e.g. ``/home/user/.system-prompt/core.md``).
        The file must be readable by the Open WebUI process.  If the
        file doesn't exist, it is silently skipped.

    -   **Knowledge base path** (e.g. ``/IDENTITY.md``).  A leading
        ``/`` that does NOT match an existing filesystem path is
        treated as a path within the knowledge collections attached to
        the selected model.  The filter iterates through attached
        collections in order, searching for a matching filename at the
        given directory path, and uses the first match found.

    Positioning
    -----------
    The ``MODEL_POSITION`` valve (admin-configured) accepts:

    ``prepend``
        Model+user prompt → files.  The model and user prompts
        (already merged in model → user order by Open WebUI) are
        placed before the file-assembled prompt.

    ``append``
        Files → model+user prompt.  The model and user prompts are
        placed after the file-assembled prompt.

    ``replace``
        Files only.  The model and user prompts are **discarded**.
        Useful when the files contain the complete system prompt.
    """

    # ── Valves ────────────────────────────────────────────────────────

    class Valves(BaseModel):
        priority: int = Field(
            default=10,
            description=(
                "Execution order.  Lower values run first.  Default 10 "
                "places this filter after most other inlet filters."
            ),
        )
        MODEL_POSITION: str = Field(
            default="prepend",
            description=(
                "Where to place the model+user system prompt relative to "
                "the file-assembled prompt: 'prepend', 'append', or "
                "'replace' (files only — model+user discarded)."
            ),
        )

    class UserValves(BaseModel):
        FILE_1: str = Field(
            default="",
            description=(
                "First file to inject (injected first).  Filesystem path "
                "or KB path like /IDENTITY.md.  Leave empty to skip."
            ),
        )
        FILE_2: str = Field(
            default="",
            description="Second file to inject.",
        )
        FILE_3: str = Field(
            default="",
            description="Third file to inject.",
        )
        FILE_4: str = Field(
            default="",
            description="Fourth file to inject.",
        )
        FILE_5: str = Field(
            default="",
            description="Fifth file to inject.",
        )
        FILE_6: str = Field(
            default="",
            description="Sixth file to inject.",
        )
        FILE_7: str = Field(
            default="",
            description="Seventh file to inject.",
        )
        FILE_8: str = Field(
            default="",
            description="Eighth file to inject.",
        )
        FILE_9: str = Field(
            default="",
            description="Ninth file to inject.",
        )
        FILE_10: str = Field(
            default="",
            description=(
                "Tenth file to inject (injected last).  Leave empty to skip."
            ),
        )

    # ── Initialisation ────────────────────────────────────────────────

    def __init__(self):
        self.valves = self.Valves()

    # ── Helpers: file reading ─────────────────────────────────────────

    @staticmethod
    def _read_filesystem(path_str: str) -> Optional[str]:
        """Read a file from the filesystem, returning trimmed content."""
        p = Path(path_str)
        if not p.is_file():
            return None
        try:
            content = p.read_text().strip()
            return content if content else None
        except Exception:
            logger.warning(
                "Failed to read filesystem prompt file: %s", p, exc_info=True,
            )
            return None

    @staticmethod
    async def _resolve_kb_file_paths(
        knowledge_id: str,
    ) -> Dict[str, str]:
        """Build a map of ``path → file_id`` for all files in a KB.

        Uses directory breadcrumbs to reconstruct full POSIX-style paths
        (e.g. ``/thoughts/choreography.md``).
        """
        from open_webui.models.knowledge import Knowledges

        path_to_id: Dict[str, str] = {}

        # Fetch all files with their directory_ids
        files_with_dirs = await Knowledges.get_files_with_directory_ids(
            knowledge_id,
        )

        if not files_with_dirs:
            return path_to_id

        # Cache directory breadcrumbs to avoid per-file DB calls
        dir_cache: Dict[str, List[str]] = {}

        for file_model, dir_id in files_with_dirs:
            parts: List[str] = []

            if dir_id:
                if dir_id not in dir_cache:
                    crumbs = await Knowledges.get_directory_breadcrumbs(
                        dir_id,
                    )
                    dir_cache[dir_id] = [
                        d.name for d in crumbs
                    ]
                parts.extend(dir_cache[dir_id])

            parts.append(file_model.filename)
            path = "/" + "/".join(parts)
            path_to_id[path] = file_model.id

        return path_to_id

    @staticmethod
    async def _read_knowledge_file(
        file_id: str,
    ) -> Optional[str]:
        """Read file content from the knowledge base by file ID."""
        from open_webui.models.files import Files

        try:
            file_model = await Files.get_file_by_id(file_id)
            if file_model and file_model.data:
                content = file_model.data.get("content", "").strip()
                return content if content else None
        except Exception:
            logger.warning(
                "Failed to read knowledge file %s", file_id, exc_info=True,
            )
        return None

    async def _read_file(self, path_str: str, __model__: dict) -> Optional[str]:
        """Read a prompt file from filesystem or knowledge base.

        Strategy:
        1.  If ``path_str`` is an existing filesystem path, read it directly.
        2.  Otherwise, if it starts with ``/``, treat it as a KB path
            and search attached knowledge collections.
        3.  Otherwise, treat as a filesystem path and return ``None`` if
            it doesn't exist.
        """
        stripped = path_str.strip()
        if not stripped:
            return None

        # ── Try filesystem first ──────────────────────────────────
        fs_content = self._read_filesystem(stripped)
        if fs_content is not None:
            logger.debug("Read file from filesystem: %s", stripped)
            return fs_content

        # ── If path looks like a KB path, search collections ─────
        if not stripped.startswith("/"):
            return None

        from open_webui.models.knowledge import Knowledges

        model_knowledge = (
            __model__.get("info", {})
            .get("meta", {})
            .get("knowledge", [])
        )
        if not model_knowledge:
            return None

        # Collect knowledge base IDs from attached collections
        kb_ids: List[str] = []
        for item in model_knowledge:
            if item.get("type") == "collection":
                kb_ids.append(item.get("id"))
            elif item.get("collection_name"):
                # Legacy single-collection attachment
                kb_ids.append(item.get("collection_name"))
            # Note: collection_names (multi-collection legacy) is
            # an array; we could iterate those too, but for
            # simplicity we skip them — multi-collection legacy
            # items are rare and complex to resolve.

        for kb_id in kb_ids:
            if not kb_id:
                continue

            try:
                path_map = await self._resolve_kb_file_paths(kb_id)
                file_id = path_map.get(stripped)
                if file_id:
                    content = await self._read_knowledge_file(file_id)
                    if content:
                        logger.debug(
                            "Read file from knowledge base %s: %s",
                            kb_id, stripped,
                        )
                        return content
            except Exception:
                logger.warning(
                    "Failed to search knowledge base %s for %s",
                    kb_id, stripped, exc_info=True,
                )
                continue

        logger.debug("File not found in any attached KB: %s", stripped)
        return None

    # ── Prompt assembly ───────────────────────────────────────────────

    async def _assemble_file_prompt(
        self,
        user_valves,
        __model__: dict,
    ) -> Optional[str]:
        """Read FILE_1 … FILE_10 from UserValves and join them."""
        parts: List[str] = []
        for key in _FILE_KEYS:
            path = getattr(user_valves, key, "")
            content = await self._read_file(path, __model__)
            if content:
                parts.append(content)
                logger.debug("Loaded prompt file from valve %s", key)

        if not parts:
            return None

        return _FILE_SEPARATOR.join(parts)

    def _combine_prompts(
        self,
        model_user_prompt: Optional[str],
        file_prompt: Optional[str],
    ) -> Optional[str]:
        """Combine model+user prompt and file prompt per MODEL_POSITION."""
        position = getattr(self.valves, "MODEL_POSITION", "prepend")

        parts: List[str] = []

        if position == "replace":
            # Files only — discard model+user prompt
            if file_prompt:
                parts.append(file_prompt)

        elif position == "append":
            # Files → model+user
            if file_prompt:
                parts.append(file_prompt)
            if model_user_prompt:
                parts.append(model_user_prompt)

        else:  # "prepend" (default)
            # Model+user → files
            if model_user_prompt:
                parts.append(model_user_prompt)
            if file_prompt:
                parts.append(file_prompt)

        if not parts:
            return None

        return _PROMPT_SEPARATOR.join(parts)

    # ── Inlet handler ─────────────────────────────────────────────────

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __model__: Optional[dict] = None,
        __request__ = None,
    ) -> dict:
        """Read files, assemble prompts, and inject the system message.

        Parameters (injected by Open WebUI)
        -----------------------------------
        body:
            OpenAI-format chat-completion request dict.  Modified
            in-place to inject the assembled system prompt.
        __user__:
            The authenticated user dict (id, email, name, role, valves).
        __model__:
            The selected model dict from ``request.app.state.MODELS``.
        __request__:
            FastAPI Request object.
        """
        if __user__ is None:
            logger.warning("inlet called without __user__; passing through")
            return body

        user_id: str = __user__.get("id", "unknown")
        user_label: str = __user__.get(
            "email", __user__.get("name", user_id),
        )

        logger.info(
            "Per-user prompt injector (filter): user=%s (%s)",
            user_id, user_label,
        )

        # ── Retrieve UserValves for this request ──────────────────
        # The filter infrastructure stores per-user valves as a
        # Pydantic model under __user__['valves'].  We pass them
        # directly to avoid concurrency issues (the module instance
        # is shared across requests).
        user_valves = __user__.get("valves")
        if user_valves is None:
            user_valves = self.UserValves()

        # ── Extract the existing system message ──────────────────
        # By the time inlet filters run, Open WebUI middleware has
        # already merged the model-level and user-level system
        # prompts (model → user order) into body["messages"][0].
        model_user_prompt: Optional[str] = None
        messages = body.get("messages", [])
        system_idx: Optional[int] = None

        for i, m in enumerate(messages):
            if m.get("role") == "system":
                content = str(m.get("content", "")).strip()
                if content:
                    model_user_prompt = content
                system_idx = i
                break

        # ── Assemble the file prompt ────────────────────────────
        file_prompt: Optional[str] = None
        if __model__ is not None:
            file_prompt = await self._assemble_file_prompt(
                user_valves, __model__,
            )
        else:
            logger.warning(
                "__model__ not available; skipping file prompt assembly",
            )

        # ── Combine and inject ──────────────────────────────────
        final_prompt = self._combine_prompts(model_user_prompt, file_prompt)

        if final_prompt:
            new_system = {"role": "system", "content": final_prompt}

            if system_idx is not None:
                messages[system_idx] = new_system
            else:
                messages.insert(0, new_system)

            logger.info(
                "Injected system prompt for user=%s (%d chars total)",
                user_label, len(final_prompt),
            )
        elif file_prompt is None and model_user_prompt is not None:
            # No files, and model+user prompt is already in place —
            # nothing to do
            logger.info(
                "No file prompt for user=%s; leaving existing system "
                "prompt unchanged",
                user_label,
            )
        elif system_idx is not None and file_prompt is None:
            # replace position with no files → remove system message
            del messages[system_idx]
            logger.info(
                "No file prompt and position=replace; removed system "
                "prompt for user=%s",
                user_label,
            )

        return body
