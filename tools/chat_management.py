"""
title: Chat & Folder Management
author: Airi V
version: 1.2.2
description: A complete suite of tools for organising conversations — rename, archive, move between folders,
             create/rename/delete folders, and advanced chat search with folder-aware filtering.
             Works in both normal chat and Automation contexts.
             Complements the built-in search_chats/view_chat tools with folder-aware operations.
             Each operation has a per‑method Valve toggle so admins can disable destructive actions
             (delete_chat / delete_folder) without losing the rest of the tool.
             Supports subfolders — every folder-name-based method accepts parent_folder_name for
             disambiguation when the same folder name exists at different levels.
"""

import json
import logging
from typing import Optional

from open_webui.models.folders import FolderForm, FolderUpdateForm, Folders
from open_webui.models.chats import ChatForm, Chats
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class Tools:
    class Valves(BaseModel):
        enable_create_folder: bool = Field(
            default=True,
            description="Allow creating new folders.",
        )
        enable_rename_folder: bool = Field(
            default=True,
            description="Allow renaming existing folders.",
        )
        enable_delete_folder: bool = Field(
            default=False,
            description="Allow deleting folders (chats inside are moved to root, not deleted). "
                        "Disabled by default for safety.",
        )
        enable_list_folders: bool = Field(
            default=True,
            description="Allow listing all folders with their chat contents.",
        )
        enable_move_chat: bool = Field(
            default=True,
            description="Allow moving chats between folders (auto‑creates folder if needed).",
        )
        enable_rename_chat: bool = Field(
            default=True,
            description="Allow renaming conversations.",
        )
        enable_archive_chat: bool = Field(
            default=True,
            description="Allow archiving conversations.",
        )
        enable_unarchive_chat: bool = Field(
            default=True,
            description="Allow unarchiving conversations.",
        )
        enable_delete_chat: bool = Field(
            default=False,
            description="Allow permanently deleting conversations. "
                        "Disabled by default — enable only if you trust the model not to make mistakes.",
        )
        enable_search_chats_advanced: bool = Field(
            default=True,
            description="Allow advanced chat search with folder‑aware filtering.",
        )
        enable_get_current_chat_id: bool = Field(
            default=True,
            description="Allow the model to discover its own current chat ID and title.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _check(self, valve_name: str) -> str | None:
        """Return an error JSON string if the valve is disabled, else None.

        If the valve name doesn't exist on the Valves model at all (typo in code),
        log a warning and default to disabled — safer than silently allowing.
        """
        if not hasattr(self.valves, valve_name):
            log.warning(
                "Valve check: '%s' not found on Valves model — treating as disabled. "
                "This is likely a coding error.",
                valve_name,
            )
            return json.dumps({
                "status": "error",
                "message": f"Operation '{valve_name.removeprefix('enable_')}' is disabled via tool Valves.",
            })
        if not getattr(self.valves, valve_name):
            return json.dumps({
                "status": "error",
                "message": f"Operation '{valve_name.removeprefix('enable_')}' is disabled via tool Valves.",
            })
        return None

    # ── Folder resolution (shared helper) ──

    async def _resolve_folder_id(
        self,
        folder_name: str,
        parent_folder_name: Optional[str],
        user_id: str,
    ) -> Optional[str]:
        """Resolve a folder name to its ID, scoped to an optional parent.

        Uses get_folder_by_parent_id_and_user_id_and_name for case‑insensitive
        exact‑match lookup scoped to the given parent (or root).  This avoids the
        ambiguity problems of search_folders_by_names, which returns descendants
        and can collide across different parent chains.

        Returns the folder ID, or None if not found.
        """
        # Resolve parent first if given
        parent_id = None
        if parent_folder_name:
            parent = await Folders.get_folder_by_parent_id_and_user_id_and_name(
                parent_id=None, user_id=user_id, name=parent_folder_name
            )
            if not parent:
                return None
            parent_id = parent.id

        folder = await Folders.get_folder_by_parent_id_and_user_id_and_name(
            parent_id=parent_id, user_id=user_id, name=folder_name,
        )
        if not folder:
            return None
        return folder.id

    async def _resolve_folder_id_required(
        self,
        folder_name: str,
        parent_folder_name: Optional[str],
        user_id: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """Like _resolve_folder_id but returns (id, error_string) where error_string
        is a user‑friendly message when the folder is not found.
        """
        fid = await self._resolve_folder_id(folder_name, parent_folder_name, user_id)
        if fid is None:
            scope = f" inside '{parent_folder_name}'" if parent_folder_name else ""
            hint = (
                " If the folder is inside another folder, specify parent_folder_name."
                if not parent_folder_name
                else ""
            )
            return None, f"Folder '{folder_name}'{scope} not found.{hint}"
        return fid, None

    # ── Folder operations ──

    async def create_folder(
        self,
        name: str,
        parent_folder_name: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """
        Create a new folder to organise conversations.

        :param name: The display name for the new folder.
        :param parent_folder_name: Name of the parent folder to nest this folder under.
                                   Omit to create at the root level.
        :return: JSON with status and the new folder's id and name.
        """
        denied = self._check("enable_create_folder")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # Resolve parent folder if specified
            parent_id = None
            if parent_folder_name:
                parent = await Folders.get_folder_by_parent_id_and_user_id_and_name(
                    parent_id=None, user_id=user_id, name=parent_folder_name,
                )
                if not parent:
                    return json.dumps({
                        "status": "error",
                        "message": f"Parent folder '{parent_folder_name}' not found.",
                    })
                parent_id = parent.id

            folder = await Folders.insert_new_folder(
                user_id,
                FolderForm(name=name),
                parent_id=parent_id,
            )
            if not folder:
                return json.dumps({"status": "error", "message": "Failed to create folder."})
            return json.dumps({
                "status": "success",
                "id": folder.id,
                "name": folder.name,
                "parent_id": folder.parent_id,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def rename_folder(
        self,
        folder_name: str,
        new_name: str,
        parent_folder_name: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """
        Rename an existing folder.

        :param folder_name: The current name of the folder to rename.
        :param new_name: The new display name for the folder.
        :param parent_folder_name: Name of the parent folder this folder lives in.
                                   Omit if the folder is at the root level.
        :return: JSON with status and the updated folder info.
        """
        denied = self._check("enable_rename_folder")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            folder_id, error = await self._resolve_folder_id_required(
                folder_name, parent_folder_name, user_id
            )
            if error:
                return json.dumps({"status": "error", "message": error})

            updated = await Folders.update_folder_by_id_and_user_id(
                folder_id, user_id,
                FolderUpdateForm(name=new_name),
            )
            if not updated:
                return json.dumps({"status": "error", "message": "Failed to rename folder."})
            return json.dumps({
                "status": "success",
                "id": updated.id,
                "name": updated.name,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def delete_folder(
        self,
        folder_name: str,
        parent_folder_name: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """
        Delete a folder. Chats inside the folder and any sub‑folders are NOT deleted —
        they are moved back to root (folder_id set to None).

        Disabled by default. To enable, set enable_delete_folder = True in tool Valves.

        :param folder_name: The name of the folder to delete.
        :param parent_folder_name: Name of the parent folder this folder lives under.
                                   Omit if the folder is at the root level.
        :return: JSON with status and a descriptive message.
        """
        denied = self._check("enable_delete_folder")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            folder_id, error = await self._resolve_folder_id_required(
                folder_name, parent_folder_name, user_id
            )
            if error:
                return json.dumps({"status": "error", "message": error})

            # Collect all descendant folder IDs so we can move their chats too.
            # The built-in delete_folder_by_id_and_user_id cascade‑deletes children
            # but does NOT move chats from those children — we must handle that.
            all_folder_ids = [folder_id]
            children = await Folders.get_children_folders_by_id_and_user_id(
                folder_id, user_id
            )
            if children:
                all_folder_ids.extend(child.id for child in children)

            # Move chats from every affected folder to root
            for fid in all_folder_ids:
                ok = await Chats.move_chats_by_user_id_and_folder_id(
                    user_id, fid, None
                )
                if not ok:
                    return json.dumps({
                        "status": "error",
                        "message": f"Failed to evacuate chats from folder before deletion. "
                                   f"Deletion aborted — no changes were made to the folder itself.",
                    })

            # Now delete the folder (cascade‑deletes children)
            deleted_ids = await Folders.delete_folder_by_id_and_user_id(folder_id, user_id)
            if not deleted_ids:
                return json.dumps({"status": "error", "message": "Failed to delete folder."})
            return json.dumps({
                "status": "success",
                "message": f"Folder '{folder_name}' and {len(deleted_ids) - 1} sub‑folder(s) deleted. "
                           f"Chats moved to root.",
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def list_folders(
        self,
        parent_folder_name: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """
        List all folders and their contents (chats inside each folder).

        :param parent_folder_name: If set, list only child folders under this parent.
                                   Omit to list root‑level folders.
        :return: JSON array of folders, each with its id, name, parent_id, and list of chat titles inside it.
        """
        denied = self._check("enable_list_folders")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # Resolve parent if specified
            parent_id = None
            if parent_folder_name:
                parent = await Folders.get_folder_by_parent_id_and_user_id_and_name(
                    parent_id=None, user_id=user_id, name=parent_folder_name,
                )
                if not parent:
                    return json.dumps({
                        "status": "error",
                        "message": f"Parent folder '{parent_folder_name}' not found.",
                    })
                parent_id = parent.id

            folders = await Folders.get_folders_by_parent_id_and_user_id(parent_id, user_id)
            if not folders:
                return json.dumps([])

            result = []
            for folder in folders:
                chats = await Chats.get_chats_by_folder_id_and_user_id(
                    folder.id, user_id, limit=200
                )
                chat_titles = [c.title for c in chats] if chats else []
                result.append({
                    "id": folder.id,
                    "name": folder.name,
                    "parent_id": folder.parent_id,
                    "chat_count": len(chat_titles),
                    "chats": chat_titles,
                })

            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ── Chat organisation ──

    async def move_chat(
        self,
        chat_id: str,
        folder_name: Optional[str] = None,
        parent_folder_name: Optional[str] = None,
        __user__: dict = None,
    ) -> str:
        """
        Move a chat into a folder (or remove it from folders).

        If the folder doesn't exist, it is created automatically at the specified
        parent level (or root if no parent is given).
        Omit folder_name or set it to empty string to move the chat back to root
        (out of all folders).

        :param chat_id: The ID of the chat to move.
        :param folder_name: Target folder name. Omit or empty to move chat to root.
        :param parent_folder_name: If folder_name doesn't exist and needs to be created,
                                   or to disambiguate when the same folder name exists
                                   in multiple places, specify its parent.
        :return: JSON with status and the resolved folder name (or "root").
        """
        denied = self._check("enable_move_chat")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # Validate chat ownership BEFORE any folder mutation.
            # This prevents orphaned auto-created folders when the chat
            # doesn't exist or the user doesn't own it.
            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
            if not chat:
                if not folder_name:
                    # Moving to root — chat not found, nothing to do
                    return json.dumps({
                        "status": "error",
                        "message": f"Chat {chat_id} not found or access denied.",
                    })
                # Chat doesn't exist AND a folder was specified.
                # We could auto-create the folder anyway (user asked us to),
                # but that's an anti-pattern — a folder created in the service
                # of a chat that doesn't exist is always orphaned.
                return json.dumps({
                    "status": "error",
                    "message": f"Chat {chat_id} not found or access denied. "
                               f"Folder '{folder_name}' was NOT created.",
                })

            target_folder_id = None
            resolved_name = "root"

            if folder_name:
                # Resolve parent if given
                parent_id = None
                if parent_folder_name:
                    parent = await Folders.get_folder_by_parent_id_and_user_id_and_name(
                        parent_id=None, user_id=user_id, name=parent_folder_name,
                    )
                    if not parent:
                        return json.dumps({
                            "status": "error",
                            "message": f"Parent folder '{parent_folder_name}' not found.",
                        })
                    parent_id = parent.id

                # Try to find existing folder by name at the scoped level
                folder_id = await self._resolve_folder_id(
                    folder_name, parent_folder_name, user_id
                )
                if folder_id:
                    target_folder_id = folder_id
                    resolved_name = folder_name
                else:
                    # Create the folder at the scoped level
                    new_folder = await Folders.insert_new_folder(
                        user_id,
                        FolderForm(name=folder_name),
                        parent_id=parent_id,
                    )
                    if not new_folder:
                        return json.dumps({
                            "status": "error",
                            "message": f"Failed to create folder '{folder_name}'.",
                        })
                    target_folder_id = new_folder.id
                    resolved_name = folder_name

            updated = await Chats.update_chat_folder_id_by_id_and_user_id(
                chat_id, user_id, target_folder_id
            )
            if not updated:
                return json.dumps({
                    "status": "error",
                    "message": f"Chat {chat_id} not found or access denied.",
                })
            return json.dumps({
                "status": "success",
                "chat_id": chat_id,
                "folder": resolved_name,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def rename_chat(
        self,
        chat_id: str,
        title: str,
        __user__: dict = None,
    ) -> str:
        """
        Rename a conversation.

        :param chat_id: The ID of the chat to rename.
        :param title: The new title for the conversation.
        :return: JSON with status and the updated title.
        """
        denied = self._check("enable_rename_chat")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # Verify ownership first
            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
            if not chat:
                return json.dumps({
                    "status": "error",
                    "message": f"Chat {chat_id} not found or access denied.",
                })

            # Use the lightweight title-only update — avoids loading & rewriting
            # the full chat JSON blob (significant for chats with many messages)
            result = await Chats.update_chat_title_by_id(chat_id, title)
            if not result:
                return json.dumps({"status": "error", "message": "Failed to rename chat."})
            return json.dumps({
                "status": "success",
                "chat_id": chat_id,
                "title": title,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def archive_chat(
        self,
        chat_id: str,
        __user__: dict = None,
    ) -> str:
        """
        Archive a conversation so it's hidden from the main chat list but not deleted.

        Note: archiving a chat also removes it from any folder (Open WebUI clears
        folder_id when archiving). This is platform behaviour, not a bug.

        :param chat_id: The ID of the chat to archive.
        :return: JSON with status and a descriptive message.
        """
        denied = self._check("enable_archive_chat")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
            if not chat:
                return json.dumps({
                    "status": "error",
                    "message": f"Chat {chat_id} not found or access denied.",
                })

            # Guard: only toggle if not already archived.
            # toggle_chat_archive_by_id flips the current state, so calling it twice
            # would silently unarchive. We also check here because the state is
            # already loaded from the ownership lookup above.
            if not chat.archived:
                await Chats.toggle_chat_archive_by_id(chat_id)

            return json.dumps({
                "status": "success",
                "chat_id": chat_id,
                "archived": True,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def unarchive_chat(
        self,
        chat_id: str,
        __user__: dict = None,
    ) -> str:
        """
        Unarchive a conversation so it appears in the main chat list again.

        :param chat_id: The ID of the chat to unarchive.
        :return: JSON with status and a descriptive message.
        """
        denied = self._check("enable_unarchive_chat")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
            if not chat:
                return json.dumps({
                    "status": "error",
                    "message": f"Chat {chat_id} not found or access denied.",
                })

            if chat.archived:
                await Chats.toggle_chat_archive_by_id(chat_id)

            return json.dumps({
                "status": "success",
                "chat_id": chat_id,
                "archived": False,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    async def delete_chat(
        self,
        chat_id: str,
        __user__: dict = None,
    ) -> str:
        """
        Permanently delete a conversation and all its messages. IRREVERSIBLE.

        Disabled by default. To enable, set enable_delete_chat = True in tool Valves.
        Think carefully before enabling this — even with a good model, accidental deletions
        are hard to recover from.

        :param chat_id: The ID of the chat to delete.
        :return: JSON with status and a descriptive message.
        """
        denied = self._check("enable_delete_chat")
        if denied:
            return denied

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # Ownership check — only the owner can delete their own chat
            chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
            if not chat:
                return json.dumps({
                    "status": "error",
                    "message": f"Chat {chat_id} not found or access denied.",
                })

            deleted = await Chats.delete_chat_by_id_and_user_id(chat_id, user_id)
            if not deleted:
                return json.dumps({
                    "status": "error",
                    "message": "Failed to delete chat.",
                })
            return json.dumps({
                "status": "success",
                "message": f"Chat {chat_id} permanently deleted.",
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ── Advanced search ──

    async def search_chats_advanced(
        self,
        query: str,
        count: int = 10,
        folder_name: Optional[str] = None,
        parent_folder_name: Optional[str] = None,
        exclude_folder_name: Optional[str] = None,
        exclude_parent_folder_name: Optional[str] = None,
        include_archived: bool = False,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        __user__: dict = None,
        __chat_id__: str = None,
    ) -> str:
        """
        Advanced chat search with folder-aware filtering.

        Use this when you need to find conversations within a specific folder,
        exclude a folder, or apply more precise filters than the built-in search_chats.

        :param query: Search text to match against chat titles and messages.
        :param count: Maximum number of results to return (default: 10).
        :param folder_name: If set, only search within this folder.
        :param parent_folder_name: If folder_name is ambiguous, disambiguate by specifying
                                   its parent folder.
        :param exclude_folder_name: If set, exclude all chats in this folder.
        :param exclude_parent_folder_name: If exclude_folder_name is ambiguous, disambiguate
                                           by specifying its parent folder.
        :param include_archived: If True, also search archived chats (default: False).
        :param start_timestamp: Only include chats updated after this Unix timestamp (seconds).
        :param end_timestamp: Only include chats updated before this Unix timestamp (seconds).
        :return: JSON array of matching chats with id, title, folder, snippet, and updated_at.
        """
        denied = self._check("enable_search_chats_advanced")
        if denied:
            return denied

        if not __user__:
            return json.dumps({"status": "error", "message": "User context not available."})

        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"status": "error", "message": "User context not available."})

        try:
            # Resolve folder IDs for include/exclude with required semantics
            include_folder_id = None
            if folder_name:
                fid, error = await self._resolve_folder_id_required(
                    folder_name, parent_folder_name, user_id
                )
                if error:
                    return json.dumps({"status": "error", "message": error})
                include_folder_id = fid

            exclude_folder_id = None
            if exclude_folder_name:
                fid, error = await self._resolve_folder_id_required(
                    exclude_folder_name, exclude_parent_folder_name, user_id
                )
                if error:
                    return json.dumps({"status": "error", "message": error})
                exclude_folder_id = fid

            # Broad fetch — post-filter by folder rather than relying on the
            # built-in search's folder: prefix, which breaks on multi-word names.
            chats = await Chats.get_chats_by_user_id_and_search_text(
                user_id=user_id,
                search_text=query,
                include_archived=include_archived,
                skip=0,
                limit=count * 3,
            )

            results = []
            for chat in chats:
                # Skip the current chat to avoid showing it in search results.
                # This matches the behaviour of the built-in search_chats tool.
                if __chat_id__ and chat.id == __chat_id__:
                    continue

                # Post-filter: include only specific folder
                if include_folder_id is not None and chat.folder_id != include_folder_id:
                    continue

                # Post-filter: exclude folder
                if exclude_folder_id is not None and chat.folder_id == exclude_folder_id:
                    continue

                # Date filters
                if start_timestamp and chat.updated_at < start_timestamp:
                    continue
                if end_timestamp and chat.updated_at > end_timestamp:
                    continue

                # Get folder name for display
                chat_folder_name = None
                if chat.folder_id:
                    folder = await Folders.get_folder_by_id_and_user_id(
                        chat.folder_id, user_id
                    )
                    if folder:
                        chat_folder_name = folder.name

                # Extract snippet
                snippet = ""
                messages = chat.chat.get("history", {}).get("messages", {})
                lower_query = query.lower()

                for msg_id, msg in messages.items():
                    content = msg.get("content", "")
                    if isinstance(content, str) and lower_query in content.lower():
                        idx = content.lower().find(lower_query)
                        start = max(0, idx - 50)
                        end = min(len(content), idx + len(query) + 100)
                        snippet = (
                            ("..." if start > 0 else "")
                            + content[start:end]
                            + ("..." if end < len(content) else "")
                        )
                        break

                if not snippet and lower_query in chat.title.lower():
                    snippet = f"Title match: {chat.title}"

                results.append({
                    "id": chat.id,
                    "title": chat.title,
                    "folder": chat_folder_name,
                    "snippet": snippet,
                    "updated_at": chat.updated_at,
                })

                if len(results) >= count:
                    break

            return json.dumps(results, ensure_ascii=False)
        except Exception as e:
            log.exception(e)
            return json.dumps({"status": "error", "message": str(e)})

    # ── Current chat identity ──

    async def get_current_chat_id(
        self,
        __chat_id__: str = None,
        __user__: dict = None,
    ) -> str:
        """
        Return the ID and title of the current conversation the agent is running in.

        Useful for self‑awareness: the agent can discover which chat it's operating in,
        then use other tools like rename_chat, archive_chat, or move_chat on itself.

        :return: JSON with the current chat's id and title (or a descriptive error).
        """
        denied = self._check("enable_get_current_chat_id")
        if denied:
            return denied

        if not __chat_id__:
            return json.dumps({
                "status": "error",
                "message": "Chat ID not available. This may not be running inside a chat context.",
            })

        user_id = __user__.get("id") if __user__ else None
        if not user_id:
            return json.dumps({
                "status": "success",
                "chat_id": __chat_id__,
                "title": None,
            })

        try:
            chat = await Chats.get_chat_by_id_and_user_id(__chat_id__, user_id)
            title = chat.title if chat else None
            return json.dumps({
                "status": "success",
                "chat_id": __chat_id__,
                "title": title,
            })
        except Exception as e:
            log.exception(e)
            return json.dumps({
                "status": "success",
                "chat_id": __chat_id__,
                "title": None,
            })
