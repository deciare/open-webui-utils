# Open WebUI Workspace Tools

A collection of workspace tools for [Open WebUI](https://github.com/open-webui/open-webui) that extend the platform's capabilities — primarily designed to support agentic workflows, memory management, and chat organisation.

## Tools

### Chat & Folder Management (`chat_management.py`)

A comprehensive suite for organising conversations. Complements the built-in chat search with folder-aware filtering, and enables automated chat filing from automations / heartbeat patterns.

**Methods:**

| Tool | Description |
|---|---|
| `create_folder` | Create a new folder, optionally nested under a parent |
| `rename_folder` | Rename an existing folder, with parent disambiguation for subfolders |
| `delete_folder` | Delete a folder; chats inside are moved to root (not deleted). Disabled by default via Valve. Cascades through subfolders. |
| `list_folders` | List all folders, optionally scoped to a parent. Includes chat counts. |
| `move_chat` | Move a chat into a folder (or back to root). Auto-creates the folder if it doesn't exist. |
| `rename_chat` | Lightweight chat title update |
| `archive_chat` | Archive a chat (idempotent — won't double-toggle) |
| `unarchive_chat` | Unarchive a chat |
| `delete_chat` | Permanently delete a chat. Disabled by default via Valve. |
| `search_chats_advanced` | Search chats with optional folder inclusion/exclusion filtering |
| `get_current_chat_id` | Discover the agent's own chat ID and title (self-awareness for automation contexts) |

**Safety:** Destructive operations (`delete_folder`, `delete_chat`) are disabled by default and independently gated via Valves. The tool validates chat ownership before any state mutation.

---

### Memories in Automation Context (`memories_in_automation_context.py`)

Brings full memory access to Open WebUI Automations, where the built-in memory tools are unavailable. Mirrors the standard memory API with the same calling conventions.

**Methods:**

| Tool | Description |
|---|---|
| `add_memory` | Store a new long-term memory |
| `replace_memory_content` | Update the content of an existing memory by ID |
| `delete_memory` | Remove a memory by ID |
| `list_memories` | List all stored memories |
| `search_memories` | Semantic search across memories |

These methods follow the same output format as Open WebUI's built-in memory tools, making them a drop-in replacement for automation contexts.

---

### View Chat Partial (`view_chat_partial.py`)

Retrieves a subset of a chat conversation, supporting several access patterns. All access is gated to the current user's own chats.

**Methods:**

| Tool | Parameters | Description |
|---|---|---|
| `view_chat_partial` | `chat_id`, `last_n`, `start_timestamp`, `end_timestamp`, `from_index`, `count`, `roles` | Retrieve a portion of a conversation — tail end, time range, or paginated range. Optionally filter by role (user, assistant, system, etc.) |

**Access patterns:**
- `last_n=10` — get the last 10 messages
- `start_timestamp=...` + `end_timestamp=...` — get messages within a time window
- `from_index=0` + `count=50` — paginate through full history
- `roles="user,assistant"` — only include specific message roles