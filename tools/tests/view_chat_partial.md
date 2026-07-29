# view_chat_partial — Test Suite (v1.3.1)

This is a test of the `view_chat_partial` workspace tool in a live Open WebUI
chat context. If any step fails or produces unexpected output, ABORT IMMEDIATELY.
Report what step failed, what was expected, and what was received. Do not
continue past a failure.

## Pre-requisites (for admin; the agent should ignore this section)
- Import `view_chat_partial.py` into Open WebUI as a workspace tool.
- Also attach `chat_management.py` (needed for `get_current_chat_id` in Phase 1).
- Attach both tools to the model that will run this test.
- Leave `view_chat_partial` Valves at defaults (`max_messages=200`).
- This should be a clean conversation — the chat is its own test data.

---

## Phase 1 — Self-identification

### Step 1: Discover current chat
```
Call get_current_chat_id with no arguments.
```
**Expected:** `{"status": "success", "chat_id": "<non-empty string>", "title": "<string>"}`

Save the `chat_id` — all subsequent `view_chat_partial` calls will use it.

---

## Phase 2 — Baseline (unfiltered retrieval)

### Step 2: Retrieve all messages with no filters
```
Call view_chat_partial with chat_id=<your chat_id> (no other parameters).
```
**Expected:** A markdown-formatted string (not an error and not JSON). Should:
- Start with `# Chat: <title>`
- Include `Messages returned: <N>` where N ≥ 2 (at least the user prompt + this assistant response)
- Contain `**[user]**` and `**[assistant]**` message headers with timestamps in `_YYYY-MM-DD HH:MM:SS UTC_` format
- Include the content of messages from this conversation

Save the total message count (N) as `total_msgs`. You'll use it to verify other
operations return smaller subsets.

### Step 3: Verify the last message is the most recent
```
Call view_chat_partial with chat_id=<your chat_id>, last_n=1
```
**Expected:** A string containing exactly one message block. It should match the
last message block from Step 2 (the most recent assistant or user message).

---

## Phase 3 — Role filtering

### Step 4: User-only filter
```
Call view_chat_partial with chat_id=<your chat_id>, roles="user"
```
**Expected:** Output must NOT contain `**[assistant]**`. Must contain at least
one `**[user]**` header (the initial test prompt). Fewer messages than `total_msgs`.

### Step 5: Assistant-only filter
```
Call view_chat_partial with chat_id=<your chat_id>, roles="assistant"
```
**Expected:** Output must NOT contain `**[user]**`. Must contain at least one
`**[assistant]**` header. Fewer messages than `total_msgs`.

### Step 6: Multiple roles
```
Call view_chat_partial with chat_id=<your chat_id>, roles="user,assistant"
```
**Expected:** Should return the same total as the unfiltered baseline
(`Messages returned: <total_msgs>`). Since all messages in a normal chat are
user or assistant, this is equivalent to no role filter.

---

## Phase 4 — Timestamp filtering

### Step 7: start_timestamp includes everything
```
Call view_chat_partial with chat_id=<your chat_id>, start_timestamp=0
```
**Expected:** `Messages returned: <total_msgs>` — Unix epoch (1970) is earlier
than any real chat message, so all messages pass the filter.

### Step 8: end_timestamp excludes everything
```
Call view_chat_partial with chat_id=<your chat_id>, end_timestamp=1
```
**Expected:** A string containing `"no messages matched your filters (total messages in chat: <total_msgs>)"`. No message block appears. This proves the timestamp filter is active.

### Step 9: Bounded range that covers everything
```
Call view_chat_partial with chat_id=<your chat_id>, start_timestamp=0, end_timestamp=4102444800
```
(4102444800 = Unix timestamp for year 2100, far in the future.)
**Expected:** `Messages returned: <total_msgs>` — the range covers all realistic
chat timestamps, so all messages pass. The output header should include a `From:`
and `To:` line with human-readable UTC dates.

---

## Phase 5 — Pagination

### Step 10: First N messages
```
Call view_chat_partial with chat_id=<your chat_id>, from_index=0, count=3
```
**Expected:** `Messages returned: 3 of <total_msgs>`. The messages should be
chronologically ordered (oldest first). Verify the first message is the initial
user prompt for this test, NOT the most recent message.

