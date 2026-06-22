This is a test of the Chat & Folder Management workspace tool (v1.2.2) in an
Open WebUI chat context. If any step fails or produces unexpected output, ABORT
IMMEDIATELY. Report what step failed, what was expected, and what was received.
Do not continue past a failure.

## Pre-requisites (for admin; the agent should ignore this section)
- Import chat_management.py into Open WebUI as a workspace tool.
- Attach the tool to the model that will run this test.
- Leave all Valves at their defaults (delete_folder and delete_chat are DISABLED).
- This should be a clean conversation with no pre-existing folders named "CTM_*".

---

## Phase 1 — Self-identification

### Step 1: Discover current chat
```
Call get_current_chat_id with no arguments.
```
**Expected:** `{"status": "success", "chat_id": "<non-empty string>", "title": "<string>"}`

Save the `chat_id` — you'll use it throughout the rest of the test.

---

## Phase 2 — Folder creation

### Step 2: Create a root-level folder
```
Call create_folder with name="CTM_Test"
```
**Expected:** `{"status": "success", "name": "CTM_Test", "parent_id": null, "id": "<uuid>"}`

### Step 3: Create a nested folder
```
Call create_folder with name="CTM_Nested", parent_folder_name="CTM_Test"
```
**Expected:** `{"status": "success", "name": "CTM_Nested", "parent_id": "<non-null uuid>"}`

### Step 4: Create under a non-existent parent (error check)
```
Call create_folder with name="Nope", parent_folder_name="NoSuchFolder"
```
**Expected:** `{"status": "error", "message": "Parent folder 'NoSuchFolder' not found."}`

---

## Phase 3 — Folder listing

