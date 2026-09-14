"""CORE: one router, one subagent, one delegated request.

Stands up a coordinator (router) with a single specialist subagent. The
subagent's only job is to answer one narrow kind of question -- looking up
an order in the "Order" tab of a Google Sheet. The router decides whether
to hand off the work and writes the final reply; it never reads the sheet
itself.
"""

import asyncio
import os

from dotenv import load_dotenv
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"

# The CLI has answered to both "Agent" and "Task" for the delegation tool
# across versions -- watch for both so a rename doesn't look like "no
# delegation happened".
DELEGATE_TOOLS = ("Agent", "Task")

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH")
HERE = os.path.dirname(os.path.abspath(__file__))

WHERE_THE_DATA_IS = (
    f"The order data lives in Google spreadsheet ID {SHEET_ID}. Always use "
    "that spreadsheet and never ask anyone for a spreadsheet ID."
)

# Step 1: one subagent, as a dictionary entry. `description` says WHEN to
# use it (the router's routing signal), `prompt` is what the subagent is
# told once it is picked, and `mcpServers` is the only server it may reach.
# No `tools=[...]` -- it sees the server's whole catalogue and picks for
# itself.
SUBAGENTS = {
    "order-lookup": AgentDefinition(
        description=(
            "Looks up ONE order in the Order tab and reports its status, "
            "carrier and ETA. Use this for any question about an order's "
            "status. Returns 'not_found' if the order ID is not in the sheet."
        ),
        prompt=(
            "You are an order-lookup specialist reporting to another agent. "
            f"{WHERE_THE_DATA_IS} Read the 'Order' tab only, which has "
            "columns Order ID | Status | Carrier | ETA Days. Report "
            "order_id, status, carrier, eta_days -- nothing else."
        ),
        mcpServers=["sheets"],
        model=MODEL,
    ),
}

# Step 2: the router. Short system prompt whose whole job is "delegate,
# don't do the work yourself".
options = ClaudeAgentOptions(
    model=MODEL,
    fallback_model=MODEL,
    system_prompt=(
        "You are a concise order-support agent. You have one specialist, "
        "order-lookup. Delegate any question about an order to it with the "
        "Agent tool -- do not read the spreadsheet yourself. Write the "
        "final reply to the customer from what the specialist reports back."
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
    # Step 3: ONE global approval list -- the delegation tool and the
    # server's tools both go here, even though only the subagent ever
    # calls the sheets tools.
    allowed_tools=[*DELEGATE_TOOLS, "mcp__sheets__*"],
    permission_mode="dontAsk",
    max_turns=15,
)


async def run(question: str) -> None:
    """Step 4: send one request and prove the work was delegated."""
    print(f"\n{'=' * 60}\nQUESTION: {question}\n{'=' * 60}")

    delegated = False
    router_reply = ""

    async for msg in query(prompt=question, options=options):
        if isinstance(msg, AssistantMessage):
            from_router = getattr(msg, "parent_tool_use_id", None) is None
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name in DELEGATE_TOOLS:
                    delegated = True
                    print(f"[delegation] router called {block.name} -> "
                          f"{block.input.get('subagent_type', '?')}")
                elif isinstance(block, TextBlock) and from_router and block.text.strip():
                    router_reply = block.text
        elif isinstance(msg, ResultMessage):
            print(f"[turn ended] terminal_reason={msg.terminal_reason}")

    print(f"\nROUTER REPLY:\n{router_reply}")
    print(f"\ndelegated: {delegated}")
    if not delegated:
        print("FAILED: the router answered without handing off to order-lookup.")


if __name__ == "__main__":
    if not SERVICE_ACCOUNT_PATH or not os.path.isfile(SERVICE_ACCOUNT_PATH):
        raise SystemExit("SERVICE_ACCOUNT_PATH is missing or wrong -- see cmd.txt")
    asyncio.run(run("What is the status of order 3001?"))