### Step 11: from_index beyond end
```
Call view_chat_partial with chat_id=<your chat_id>, from_index=9999, count=5
```
**Expected:** A string containing `"no messages matched your filters"`. The
from_index is beyond the end of the chat.

### Step 12: count alone (from_index defaults to 0)
```
Call view_chat_partial with chat_id=<your chat_id>, count=2
```
**Expected:** `Messages returned: 2 of <total_msgs>` — the first 2 messages.

---

## Phase 6 — Context expansion (before_n / after_n)

These are the v1.2.0 feature tests. Context messages are pulled from the full
(unfiltered) conversation, bypassing role and timestamp filters — like grep's
-B and -A flags.

### Step 13: last_n with before_n context
```
Call view_chat_partial with chat_id=<your chat_id>, last_n=1, before_n=2
```
**Expected:**
- `Messages returned: 3 of <total_msgs> (2 before, 0 after context)`
- The output should contain 3 message blocks
- The first 2 are context; the last is the most recent message (the result itself)

### Step 14: from_index=0, count=1 with after_n context
```
Call view_chat_partial with chat_id=<your chat_id>, from_index=0, count=1, after_n=2
```
**Expected:**
- `Messages returned: 3 of <total_msgs> (0 before, 2 after context)`
- The output should contain 3 message blocks
- The first is the result; the next 2 are context

### Step 15: before_n + after_n combined
```
Call view_chat_partial with chat_id=<your chat_id>, last_n=1, before_n=1, after_n=1
```
**Expected:**
- `Messages returned: 3 of <total_msgs> (1 before, 1 after context)`
- 3 message blocks total

### Step 16: Context bypasses role filter
```
Call view_chat_partial with chat_id=<your chat_id>, roles="user", from_index=0, count=1, after_n=2
```
**Expected:** The result message (first) should be `**[user]**`. The 2 context
messages (after) may include `**[assistant]**` — context bypasses the role filter.
If the second message in the chat is assistant, the after-context should include
it. Verify this property holds.

Note: this depends on the specific chat structure. If the first user message is
immediately followed by an assistant message, the after-context will include at
least one `**[assistant]**` message. If it doesn't (e.g., two consecutive user
messages), note the finding but don't fail.

---

## Phase 7 — Error handling

### Step 17: Chat not found
```
Call view_chat_partial with chat_id="00000000-0000-4000-8000-000000000000"
(a well-formed UUID guaranteed not to exist).
```
**Expected:** A string starting with `"Error: Chat"` and containing `"not found"`.

### Step 18: No messages match filters (empty result, not an error)
```
Call view_chat_partial with chat_id=<your chat_id>, roles="system"
```
**Expected:** A string containing `"no messages matched your filters (total messages in chat: <total_msgs>)"`. This is NOT an error — "no matches" is a valid result, not a failure.

### Step 19: Access denied (other user's chat)
```
Call view_chat_partial with chat_id="6a95f250-220c-432c-88e6-955a4cac6e43"
```
**Note:** This is a real chat ID. Whether it belongs to another user or doesn't
exist depends on the deployment. Expected outcomes:
- If the chat exists but belongs to another user: `"Error: Access denied. You can only view your own chats."`
- If the chat doesn't exist: `"Error: Chat '...' not found."`

Either outcome is acceptable; both confirm the gate is active. Report which
outcome occurred.

---

## Phase 8 — Interaction with other parameters

### Step 20: Role + last_n combined
```
Call view_chat_partial with chat_id=<your chat_id>, roles="user", last_n=2
```
**Expected:** Returns at most 2 messages, all `**[user]**`. No `**[assistant]**`
present. The messages should be the last 2 user messages in the conversation.

### Step 21: Timestamp + pagination combined
```
Call view_chat_partial with chat_id=<your chat_id>, start_timestamp=0, from_index=0, count=1
```
**Expected:** `Messages returned: 1 of <total_msgs>` — the first message.

### Step 22: Output is deterministic
Re-run Step 2 (unfiltered baseline) a second time.
```
Call view_chat_partial with chat_id=<your chat_id> (no other parameters).
```
**Expected:** Same `Messages returned: <N>` count as the first baseline call.
The tool is read-only and idempotent.