### Step 5: List root folders
```
Call list_folders with no arguments.
```
**Expected:** A JSON array containing an entry with `"name": "CTM_Test"`,
`"parent_id": null`, `"chat_count": 0`. CTM_Nested should NOT appear (it's a child).

### Step 6: Drill into CTM_Test's children
```
Call list_folders with parent_folder_name="CTM_Test"
```
**Expected:** An array containing `"name": "CTM_Nested"`.

### Step 7: List children of non-existent parent (error check)
```
Call list_folders with parent_folder_name="NoSuchParent"
```
**Expected:** `{"status": "error", "message": "Parent folder 'NoSuchParent' not found."}`

---

## Phase 4 — Folder renaming

### Step 8: Rename the root folder
```
Call rename_folder with folder_name="CTM_Test", new_name="CTM_Renamed"
```
**Expected:** `{"status": "success", "name": "CTM_Renamed"}`

### Step 9: Rename a non-existent folder (error check)
```
Call rename_folder with folder_name="NoSuchFolder", new_name="Anything"
```
**Expected:** `{"status": "error", "message": "Folder 'NoSuchFolder' not found. If the folder is inside another folder, specify parent_folder_name."}`

### Step 10: Verify rename via list
```
Call list_folders with no arguments.
```
**Expected:** Contains `"name": "CTM_Renamed"`. Does NOT contain `"name": "CTM_Test"`.

---

## Phase 5 — Chat movement and renaming

### Step 11: Move this chat into the renamed folder
```
Call move_chat with chat_id=<your chat_id>, folder_name="CTM_Renamed"
```
**Expected:** `{"status": "success", "chat_id": "<your chat_id>", "folder": "CTM_Renamed"}`

### Step 12: Rename this chat
```
Call rename_chat with chat_id=<your chat_id>, title="CTM Test Chat"
```
**Expected:** `{"status": "success", "chat_id": "<your chat_id>", "title": "CTM Test Chat"}`

### Step 13: Move into nested folder using parent disambiguation
```
Call move_chat with chat_id=<your chat_id>, folder_name="CTM_Nested", parent_folder_name="CTM_Renamed"
```
**Expected:** `{"status": "success", "folder": "CTM_Nested"}`

### Step 14: Move to a folder that doesn't exist yet (auto-create)
```
Call move_chat with chat_id=<your chat_id>, folder_name="CTM_AutoCreated"
```
**Expected:** `{"status": "success", "folder": "CTM_AutoCreated"}` — the folder should be created at root level automatically.

### Step 15: Move back to CTM_Renamed for search tests
```
Call move_chat with chat_id=<your chat_id>, folder_name="CTM_Renamed"
```
**Expected:** `{"status": "success", "folder": "CTM_Renamed"}`

### Step 16: Move a non-existent chat (error check)
```
Call move_chat with chat_id="nonexistent-chat-id-99999", folder_name="Anything"
```
**Expected:** `{"status": "error", ...}` (chat not found or access denied)

---

## Phase 6 — Advanced search

### Step 17: Current chat exclusion (v1.2.2 fix)
```
Call search_chats_advanced with query="CTM Test", count=10
```
(No folder filter — this is a broad search to confirm the current chat is always excluded.)
**Expected:** Array that does NOT contain the current chat (`"title": "CTM Test Chat"`).
This proves `__chat_id__` injection and exclusion are working, matching built-in search_chats behaviour.

### Step 18: Search within a specific folder
```
Call search_chats_advanced with query="CTM Test", folder_name="CTM_Renamed", count=5
```
**Expected:** Empty array `[]` or array containing only non-current chats with `"folder": "CTM_Renamed"`.
The current chat IS in CTM_Renamed but is excluded by the Step 17 guard (__chat_id__ check runs
before folder filtering, and the current chat is the only known match in this folder).

### Step 19: Search excluding a folder
```
Call search_chats_advanced with query="CTM Test", exclude_folder_name="CTM_Renamed", count=10
```
**Expected:** Array that does NOT contain the CTM Test Chat entry. (It was excluded — doubly so:
once by self-exclusion, and the folder exclusion provides a second cut.)

### Step 20: Search with non-existent include folder (error check)
```
Call search_chats_advanced with query="anything", folder_name="NoSuch", count=5
```
**Expected:** `{"status": "error", "message": "Folder 'NoSuch' not found. ..."}`

### Step 21: Search with non-existent exclude folder (error check)
```
Call search_chats_advanced with query="anything", exclude_folder_name="NoSuch", count=5
```
**Expected:** `{"status": "error", "message": "Folder 'NoSuch' not found. ..."}`

---

## Phase 7 — Archive / Unarchive

### Step 22: Archive this chat
```
Call archive_chat with chat_id=<your chat_id>
```
**Expected:** `{"status": "success", "archived": true}`

### Step 23: Archive again (idempotency check — the bug fix)
```
Call archive_chat again with chat_id=<your chat_id>
```
**Expected:** `{"status": "success", "archived": true}` — still true, NOT toggled back to false.

### Step 24: Unarchive
```
Call unarchive_chat with chat_id=<your chat_id>
```
**Expected:** `{"status": "success", "archived": false}`

---

## Phase 8 — Disabled-by-default valve checks

### Step 25: delete_folder should be rejected
```
Call delete_folder with folder_name="CTM_AutoCreated"
```
**Expected:** `{"status": "error", "message": "Operation 'delete_folder' is disabled via tool Valves."}`

### Step 26: delete_chat should be rejected
```
Call delete_chat with chat_id="any-string"
```
**Expected:** `{"status": "error", "message": "Operation 'delete_chat' is disabled via tool Valves."}`

---

## Phase 9 — Cleanup

➡️ **Admin action:** Before running the steps below, go to Workspace → Tools →
chat_management → Valves and set `enable_delete_folder` to **True**.

### Step 27: Delete the renamed folder tree
```
Call delete_folder with folder_name="CTM_Renamed"
```
**Expected:** `{"status": "success", "message": "Folder 'CTM_Renamed' and 1 sub‑folder(s) deleted. Chats moved to root."}`

### Step 28: Delete the auto-created folder
```
Call delete_folder with folder_name="CTM_AutoCreated"
```
**Expected:** `{"status": "success" ...}`

### Step 29: Verify cleanup
```
Call list_folders with no arguments.
```
**Expected:** No entries whose name starts with `"CTM_"`.

### Step 30: Verify our chat was evacuated (not lost)
```
Call get_current_chat_id
```
**Expected:** `{"status": "success", "chat_id": "<same chat_id>"}` — chat still exists, just back at root.

---

## Phase 10 — Final disabled check

### Step 31: Confirm delete_chat is still disabled
```
Call delete_chat with chat_id=<your chat_id>
```
**Expected:** `{"status": "error", "message": "Operation 'delete_chat' is disabled via tool Valves."}`
(Even though we just used delete_folder, each valve is independent.)

---

✅ All 31 steps passed.
```

## Smoke test (quick)

For a rapid sanity check when you just imported a new version and want to know it's not totally broken:

```markdown
This is a smoke test of chat_management.py v1.2.2. ABORT on failure.

1. `get_current_chat_id` → should return chat_id + title.
2. `create_folder` name="SmokeTest" → success.
3. `list_folders` → should contain SmokeTest.
4. `move_chat` with chat_id=<yours>, folder_name="SmokeTest" → success.
5. `rename_chat` with chat_id=<yours>, title="Smoke Test Chat" → success.
6. `search_chats_advanced` query="Smoke Test", folder_name="SmokeTest" → should return empty (self-exclusion).
7. `search_chats_advanced` query="Smoke Test", exclude_folder_name="SmokeTest" → should NOT contain current chat.
8. `search_chats_advanced` query="Smoke Test" (no folder filter) → should NOT contain current chat (exclusion proof).
9. `archive_chat` / `unarchive_chat` → both succeed.
10. `delete_chat` → error about disabled valve.
11. Enable delete_folder valve → `delete_folder` folder_name="SmokeTest" → success.
12. `list_folders` → no SmokeTest.
```

---

A couple of notes on the test design:

**Why Phase 9 requires admin intervention mid-test.** The delete_folder valve defaults to disabled. You can't test "disabled valve" and "successful deletion" in the same valve state. The alternative would be enabling delete_folder from the start and skipping the disabled test — but since the disabled-by-default behavior is a safety invariant worth verifying, I split it.

**Step 23 is the archive toggling regression test.** Without the fix, calling `archive_chat` on an already-archived chat would silently unarchive it while still reporting `archived: true`. The test catches that.

**Search tests use the renamed chat title "CTM Test Chat" as the query.** This verifies that post-filtering works (the chat is in CTM_Renamed) and that the snippet extraction finds title matches too. **Step 17 (added v1.2.2)** validates that the current chat is excluded from results — matching built-in search_chats behaviour via `__chat_id__` injection.

**No test for `delete_folder` evacuation failure.** That code path (`move_chats` returning False) is hard to trigger in a normal test — it would require a DB-level failure. It's covered by the source-verified API contract; a unit test would need mocking.