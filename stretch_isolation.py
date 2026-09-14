"""STRETCH: prove a subagent starts fresh -- then break it on purpose.

Two runs of the same request. In each, the router states an order ID back
to the customer in its OWN reply, then delegates to the order-lookup
subagent. Run 1 tells the router to delegate WITHOUT repeating the ID in
the delegation prompt -- the subagent has no history to fall back on, so
it fails. Run 2 tells the router to put the ID IN the delegation prompt --
the only thing that crosses the boundary -- and the subagent succeeds.
"""

import asyncio
import os

from dotenv import load_dotenv
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

load_dotenv()

# Cheapest current model -- this whole exercise is a handful of short
# tool-using turns, not a task that benefits from a bigger model.
MODEL = "claude-haiku-4-5-20251001"
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
            "carrier and ETA. Needs an order ID to do anything -- returns "
            "'no_order_id_given' if it was not handed one."
        ),
        prompt=(
            "You are an order-lookup specialist reporting to another agent. "
            f"{WHERE_THE_DATA_IS} Read the 'Order' tab only (columns Order "
            "ID | Status | Carrier | ETA Days). You only know what is in "
            "THIS prompt -- you were not part of whatever conversation "
            "happened before you were called. If no order ID was given to "
            "you here, do not guess or invent one: report exactly "
            "'no_order_id_given'."
        ),
        mcpServers=["sheets"],
        model=MODEL,
    ),
}

options = ClaudeAgentOptions(
    model=MODEL,
    fallback_model=MODEL,
    system_prompt=(
        "You are a concise order-support agent with one specialist, "
        "order-lookup. Follow the customer's instructions about exactly "
        "how to phrase your delegation -- they are testing your handoff "
        "behavior on purpose."
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
    max_turns=10,  # small cap -- this is a short, cheap demo, not a long task
)


async def run(label: str, user_message: str) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")

    router_text = ""
    delegation_prompt = None
    subagent_reply = ""

    async for msg in query(prompt=user_message, options=options):
        if isinstance(msg, AssistantMessage):
            from_router = getattr(msg, "parent_tool_use_id", None) is None
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name in DELEGATE_TOOLS:
                    delegation_prompt = block.input.get("prompt", "")
                elif isinstance(block, TextBlock) and block.text.strip():
                    if from_router:
                        router_text += block.text
                    else:
                        subagent_reply += block.text
        elif isinstance(msg, ResultMessage):
            pass

    print(f"ROUTER'S OWN TURN (never sent to the subagent):\n  {router_text.strip()}")
    print(f"\nWHAT CROSSED THE BOUNDARY (the literal delegation prompt):\n  {delegation_prompt!r}")
    print(f"\nSUBAGENT'S ANSWER:\n  {subagent_reply.strip()}")


async def main() -> None:
    # Run 1: the router states the order ID to the customer, then is told
    # to hand off WITHOUT repeating it. The subagent gets nothing to work
    # with -- the fact never crosses.
    await run(
        "RUN 1 -- fact left out of the delegation prompt",
        "A customer is asking about order 3001. First, tell me the order "
        "ID back so I know you have it. Then delegate to order-lookup, "
        "but phrase your delegation generically -- just ask it to 'look "
        "up the order the customer mentioned' without repeating the ID "
        "anywhere in the delegation prompt.",
    )

    # Run 2: same request, but this time the ID is written into the
    # delegation prompt by hand -- the only thing that crosses.
    await run(
        "RUN 2 -- fact written into the delegation prompt by hand",
        "A customer is asking about order 3001. First, tell me the order "
        "ID back so I know you have it. Then delegate to order-lookup, "
        "and this time explicitly include the order ID 3001 in the "
        "delegation prompt.",
    )


if __name__ == "__main__":
    if not SERVICE_ACCOUNT_PATH or not os.path.isfile(SERVICE_ACCOUNT_PATH):
        raise SystemExit("SERVICE_ACCOUNT_PATH is missing or wrong -- see cmd.txt")
    asyncio.run(main())