---

## Phase 9 — Smoke test

For a rapid sanity check after importing a new version:

1. `get_current_chat_id` → save chat_id.
2. `view_chat_partial(chat_id)` → returns markdown, not error. Contains `# Chat:`, message count, message blocks.
3. `view_chat_partial(chat_id, last_n=1)` → exactly one message.
4. `view_chat_partial(chat_id, roles="user")` → no `**[assistant]**`.
5. `view_chat_partial(chat_id, roles="assistant")` → no `**[user]**`.
6. `view_chat_partial(chat_id, start_timestamp=0)` → all messages (proves filter works).
7. `view_chat_partial(chat_id, end_timestamp=1)` → "no messages matched" (proves filter active).
8. `view_chat_partial(chat_id, from_index=0, count=3)` → first 3 messages.
9. `view_chat_partial(chat_id, last_n=1, before_n=2)` → 3 messages, "(2 before" in count line.
10. `view_chat_partial(chat_id="bogus-id")` → error with "not found".
11. Re-run (2) → same output (idempotent).

---

✅ All steps passed.

---

## Notes on test design

**Why this is a live test, not just a Python unit test.** `test_view_chat_partial.py`
validates the filter logic in isolation with synthetic message data. That covers
before_n/after_n arithmetic, filter ordering, and edge cases like boundary
clamping. This Markdown test validates that the tool works end-to-end in Open WebUI:
the `__user__` injection functions, the `Chats` model resolves, the message
format is parsed correctly from real chat history, the ownership gate fires, and
the output formatting matches what the agent actually receives.

**Why the test chat IS the test data.** Unlike `memories_in_automation_context`
(which creates and destroys test memories), `view_chat_partial` is purely
read-only. The conversation running the test provides the message history. Each
step adds new messages, so a `last_n` call at step N+1 returns the assistant
response from step N. This is deterministic and self-validating.

**Why Step 8 uses `end_timestamp=1` for empty results.** Unix epoch start
(1970-01-01) is earlier than any real chat message. Setting `end_timestamp=1`
means "only return messages with timestamp ≤ 1 second after epoch" — of which
there are none. Using `start_timestamp=0` + `end_timestamp=4102444800` (year 2100)
covers the full range. These boundary values are simple, require no math, and
never collide with real timestamps.

**Why Step 16 tests context bypassing roles.** The v1.2.0 before_n/after_n
feature explicitly bypasses all filters when pulling context messages. This
matches grep's -B/-A behavior: context lines may not match the filter but
provide surrounding context. If the first message is user and the second is
assistant, `roles="user", from_index=0, count=1, after_n=2` should include an
assistant message in the after-context — proving the bypass works.

**Why Step 19 accepts two outcomes.** We can't know whether the hardcoded chat
ID belongs to another user or doesn't exist on the tester's deployment. Both
the "access denied" and "not found" paths confirm the ownership gate is active:
the tool checks `chat.user_id != user_id` before returning content. Either
outcome proves the tool isn't serving other users' chats.

**Why there's no Valve cap test.** The `max_messages` valve defaults to 200.
Lowering it to test the cap requires admin intervention mid-test (like
`chat_management.md` Phase 9). The cap logic is trivial — `ordered[-max_messages:]` —
and is thoroughly tested in the Python unit tests (last two cases). Skipping the
live admin-intervention step keeps the test self-contained.

**What's NOT tested here (covered by Python unit tests instead):**
- Precise before_n/after_n arithmetic for edge cases (boundary clamping, zero
  values, overlap prevention)
- Orphan message exclusion in `_walk_history` (requires manually constructed
  message maps with broken parent chains)
- `max_messages` cap behavior (requires valve changes)
- `_reconstruct_from_output_array` for v0.10+ format (depends on whether the
  chat was generated with structured output arrays)
- `_html_entity_encode` escaping correctness

**Per-user isolation.** `view_chat_partial` gates on `chat.user_id != user_id`
using `__user__["id"]`. Steps 17 and 19 exercise the gate's two failure paths
(not found vs. access denied). The tool never serves a chat owned by a different
user.
