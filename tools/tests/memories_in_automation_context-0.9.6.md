This is a test of memory access in an Open WebUI Automations context. If any
of the steps below fail, ABORT IMMEDIATELY, report the error, and stop.

Please tell me the exact call syntax that you use to call the memory tools, and
their corresponding output formats: `add_memory`, `replace_memory_content`,
`delete_memory`, `list_memories`, and `search_memories`.

For completeness, please do the following in order:

---

## Phase 1 — Happy path (success cases)

### Step 1: Add a memory
Call `add_memory` with content: `"MEMTEST 20260618"`.
**Expected:** `{"status": "success", "id": "<uuid>"}` — save the returned `id` for
later steps.

Report the output in a code block.

### Step 2: Edit the memory
Call `replace_memory_content` on the newly added memory with
`content="MEMTEST 20260618 edited"`.
**Expected:** `{"status": "success", "id": "<same-id>", "content": "MEMTEST 20260618 edited"}`.

Report the output.

### Step 3: List all memories
Call `list_memories` and provide a sample of the output, including at least 2
memories from the result. The list **must** contain an entry with
`"content": "MEMTEST 20260618 edited"`, whose `id` matches Step 1.

### Step 4: Search for a relevant memory
Call `search_memories` with `query="MEMTEST"` and `count=2`.
**Expected:** An array whose first entry has `"content": "MEMTEST 20260618 edited"`
and `"id"` matching Step 1.

Report the output.

---

## Phase 2 — Failure paths (error cases)

### Step 5: Edit a memory that does not exist
Call `replace_memory_content` with `memory_id="00000000-0000-4000-8000-000000000000"`
(a well-formed UUID that is guaranteed not to exist) and `content="should never work"`.
**Expected:** `{"error": "..."}` — any error response. **Must NOT** be
`{"status": "success", ...}`.

Report the output.

### Step 6: Delete a memory that does not exist
Call `delete_memory` with `memory_id="00000000-0000-4000-8000-000000000000"`.
**Expected:** `{"error": "Memory not found or access denied"}`.

Report the output.

### Step 7: Search for something that will not match
Call `search_memories` with `query="xyznonexistent12345"` and `count=5`.
**Expected:** `[]` (empty array). **Must NOT** contain any memory entries,
and must NOT be an error object.

Report the output.

---

## Phase 3 — Cleanup and verification

### Step 8: Delete the MEMTEST memory
Call `delete_memory` on the memory created in Step 1 (`memory_id` from Step 1).
**Expected:** `{"status": "success", "message": "Memory <id> deleted"}`.

### Step 9: Verify the memory is gone (list)
Call `list_memories`.
**Expected:** The result does **NOT** contain any entry with
`"content": "MEMTEST 20260618 edited"`.

### Step 10: Verify the memory is gone (search)
Call `search_memories` with `query="MEMTEST 20260618"` and `count=5`.
**Expected:** `[]` (empty array), or an array that does **NOT** contain any entry
with `"content": "MEMTEST 20260618 edited"`.

### Step 11: Confirm the non-existent-memory delete still fails
Call `delete_memory` again with `memory_id="00000000-0000-4000-8000-000000000000"`.
**Expected:** `{"error": "Memory not found or access denied"}` — unchanged
behaviour, proving Step 6 was not a fluke.

---

## Phase 4 — Guard rail: bogus memory_id that uses the correct user's memories table

A subtly different class of error: `delete_memory` with a UUID that is NOT the
user's own memory but belongs to someone else (or was already deleted). The tool
implementation uses `Memories.delete_memory_by_id_and_user_id(memory_id, user.id)`,
which returns `False` when the memory is not found for THIS user specifically.
Since we can't know another user's memory IDs, the "not exists" test from Step 6
already exercises this code path — but it's worth documenting that the
protection is per-user, not global.

---

✅ All 11 steps passed.

---

## Smoke test (quick)

For a rapid sanity check in a new automation:

1. `add_memory` content=`"SMOKETEST"` → `{"status": "success", "id": "..."}`
2. `list_memories` → must contain `"SMOKETEST"`.
3. `search_memories` query=`"SMOKETEST"` → must contain `"SMOKETEST"`.
4. `replace_memory_content` with the saved id + content=`"SMOKETEST edited"` → success.
5. `replace_memory_content` with bogus id → `{"error": "..."}`.
6. `delete_memory` with bogus id → `{"error": "Memory not found or access denied"}`.
7. `delete_memory` with the real id → success.
8. `list_memories` → must NOT contain `"SMOKETEST"`.

---

## Notes on test design

**Why Steps 5-7 matter.** The original test only validated the happy path.
In production, an agent might hallucinate a memory ID and call
`replace_memory_content` expecting it to work. The tool must report failure
cleanly (a JSON error object, not a broken response or 500), so the agent
can self-correct. Step 5 tests the 404-from-router path; Step 6 tests the
"not found for this user" path.

**Why Step 7 tests an empty result, not an error.** `search_memories` uses
vector similarity. The query `"xyznonexistent12345"` should have no relevant
matches, returning `[]`. This is correct behavior — semantic search naturally
returns nothing for nonsense queries. It must NOT return an error object,
because "no matches" is not an error condition.

**Why Step 11 re-tests the non-existent delete.** After the cleanup steps
have run, we can be certain the target memory is gone. A second bogus-ID
delete should still produce the same error — proving the first was not an
artifact of system state.

**Phase 4 documents a non-obvious design detail.** The delete tool uses
`delete_memory_by_id_and_user_id`, not a global `delete_memory_by_id`. A UUID
that exists in *some* user's collection but not the calling user's will still
fail. The bogus-UUID test exercises the same `False` return path, so we don't
need a separate test — but the reader should understand why the tool behaves
this way.

**Per-user isolation.** All memory tools are scoped to the calling user via
`__user__` injection. This means:
- `list_memories` only returns the caller's memories.
- `search_memories` only searches the caller's vector collection.
- `replace_memory_content` and `delete_memory` fail if the memory doesn't
  belong to the caller.
These are correct and intentional — the automation runs as the user who
created it.
