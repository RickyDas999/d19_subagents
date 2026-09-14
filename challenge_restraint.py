"""CHALLENGE: teach the router restraint with a second subagent.

Two subagents -- order-lookup and delay-analyst -- and a router rulebook
that decides, per request, how many of them to actually use:

    Always start with order-lookup.
    If its answer is complete ......... -> stop, 0 more delegations.
    If its answer needs a follow-up ... -> call delay-analyst, 1 more.
    Never delegate "just in case".

Three requests, three different delegation counts, same code, no branching
written by us -- the router decides at runtime.
"""

import asyncio
import os

from dotenv import load_dotenv
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"  # cheapest current model -- this is a few short tool-calling turns
DELEGATE_TOOLS = ("Agent", "Task")

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH")
HERE = os.path.dirname(os.path.abspath(__file__))

WHERE_THE_DATA_IS = (
    f"The order data lives in Google spreadsheet ID {SHEET_ID}. Always use "
    "that spreadsheet and never ask anyone for a spreadsheet ID."
)

SUBAGENTS = {
    "order-lookup": AgentDefinition(
        description=(
            "Looks up ONE order in the Order tab and reports its status, "
            "carrier and ETA. Always call this first for any order "
            "question. Returns 'not_found' if the order ID is not in the "
            "sheet."
        ),
        prompt=(
            "You are an order-lookup specialist reporting to another agent. "
            f"{WHERE_THE_DATA_IS} Read the 'Order' tab only (columns Order "
            "ID | Status | Carrier | ETA Days). Report order_id, status, "
            "carrier, eta_days -- nothing else. If the order ID is not in "
            "the tab, report exactly 'not_found'."
        ),
        mcpServers=["sheets"],
        model=MODEL,
    ),
    "delay-analyst": AgentDefinition(
        description=(
            "Reads the Delays tab and reports WHY one order is late. Call "
            "this ONLY after order-lookup has reported a delayed status -- "
            "never before, never 'just in case'. Returns "
            "'no_reason_logged' if no reason has been written down yet."
        ),
        prompt=(
            "You are a delay-analysis specialist reporting to another "
            f"agent. {WHERE_THE_DATA_IS} Read the 'Delays' tab only "
            "(columns Order ID | Reason). Report order_id, reason -- "
            "nothing else. If the order ID has no row, report exactly "
            "'no_reason_logged'."
        ),
        mcpServers=["sheets"],
        model=MODEL,
    ),
}

# The rule to encode, verbatim, as the router's whole job.
options = ClaudeAgentOptions(
    model=MODEL,
    fallback_model=MODEL,
    system_prompt=(
        "You are a concise order-support agent coordinating two "
        "specialists: order-lookup and delay-analyst. Delegate spreadsheet "
        "work with the Agent tool -- never read the sheet yourself.\n\n"
        "The rule, in order:\n"
        "1. If the question needs no order data (nothing you can look up "
        "would answer it), answer directly -- delegate to nobody.\n"
        "2. Otherwise, always start with order-lookup. You cannot answer "
        "an order question without it.\n"
        "3. If order-lookup's status is NOT a delay, stop. One delegation "
        "is enough -- do not call delay-analyst 'just in case'.\n"
        "4. If order-lookup's status IS a delay, call delay-analyst for "
        "that same order ID, and put the reason in your reply.\n\n"
        "Keep your final reply to 2-3 sentences."
    ),
    agents=SUBAGENTS,
    mcp_servers={
        "sheets": {
            "command": "uvx",
            "args": ["--with", "mcp<2", "mcp-google-sheets@latest"],
            "env": {"SERVICE_ACCOUNT_PATH": SERVICE_ACCOUNT_PATH},
            "alwaysLoad": True,
        }
    },
    cwd=HERE,
    env={"MCP_CONNECTION_NONBLOCKING": "0"},
    allowed_tools=[*DELEGATE_TOOLS, "mcp__sheets__*"],
    permission_mode="dontAsk",
    max_turns=12,
)


async def run(question: str) -> int:
    print(f"\n{'=' * 70}\nQUESTION: {question}\n{'=' * 70}")

    delegations = []
    reply = ""

    async for msg in query(prompt=question, options=options):
        if isinstance(msg, AssistantMessage):
            from_router = getattr(msg, "parent_tool_use_id", None) is None
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name in DELEGATE_TOOLS:
                    delegations.append(block.input.get("subagent_type", "?"))
                elif isinstance(block, TextBlock) and from_router and block.text.strip():
                    reply = block.text

    print(f"delegations ({len(delegations)}): {delegations}")
    print(f"REPLY: {reply.strip()}")
    return len(delegations)


async def main() -> None:
    counts = []
    # Needs no sheet data at all -- the router should answer directly.
    counts.append(await run("What's your return policy?"))
    # Not delayed -- one delegation (order-lookup), then stop.
    counts.append(await run("What is the status of order 3001?"))
    # Delayed -- two delegations (order-lookup, then delay-analyst).
    counts.append(await run("Why is order 1002 taking so long?"))

    print(f"\n{'=' * 70}\nDELEGATION COUNTS ACROSS THE THREE REQUESTS: {counts}")
    if len(set(counts)) < 2:
        print("WARNING: expected the counts to differ -- restraint rule "
              "may not be kicking in.")


if __name__ == "__main__":
    if not SERVICE_ACCOUNT_PATH or not os.path.isfile(SERVICE_ACCOUNT_PATH):
        raise SystemExit("SERVICE_ACCOUNT_PATH is missing or wrong -- see cmd.txt")
    asyncio.run(main())
