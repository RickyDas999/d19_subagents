
import asyncio, os
from dotenv import load_dotenv

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 30
MAX_OUTPUT_TOKENS = 1024

# The CLI renamed the delegation tool from "Task" to "Agent" and answers to
# both. Get the name wrong and the delegation silently does not count as one.
DELEGATE_TOOLS = ("Agent", "Task")

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH")
HERE = os.path.dirname(os.path.abspath(__file__))

# The sheet ID is configuration, so it goes in the prompt, not the question.
WHERE_THE_DATA_IS = (
    f"The order data lives in Google spreadsheet ID {SHEET_ID}. Always use that "
    "spreadsheet and never ask anyone for a spreadsheet ID."
)


# ---------- The two specialists ----------
# `description` is what the ROUTER reads when it picks who to hand the job to.
SUBAGENTS = {
    "order-lookup": AgentDefinition(
        description=(
            "Looks up ONE order in the Order tab and reports its status, carrier "
            "and ETA. Use this first for any order question. Returns 'not_found' "
            "if the order ID is not in the sheet."
        ),
        prompt=(
            "You are an order-lookup specialist reporting to another agent. "
            f"{WHERE_THE_DATA_IS} Read the 'Order' tab only."
        ),
        skills=["order-lookup"],
        mcpServers=["sheets"],
        # The one hard boundary: a specialist cannot delegate onward.
        disallowedTools=list(DELEGATE_TOOLS),
        model=MODEL,
    ),
    "delay-analyst": AgentDefinition(
        description=(
            "Reads the Delays tab and reports WHY one order is late. Use only "
            "after order-lookup reports a delayed status. Returns "
            "'no_reason_logged' if no reason has been written down yet."
        ),
        prompt=(
            "You are a delay-analysis specialist reporting to another agent. "
            f"{WHERE_THE_DATA_IS} Read the 'Delays' tab only."
        ),
        skills=["delay-analysis"],
        mcpServers=["sheets"],
        disallowedTools=list(DELEGATE_TOOLS),
        model=MODEL,
    ),
}

# No tools=[...] on either specialist: each sees the server's whole catalogue
# and picks for itself, rather than us choosing and it filling in arguments.


# ---------- The router ----------
options = ClaudeAgentOptions(
    model=MODEL,
    fallback_model=MODEL,
    # Short on purpose -- the routing rules live in the support-router Skill.
    system_prompt=(
        "You are a concise order-support agent coordinating two specialists. "
        "Delegate the spreadsheet work with the Agent tool; do not read the "
        "sheet yourself."
    ),
    agents=SUBAGENTS,
    mcp_servers={
        "sheets": {
            "command": "uvx",
            "args": ["--with", "mcp<2", "mcp-google-sheets@latest"],
            # Credentials go to the SERVER. No agent ever sees the key.
            "env": {"SERVICE_ACCOUNT_PATH": SERVICE_ACCOUNT_PATH},
            "alwaysLoad": True,
        }
    },
    cwd=HERE,
    setting_sources=["project"],
    # Only the router's own Skill -- each specialist declares its own above.
    skills=["support-router"],
    env={
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(MAX_OUTPUT_TOKENS),
        # Wait for the sheets server, or the specialists start before it is up.
        "MCP_CONNECTION_NONBLOCKING": "0",
    },
    # ONE GLOBAL approval list, not a per-agent tool set: the sheets tools go
    # here even though only the SPECIALISTS call them, and their Skills too.
    allowed_tools=[
        *DELEGATE_TOOLS,
        "mcp__sheets__*",
        "Skill(order-lookup)",
        "Skill(delay-analysis)",
    ],
    permission_mode="dontAsk",
    max_turns=MAX_TURNS,
)


# The whole agent: one `async for` over the SDK's stream. No loop of ours.
async def run_team(question: str) -> None:
    print(f"\n{'=' * 60}\nQUESTION: {question}\n{'=' * 60}")

    delegations = 0
    reply = ""
    verdict = None

    async for msg in query(prompt=question, options=options):
        if isinstance(msg, AssistantMessage):
            # A message with no parent_tool_use_id came from the router itself;
            # anything else is a specialist talking inside its own conversation.
            from_router = getattr(msg, "parent_tool_use_id", None) is None
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name in DELEGATE_TOOLS:
                    delegations += 1
                elif isinstance(block, TextBlock) and block.text.strip() and from_router:
                    reply = block.text
        elif isinstance(msg, ResultMessage):
            # Each nested conversation ends with one too; the LAST is the verdict.
            verdict = msg

    print(f"\n{reply}")
    print(f"\ndelegations: {delegations}   "
          f"terminal_reason: {verdict.terminal_reason if verdict else '?'}")
    if verdict and verdict.terminal_reason == "max_turns":
        print(f"WARNING: hit the {MAX_TURNS}-turn cap. This did NOT succeed.")


# THE LESSON: same code, three questions, three different amounts of work.
async def main() -> None:
    await run_team("What is the status of order 3001, and how many days until it arrives?")
    await run_team("Where is my order 1002, and why is it taking so long?")
    await run_team("I placed an order last week and it still has not arrived.")


# Fail loudly on a missing key, or every sheet read 404s inside a specialist.
if __name__ == "__main__":
    if not SERVICE_ACCOUNT_PATH or not os.path.isfile(SERVICE_ACCOUNT_PATH):
        raise SystemExit("SERVICE_ACCOUNT_PATH is missing or wrong -- see cmd.txt")
    asyncio.run(main())
