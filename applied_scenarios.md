# Applied — five scenarios

## 1. A subagent needs a customer ID the router looked up a moment ago. Where must that ID be, for the subagent to use it?

It must be written into the delegation prompt itself — the `prompt` argument of the Agent/Task call. That is the only thing that crosses into a subagent's fresh session; the router's own conversation history never does. (Proven directly in `stretch_isolation.py`: the router said the order ID out loud, but the subagent only succeeded once that ID was written into the delegation prompt by hand.)

## 2. You want the router to hand off spreadsheet work but never touch the sheet itself. Which tool must the router have — and which must it be told not to use?

The router must have the **Agent** (delegation) tool. It must be *told*, not technically prevented, not to call the `mcp__sheets__*` tools itself — because `allowed_tools` is one global list, those sheets tools are present and approved in the router's own session too (the specialists need them). The only thing stopping the router from reading the sheet directly is its system prompt telling it to delegate every read.

## 3. A specialist returns a fact it did not actually read, phrased as a confident guess. Why is this the most damaging thing a specialist can return?

Because the router has no way to tell a confident guess apart from a verified read — it trusts the specialist's report and passes it straight on. An explicit `not_found` or `no_reason_logged` is a real, useful answer the router can act on; a fabricated-but-confident one poisons everything downstream silently, with no error to catch and no way to know it happened until a customer is told something false with full confidence.

## 4. The router's approval list already contains the specialists' tools. Does that mean the router itself may call those tools? What actually keeps it in its lane?

Yes — technically it may. `allowed_tools` is one global list, so nothing at the permission-system level blocks the router from calling `mcp__sheets__*` directly; it has the same approval the specialists do. What actually keeps it in its lane is purely behavioral: the system prompt telling it to delegate rather than do the work itself. The boundary is a rule the router follows, not a wall it can't get past.

## 5. To make the team faster, a teammate proposes splitting 'read one tab' into three smaller subagents that each read part of it. Good idea or not — and why?

**Bad idea — this is the one that adds cost without adding correctness.** Reading one tab is already a single atomic unit of work. Splitting it three ways multiplies delegation round-trips (three model calls, three MCP connections) for a job one subagent already does in one shot, and it adds a new failure mode that didn't exist before: three independent reads of the same data can come back inconsistent with each other, and now the router has to reconcile contradictions instead of just relaying one clean answer.
