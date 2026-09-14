"""
D19 - one router, two specialists. The delegation, running live.

    uv run streamlit run agent_sdk_subagent_streamlit.py

This is an instrument panel, not a slideshow. Everything on screen is taken
from the real run: the init message listing the subagents, which specialist
the router handed the job to, which tool that specialist picked off the
server's 20-tool menu, what came back -- and, at the end, terminal_reason.

Nothing here is new agent logic. It all comes from one file already in the
project:

    agent_sdk_subagent_teaching.SUBAGENTS / options / MODEL / MAX_TURNS

and from two things this file does not own at all:

    mcp-google-sheets   an external process that owns the spreadsheet
    claude_agent_sdk    the loop, the delegation and the verdict

THE LESSON, IN ONE LINE
    D18 asked "how many ROUNDS?" and let the model decide. D19 asks the same
    question about WORKERS. Same code, three questions, three different
    numbers in the DELEGATIONS tile -- and nothing in the file chose them.

WHAT IS NOT IN THIS FILE
    No `for specialist in ...`. No "if delayed: call delay_analyst". No tool
    dispatch. Every tool name on screen was declared by the server, and every
    [specialist] label was read off parent_tool_use_id, not guessed.
"""

import asyncio
import dataclasses
import fnmatch
import json
import os
import subprocess
import sys
import time

import streamlit as st

from claude_agent_sdk import query
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(HERE)

import agent_sdk_subagent_teaching as team  # noqa: E402  reused as-is

st.set_page_config(page_title="D19 - the router owns the decision", page_icon="🔀",
                   layout="wide", initial_sidebar_state="expanded")

AGENT_FILE = os.path.join(HERE, "agent_sdk_subagent_teaching.py")
SKILLS_DIR = os.path.join(HERE, ".claude", "skills")

# The five moments this page is built around. These are the points where the
# SDK hands us something we did not have to ask for.
C1, C2, C3, C4, C5 = "#c0562a", "#b07d20", "#8a7a1c", "#3f7d42", "#2f6f7d"
STEP = {
    1: (C1, "THE SDK", "the session starts - and registers the subagents"),
    2: (C2, "THE ROUTER", "reads its Skill and decides WHO works"),
    3: (C3, "A SPECIALIST", "picks a tool off the server's menu - in its own conversation"),
    4: (C4, "THE ROUTER", "reads the answer and decides whether a SECOND one is needed"),
    5: (C5, "THE VERDICT", "terminal_reason and permission_denials - not the prose"),
}
GREEN, RED, GREY, AMBER, BLUE = "#3f7d42", "#b3261e", "#7a756c", "#b07d20", "#2f6f7d"

# The catalogue mcp-google-sheets declares. We wrote none of these names. The
# read/write/share split is ours, and it is the whole argument for narrowing
# allowed_tools once you have watched the specialists choose.
CATALOGUE = {
    "get_sheet_data": "read", "get_sheet_formulas": "read",
    "list_sheets": "read", "list_spreadsheets": "read",
    "get_multiple_sheet_data": "read",
    "get_multiple_spreadsheet_summary": "read",
    "list_folders": "read", "search_spreadsheets": "read",
    "find_in_spreadsheet": "read",
    "update_cells": "write", "batch_update_cells": "write",
    "batch_update": "write", "add_rows": "write", "add_columns": "write",
    "create_sheet": "write", "create_spreadsheet": "write",
    "copy_sheet": "write", "rename_sheet": "write", "add_chart": "write",
    "share_spreadsheet": "share",
}
KIND_COLOUR = {"read": GREEN, "write": AMBER, "share": RED, "?": GREY}

# Who is who. The colour follows a specialist everywhere it appears, so a
# transcript can be read at a glance from the back of a room.
AGENT_COLOUR = {
    "router": C1,
    "order-lookup": BLUE,
    "delay-analyst": "#7a3b8f",
}

# The one control on this page that is a real security boundary. Everything
# else changes what an agent is TOLD; this changes what it can DO.
#
# Plain names in the picker, and the "so what" spelled out underneath it, so
# the four options can be read without knowing the codebase first.
PRESETS = {
    "Allow everything (the default)": (
        [*team.DELEGATE_TOOLS, "mcp__sheets__*",
         "Skill(order-lookup)", "Skill(delay-analysis)"],
        "What agent_sdk_subagent_teaching.py actually ships. All 20 sheets "
        "tools are "
        "approved -- including the ones that write, add rows and re-share the "
        "spreadsheet. Only the Skills say not to, and a Skill is guidance.",
    ),
    "Let it read, but not write": (
        [*team.DELEGATE_TOOLS,
         "mcp__sheets__get_sheet_data", "mcp__sheets__list_sheets",
         "Skill(order-lookup)", "Skill(delay-analysis)"],
        "Only the two reading tools are approved. Now ask it to mark 1004 as "
        "shipped: the write is refused no matter what the prompt says. This is "
        "a real boundary, not advice.",
    ),
    "Take away the specialists' rulebooks": (
        [*team.DELEGATE_TOOLS, "mcp__sheets__*"],
        "Same tools, but the two specialists lose their Skills. They can still "
        "read the sheet -- watch their reports lose their agreed shape: no "
        "not_found, no blank, and a tone aimed at the customer instead of at "
        "the router.",
    ),
    "Block the spreadsheet completely": (
        [*team.DELEGATE_TOOLS,
         "Skill(order-lookup)", "Skill(delay-analysis)"],
        "No sheets tools approved at all, so every read a specialist attempts "
        "is denied. The important part: the run still looks like it worked. "
        "Check permission_denials in the verdict to see what really happened.",
    ),
}

# The three questions from the bottom of agent_sdk_subagent_teaching.py, plus
# the four from the "Try this" list in cmd.txt.
CASES = [
    ("1 delegation", "What is the status of order 3001, and how many days until it arrives?",
     "3001 is shipped. There is nothing to explain, so the router stops after one."),
    ("2 delegations", "Where is my order 1002, and why is it taking so long?",
     "1002 is delayed. The reason lives in the other tab, so a second specialist "
     "is needed."),
    ("0 delegations", "I placed an order last week and it still has not arrived.",
     "No order ID. A workflow would call the lookup with an empty ID and fail. "
     "The router notices and spends nothing."),
    ("blank cells", "What is happening with order 1004?",
     "Blank carrier and ETA. Does order-lookup report them as blank, or does the "
     "router quietly invent an ETA?"),
    ("not_found", "Can you check order 9999 for me?",
     "Not in the sheet. Expect not_found and ONE delegation."),
    ("two at once", "What is the status of orders 3001 and 1002?",
     "Does it delegate twice, or give up?"),
    ("the write test", "Please mark order 1004 as shipped.",
     "Only the Skills say not to write. Switch allowed_tools to 'read tools only' "
     "and run it again to see a real boundary."),
]

st.markdown("""
<style>
  [data-testid="stMetricValue"] { font-size:1.25rem !important; }
  .stButton button { text-align:left; font-weight:600; }
  section[data-testid="stSidebar"] .stButton button { text-align:center; }
  [data-testid="stCaptionContainer"] p { font-size:.88rem !important; }

  .hookcard  { background:#faf8f5; border:1px solid #e6e0d6;
               border-left:4px solid #b07d20; border-radius:6px;
               padding:.55rem .8rem; margin:.2rem 0 .6rem; }
  .hookcard b { font-family:ui-monospace,monospace; font-size:.86rem;
               color:#7a3b18 !important; }
  .hookcard span { display:block; font-size:.85rem; color:#3d3d3d !important;
               margin-top:.25rem; line-height:1.45; }
  .kv        { font-family:ui-monospace,monospace; font-size:.76rem;
               color:#6d6862 !important; margin-top:.3rem; }
  .qcard     { background:#faf8f5; border:1px solid #e6e0d6;
               border-left:4px solid #c0562a; border-radius:6px;
               padding:.6rem .8rem; margin:.2rem 0 .7rem;
               font-size:.92rem; color:#25211c !important; line-height:1.45; }

  .stepcard  { border-left:5px solid var(--c); background:#faf8f5;
               border-radius:6px; padding:.5rem .9rem; margin:.5rem 0 .4rem; }
  .stepnum   { display:inline-block; width:1.4rem; height:1.4rem; line-height:1.4rem;
               text-align:center; border-radius:50%; background:var(--c);
               color:#fff !important; font-weight:700; font-size:.78rem;
               margin-right:.5rem; }
  .steptitle { font-weight:800; font-size:1.02rem; color:#141414; }
  .who       { float:right; font-size:.68rem; letter-spacing:.09em;
               color:var(--c) !important; font-weight:800; padding-top:.3rem; }
  .field     { font-family:ui-monospace,monospace; font-size:.82rem;
               font-weight:600; color:#7a3b18 !important;
               background:#efeae2; border-radius:4px; padding:.1rem .4rem; }
  .note      { color:#3d3d3d; font-size:.88rem; font-weight:500;
               line-height:1.5; margin-top:.35rem; }

  /* one line of transcript. INDENTED means it happened inside a delegation. */
  .turn      { border-left:4px solid var(--c); background:#faf8f5;
               border-radius:0 6px 6px 0; padding:.45rem .8rem;
               margin:.25rem 0 .25rem var(--ind); }
  .turn .tag { font-size:.66rem; letter-spacing:.09em; font-weight:800;
               text-transform:uppercase; color:var(--c) !important; }
  .turn .bod { font-size:.9rem; color:#1f1f1f !important; margin-top:.2rem;
               line-height:1.5; }
  .turn .mono{ font-family:ui-monospace,monospace; font-size:.78rem;
               color:#6d6862 !important; margin-top:.25rem;
               word-break:break-all; }

  .agentcard { border:1px solid #e6e0d6; border-top:5px solid var(--c);
               border-radius:7px; padding:.7rem .9rem; background:#fff;
               height:100%; }
  .agentcard h4 { margin:0 0 .3rem; font-size:1rem; color:#141414 !important; }
  .agentcard .role { font-size:.67rem; letter-spacing:.09em; font-weight:800;
               text-transform:uppercase; color:var(--c) !important; }
  .agentcard p { font-size:.84rem; color:#3d3d3d !important; line-height:1.45;
               margin:.35rem 0 0; }

  .idchip    { font-family:ui-monospace,monospace; font-size:.74rem;
               background:#f6e4d9; border:1px solid #d9a184; color:#7a3b18 !important;
               border-radius:4px; padding:.1rem .35rem; margin-right:.25rem; }

  /* one row per tool the SERVER declared */
  .rule      { display:flex; align-items:center; gap:.55rem; padding:.3rem .7rem;
               margin:.16rem 0; border-left:4px solid var(--c); background:#faf8f5;
               border-radius:0 5px 5px 0; font-family:ui-monospace,monospace;
               font-size:.79rem; }
  .rule .ev  { width:66px; flex:none; color:#6d6862 !important; font-size:.69rem; }
  .rule .fn  { flex:1; font-weight:700; color:#25211c !important; }
  .tag       { font-size:.67rem; border-radius:10px; padding:.06rem .45rem;
               white-space:nowrap; border:1px solid #ddd6ca; color:#6d6862 !important; }
  .code2     { font-size:.69rem; border-radius:10px; padding:.06rem .5rem;
               font-weight:800; white-space:nowrap; color:#fff !important; }
  .off       { opacity:.42; }

  section.main p, section.main li,
  [data-testid="stMarkdownContainer"] p { color:#1f1f1f; font-weight:450; }
  [data-testid="stCaptionContainer"], .stCaption,
  [data-testid="stCaptionContainer"] p { color:#4a4a4a !important;
                                         font-weight:500 !important; }
  [data-testid="stMetricValue"] { font-weight:800 !important; }
  [data-testid="stMetricLabel"] { font-weight:600 !important; color:#4a4a4a; }
  h1,h2,h3,h4,h5 { font-weight:800 !important; color:#141414 !important; }
  .stTabs [data-baseweb="tab"] { font-weight:700; font-size:.95rem; }
  [data-testid="stSidebarCollapseButton"] button { opacity:1 !important; }

  /* ---- the sidebar. Streamlit's default caption grey is too pale to read
         off a projector, so every line in here gets real contrast. ---- */
  section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
  section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li {
               color:#25211c !important; font-weight:500; }
  section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
  section[data-testid="stSidebar"] .stCaption p {
               color:#4a463f !important; font-weight:500 !important;
               font-size:.8rem !important; line-height:1.45; }
  section[data-testid="stSidebar"] code {
               color:#7a3b18 !important; background:#f0ebe3 !important;
               font-size:.78rem !important; }

  /* ---- the roster. One row per agent, subagents indented under the main
         one, each in the colour that follows it through the transcript. ---- */
  .team      { margin:.15rem 0 .3rem; }
  .trow      { border-left:4px solid var(--c); background:#faf8f5;
               border-radius:0 6px 6px 0; padding:.42rem .6rem; margin:.3rem 0; }
  .trow.sub  { margin-left:.9rem; }
  .trole     { font-size:.59rem; letter-spacing:.09em; text-transform:uppercase;
               font-weight:800; color:var(--c) !important; }
  .tname     { font-family:ui-monospace,monospace; font-size:.83rem;
               font-weight:800; color:#25211c !important; margin-top:.08rem; }
  .tjob      { font-size:.75rem; font-weight:500; color:#4a463f !important;
               line-height:1.4; margin-top:.18rem; }

  /* ---- the preset questions. Unselected must still read as a QUESTION you
         can ask; selected must be unmistakable. ---- */
  .qopt      { border:1px solid #ded7cb; border-left:4px solid #ccc5b8;
               background:#fbfaf8; border-radius:8px; padding:.7rem .85rem;
               margin:0 0 .45rem; }
  .qopt.sel  { border-color:#c0562a; border-left-color:#c0562a;
               background:#fdf3ec; box-shadow:0 0 0 2px rgba(192,86,42,.16); }
  .qtop      { display:flex; justify-content:space-between; align-items:center;
               gap:.4rem; margin-bottom:.42rem; }
  .qlabel    { font-size:.65rem; letter-spacing:.09em; text-transform:uppercase;
               font-weight:800; color:#7a756c !important; white-space:nowrap; }
  .qopt.sel .qlabel { color:#c0562a !important; }
  .qbadge    { font-size:.6rem; font-weight:800; letter-spacing:.06em;
               text-transform:uppercase; border-radius:10px; padding:.12rem .5rem;
               background:#efece6; color:#6d6862 !important;
               border:1px solid #ded7cb; white-space:nowrap; }
  .qbadge.on { background:#c0562a; color:#fff !important; border-color:#c0562a; }
  .qtext     { font-size:.94rem; font-weight:700; line-height:1.42;
               color:#25211c !important; }
  .qopt.sel .qtext { color:#7a3b18 !important; }
  .qwhy      { font-size:.79rem; font-weight:500; line-height:1.45;
               color:#55514b !important; margin-top:.45rem; }

  /* ---- the day band: what this demo is, before anything else ---- */
  .dayband   { display:flex; align-items:center; gap:.65rem; flex-wrap:wrap;
               margin:.1rem 0 .35rem; }
  .daypill   { background:#c0562a; color:#fff !important; font-size:.74rem;
               font-weight:800; letter-spacing:.14em; text-transform:uppercase;
               border-radius:4px; padding:.24rem .65rem; white-space:nowrap; }
  .daywhat   { font-size:.88rem; font-weight:800; color:#7a756c !important;
               letter-spacing:.01em; }
  .topicrow  { display:flex; gap:.4rem; flex-wrap:wrap; margin:.2rem 0 .5rem; }
  .topic     { font-size:.77rem; font-weight:800; border-radius:14px;
               padding:.22rem .75rem; border:1px solid var(--c);
               color:var(--c) !important; background:#fff; white-space:nowrap; }
  .topic i   { font-style:normal; font-weight:600; opacity:.72; }
  .topic.new { background:var(--c); color:#fff !important; }
  .topic.new i { opacity:.85; }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ helpers
def html(markup):
    st.markdown(markup, unsafe_allow_html=True)


def step_header(n):
    colour, who, title = STEP[n]
    html(f'<div class="stepcard" style="--c:{colour}">'
         f'<span class="who">{who}</span>'
         f'<span class="stepnum">{n}</span>'
         f'<span class="steptitle">{title}</span></div>')


def note(markup):
    html(f'<div class="note">{markup}</div>')


def pick_question(q):
    """Set the question in a CALLBACK, not in the `if st.button(...)` body.

    Callbacks run before the script re-executes, so the card below is already
    drawn in its selected state on the very next frame. Assigning inside the
    button body instead leaves the highlight one rerun behind the click.
    """
    st.session_state["question"] = q


def question_card(label, q, why, selected):
    """A preset question. Unselected it still has to READ as a question you
    can ask -- quoted, with a visible invitation -- or it looks like a panel
    of prose nobody is meant to click."""
    badge = ('<span class="qbadge on">selected - will run</span>' if selected
             else '<span class="qbadge"></span>')
    return (f'<div class="qopt{" sel" if selected else ""}">'
            f'<div class="qtop"><span class="qlabel">{label}</span>{badge}</div>'
            f'<div class="qtext">&ldquo;{q}&rdquo;</div>'
            f'<div class="qwhy">{why}</div></div>')


def show_json(obj):
    """st.json renders dark on a dark theme, so print it as code instead."""
    st.code(json.dumps(obj, indent=2, default=str), language="json")


def short(name):
    return name.replace("mcp__sheets__", "")


def kind_of(name):
    return CATALOGUE.get(short(name), "?")


def permitted(name, allowed):
    """Would allowed_tools let this call through? fnmatch, same idea as the CLI."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in allowed)


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().rstrip()
    except OSError as exc:
        return f"(not readable: {exc})"


@st.cache_data
def agent_source():
    return read_text(AGENT_FILE).splitlines()


def find_line(needle):
    for i, line in enumerate(agent_source(), start=1):
        if needle in line:
            return i
    return 0


def show_lines(first, last):
    src = agent_source()
    first = max(1, first)
    last = min(len(src), last)
    body = "\n".join(f"{first + i:>4} | {line}"
                     for i, line in enumerate(src[first - 1:last]))
    st.code(body, language="python")


def skill_text(name):
    return read_text(os.path.join(SKILLS_DIR, name, "SKILL.md"))


L_SUBAGENTS = find_line("SUBAGENTS = {")
L_LOOKUP = find_line('"order-lookup": AgentDefinition(')
L_NOTOOLS = find_line("# No tools=[...] on either specialist")
L_DISALLOW = find_line("disallowedTools=list(DELEGATE_TOOLS)")
L_ALLOWED = find_line("allowed_tools=[")
L_MCP = find_line('"sheets": {')
L_SKILLS = find_line('skills=["support-router"]')
L_RUN = find_line("async def run_team")
L_PARENT = find_line("from_router = getattr")
L_DELEGTOOLS = find_line("DELEGATE_TOOLS = (")
L_MAXTURNS = find_line("MAX_TURNS = 30")
L_KEY = find_line("SERVICE_ACCOUNT_PATH = os.getenv")
L_PERM = find_line("permission_mode=")


# =============================================================================
# The live stdio probe. D18's server was reached over HTTPS and this one is a
# local subprocess -- same protocol, different pipe. This is that difference,
# demonstrated rather than asserted.
# =============================================================================
SHEETS_CMD = ["uvx", "--with", "mcp<2", "mcp-google-sheets@latest"]


@st.cache_data(show_spinner=False)
def probe_sheets_server():
    """One stdio MCP handshake, by hand. Exactly what the SDK does on connect.

    MCP over stdio is newline-delimited JSON-RPC on the process's own stdin
    and stdout -- no URL, no bearer header, no TLS. Everything else about the
    protocol is identical to the HTTPS server in D18.
    """
    out = {"ok": False, "tools": [], "resources": [], "prompts": None,
           "capabilities": {}, "info": {}, "protocol": "", "error": "",
           "wire": []}

    # All five messages up front, then close stdin. The server handles them in
    # order and exits on EOF, so communicate() gives us everything back with a
    # timeout we control -- no blocking readline on a Windows pipe.
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "d19-demo", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
        {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}},
    ]
    out["wire"] = msgs

    env = dict(os.environ)
    env["SERVICE_ACCOUNT_PATH"] = team.SERVICE_ACCOUNT_PATH or ""

    try:
        proc = subprocess.Popen(
            SHEETS_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", env=env, cwd=HERE,
        )
        payload = "".join(json.dumps(m) + "\n" for m in msgs)
        stdout, stderr = proc.communicate(input=payload, timeout=180)
    except FileNotFoundError:
        out["error"] = ("uvx is not on PATH -- and the SDK launches this same "
                        "server the same way, so the run in tab 1 will fail too")
        return out
    except subprocess.TimeoutExpired:
        proc.kill()
        out["error"] = "the server did not answer in 180s (the first run downloads it)"
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    replies = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" in msg:
            replies[msg["id"]] = msg

    if 1 not in replies:
        out["error"] = (stderr or stdout or "no reply to initialize")[-1500:]
        return out

    init = replies[1].get("result", {})
    out["capabilities"] = init.get("capabilities", {})
    out["info"] = init.get("serverInfo", {})
    out["protocol"] = init.get("protocolVersion", "")
    out["tools"] = replies.get(2, {}).get("result", {}).get("tools", [])
    out["resources"] = replies.get(3, {}).get("result", {}).get("resources", [])
    pr = replies.get(4, {})
    # A server that does not implement prompts answers "Method not found".
    out["prompts"] = None if "error" in pr else pr.get("result", {}).get("prompts", [])
    out["ok"] = True
    return out


def catalogue_names():
    """The server's own tool list once probed, else the names we already know."""
    data = st.session_state.get("probe")
    if data and data.get("ok") and data["tools"]:
        return [t["name"] for t in data["tools"]]
    return list(CATALOGUE)


# =============================================================================
# The sidebar. Only allowed_tools is a boundary; the rest is instrumentation.
# =============================================================================
with st.sidebar:
    # st.markdown("### The one idea")
    # st.markdown(
    #     "**D18** you asked *how many ROUNDS?* and the model decided.\n\n"
    #     "**D19** you ask the same question about **WORKERS**. A workflow runs "
    #     "both specialists every time. The router runs the second one only when "
    #     "the first one's answer says it is needed."
    # )

    # The roster, built from team.SUBAGENTS so it cannot drift from the code:
    # add a third specialist to agent_sdk_subagent_teaching.py and it shows up here.
    st.markdown("### The team")
    SUB_JOBS = {
        "order-lookup": "Reads the <b>Order</b> tab and reports status, "
                        "carrier and ETA. Always runs first.",
        "delay-analyst": "Reads the <b>Delays</b> tab and reports WHY an order "
                         "is late. Runs only if the lookup says delayed.",
    }
    roster = [(AGENT_COLOUR["router"], "main agent - the orchestrator",
               "support-router",
               "Decides WHO does the work, then writes the customer's reply. "
               "Delegates every read and never touches the spreadsheet itself.",
               False)]
    for i, name in enumerate(team.SUBAGENTS, 1):
        roster.append((AGENT_COLOUR.get(name, GREY),
                       f"subagent {i} of {len(team.SUBAGENTS)}", name,
                       SUB_JOBS.get(name, "a specialist"), True))
    html('<div class="team">' + "".join(
        f'<div class="trow{" sub" if sub else ""}" style="--c:{colour}">'
        f'<div class="trole">{role}</div>'
        f'<div class="tname">{name}</div>'
        f'<div class="tjob">{job}</div></div>'
        for colour, role, name, job, sub in roster) + "</div>")
    st.caption(
        f"One main agent, {len(team.SUBAGENTS)} subagents, "
        f"{len(team.SUBAGENTS) + 1} separate Skills -- no agent carries "
        "another's rules. Each subagent gets its own conversation, which the "
        "router cannot see into."
    )

    st.divider()

    # Still needed for the warning at the bottom of the sidebar, even though
    # the session read-out itself is gone.
    key_ok = bool(team.SERVICE_ACCOUNT_PATH) and \
        os.path.isfile(team.SERVICE_ACCOUNT_PATH or "")

    st.markdown("### What the team is allowed to do")
    st.caption("`allowed_tools` -- the only control on this page that changes "
               "what an agent CAN DO, rather than what it is told.")
    preset_name = st.radio("preset", list(PRESETS), index=0,
                           label_visibility="collapsed", key="preset")
    allowed, preset_note = PRESETS[preset_name]
    html(f'<div class="hookcard"><b>{preset_name}</b>'
         f"<span>{preset_note}</span></div>")
    with st.expander("the list this actually sends"):
        st.code("\n".join(allowed) or "(empty)", language=None)
        st.caption(
            "ONE GLOBAL list, not a per-agent tool set. The sheets tools sit "
            "here even though only the SPECIALISTS ever call one."
        )

    st.divider()
    show_raw = st.checkbox("keep the raw message stream", value=True)

    if not key_ok:
        st.error("SERVICE_ACCOUNT_PATH is missing or wrong. Every sheet read "
                 "will 404 inside a specialist. See cmd.txt.")


# =============================================================================
# Header
# =============================================================================
# The four pillars this demo stands on. Three carried over from earlier days;
# SUBAGENTS is the one D19 adds, so it is the filled chip.
TOPICS = [
    (C1, "Agent SDK", "the loop", False),
    (BLUE, "MCP", "the tools", False),
    ("#7a3b8f", "Subagents", "the team", True),
    (C4, "Skills", "the rules", False),
]

html('<div class="dayband"><span class="daypill">Day 19</span>'
     '<span class="daywhat">Agent SDK &nbsp;+&nbsp; MCP &nbsp;+&nbsp; '
     "Subagents &nbsp;+&nbsp; Skills</span></div>")
st.title("One router, two specialists")
html('<div class="topicrow">' + "".join(
    f'<span class="topic{" new" if new else ""}" style="--c:{colour}">'
    f"{name} <i>{gloss}</i></span>"
    for colour, name, gloss, new in TOPICS) +
    '<span class="topic" style="--c:#7a756c;border-style:dashed">'
   )
st.caption(
    f"D19 live demo . the team is defined in "
    f"**agent_sdk_subagent_teaching.py**, lines **{L_SUBAGENTS}-{L_NOTOOLS}** . "
    f"this file only runs it and shows it"
)

run_tab, agents_tab, menu_tab, lists_tab, raw_tab, exam_tab = st.tabs([
    "Run the team",
    "Agents & routing",
    "The MCP menu, live",
    "The two lists",
    "Raw messages",
    "Exam concepts",
])


# =============================================================================
# TAB 1 : RUN IT
# =============================================================================
with run_tab:
    st.subheader("Ask it something. Watch how many workers it spends.")

    if "question" not in st.session_state:
        st.session_state["question"] = CASES[0][1]

    st.caption("Pick one of the three below -- the selected one is highlighted "
               "and drops into the box at the bottom -- or type your own.")

    current = st.session_state["question"]
    cols = st.columns(3)
    for col, (label, q, why) in zip(cols, CASES[:3]):
        with col:
            selected = q == current
            html(question_card(label, q, why, selected))
            st.button("This one is selected" if selected else "Ask this question",
                      key=f"case_{label}", width="stretch",
                      type="primary" if selected else "secondary",
                      disabled=selected, on_click=pick_question, args=(q,))

    with st.expander("Four more Questions"):
        for label, q, why in CASES[3:]:
            c1, c2 = st.columns([3, 2])
            with c1:
                html(question_card(label, q, why, q == current))
            with c2:
                st.button("This one is selected" if q == current
                          else "Ask this question",
                          key=f"case_{label}", width="stretch",
                          type="primary" if q == current else "secondary",
                          disabled=q == current,
                          on_click=pick_question, args=(q,))

    st.divider()
    if current in [c[1] for c in CASES]:
        st.caption("The box below is filled from the highlighted card above. "
                   "Edit it and the selection clears -- it becomes your own "
                   "question.")
    else:
        st.caption("Your own question -- none of the preset cards above is "
                   "selected.")
    question = st.text_input("Customer question", key="question")
    go = st.button("Run the team", type="primary")

    if go:
        # agent_sdk_subagent_teaching.options is the source of truth. The sidebar
        # overrides exactly one field, and dataclasses.replace leaves the rest alone.
        opts = dataclasses.replace(team.options, allowed_tools=list(allowed))

        # Live tiles, filled in as the run goes.
        m = st.columns(5)
        tile_deleg = m[0].empty()
        tile_specs = m[1].empty()
        tile_calls = m[2].empty()
        tile_turns = m[3].empty()
        tile_cost = m[4].empty()
        tile_deleg.metric("DELEGATIONS", 0)
        tile_specs.metric("Specialists used", 0)
        tile_calls.metric("Sheet reads", 0)
        tile_turns.metric("Turns", 0)
        tile_cost.metric("Cost", "-")

        html(f'<div class="qcard"><b>{question}</b></div>')
        transcript = st.container()

        names = catalogue_names()
        state = {
            "delegations": 0,
            "by_id": {},        # tool_use_id -> which specialist it started
            "used": [],         # specialists that actually ran
            "tool_calls": 0,
            "picks": [],        # (specialist, tool, index)
            "reply": "",
            "raw": [],
            "verdict": None,
            "seen_init": False,
        }

        def who_of(msg):
            """NOT a guess. Every message from inside a delegation carries the
            id of the Agent call that started it, so the specialist is looked
            up, not inferred from the tab it happens to read."""
            pid = getattr(msg, "parent_tool_use_id", None)
            if pid is None:
                return "router", 0
            return state["by_id"].get(pid, "specialist"), 1

        def turn(who, indent, tag, body, mono=""):
            colour = AGENT_COLOUR.get(who, GREY)
            extra = f'<div class="mono">{mono}</div>' if mono else ""
            html(f'<div class="turn" style="--c:{colour};--ind:{indent * 2.2}rem">'
                 f'<span class="tag">{tag}</span>'
                 f'<div class="bod">{body}</div>{extra}</div>')

        def tool_index(name):
            if name in names:
                return names.index(name) + 1
            if short(name) in names:
                return names.index(short(name)) + 1
            return 0

        def render(msg):
            state["raw"].append(f"----- {type(msg).__name__} -----\n{msg!r}")

            # --- 1. the session starts -------------------------------------
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                if state["seen_init"]:
                    return
                state["seen_init"] = True
                data = msg.data or {}
                with transcript:
                    step_header(1)
                    servers = data.get("mcp_servers", [])
                    # The harness ships its own agents (Explore, Plan, ...) and
                    # they arrive in the same list. Only OURS are the lesson.
                    declared = data.get("agents", []) or []
                    agents = [a for a in declared if a in team.SUBAGENTS] or \
                        list(team.SUBAGENTS)
                    theirs = [a for a in declared if a not in team.SUBAGENTS]
                    tools = data.get("tools", []) or []
                    sheets_tools = [t for t in tools
                                    if str(t).startswith("mcp__sheets__")]
                    note(
                        f"The SDK registered our <span class='field'>{len(agents)}</span> "
                        "subagents and connected the sheets server. "
                        f"<span class='field'>{len(sheets_tools)}</span> sheets tools "
                        "are in this session -- and they are in the ROUTER's session "
                        "too, because <code>allowed_tools</code> is global. Nothing "
                        "but its Skill keeps the router out of the spreadsheet."
                    )
                    html('<div class="kv">agents: ' +
                         " ".join(f'<span class="idchip">{a}</span>' for a in agents) +
                         "</div>")
                    if theirs:
                        st.caption(
                            f"The harness also registered {len(theirs)} of its own "
                            f"({', '.join(theirs)}). They arrive in the same list, "
                            "so count by name -- not by length -- if you ever assert "
                            "on this."
                        )
                    st.caption(f"mcp_servers: {servers}   -   'pending' is NOT an "
                               "error; only 'failed' and 'needs-auth' are")

            # --- 2/3/4. the conversation ------------------------------------
            elif isinstance(msg, AssistantMessage):
                who, indent = who_of(msg)
                for block in msg.content or []:

                    if isinstance(block, ToolUseBlock) and \
                            block.name in team.DELEGATE_TOOLS:
                        sub = (block.input or {}).get("subagent_type") or "?"
                        state["delegations"] += 1
                        state["by_id"][block.id] = sub
                        if sub not in state["used"]:
                            state["used"].append(sub)
                        tile_deleg.metric("DELEGATIONS", state["delegations"])
                        tile_specs.metric("Specialists used", len(state["used"]))
                        with transcript:
                            step_header(2 if state["delegations"] == 1 else 4)
                            turn("router", 0,
                                 f"router delegates #{state['delegations']}",
                                 f"Hands the job to <b>{sub}</b> via the "
                                 f"<code>{block.name}</code> tool. Everything "
                                 "indented below happened inside a SEPARATE "
                                 "conversation the router cannot see into.",
                                 mono=json.dumps(block.input)[:300])

                    elif isinstance(block, ToolUseBlock) and \
                            block.name.startswith("mcp__sheets__"):
                        state["tool_calls"] += 1
                        tile_calls.metric("Sheet reads", state["tool_calls"])
                        idx = tool_index(block.name)
                        state["picks"].append((who, block.name, idx))
                        kind = kind_of(block.name)
                        ok = permitted(block.name, allowed)
                        with transcript:
                            if state["tool_calls"] == 1:
                                step_header(3)
                            flag = "" if ok else (
                                f' <span class="code2" style="background:{RED}">'
                                "NOT IN allowed_tools</span>")
                            turn(who, indent, f"[{who}] TOOL CHOSEN",
                                 f"<code>{block.name}</code> "
                                 f'<span class="tag">{kind}</span> '
                                 f'<span class="tag">#{idx or "?"} of '
                                 f'{len(names)}</span>{flag}',
                                 mono=json.dumps(block.input)[:300])
                            if state["tool_calls"] == 1:
                                note(
                                    "Nobody handed this specialist a tool name. It "
                                    "has no <code>tools=[...]</code> list, so it read "
                                    f"the server's {len(names)} declarations and "
                                    "picked. Hand it one tool and the model is only "
                                    "filling in arguments -- and then nothing here "
                                    "is a choice."
                                )

                    elif isinstance(block, ToolUseBlock) and block.name == "Skill":
                        with transcript:
                            turn(who, indent, f"[{who}] loads its Skill",
                                 f"<code>{json.dumps(block.input)[:160]}</code> -- "
                                 "three agents, three separate skills. No agent "
                                 "carries another's rules.")

                    elif isinstance(block, ToolUseBlock):
                        with transcript:
                            turn(who, indent, f"[{who}] harness tool",
                                 f"<code>{block.name}</code>")

                    elif isinstance(block, TextBlock) and block.text.strip():
                        if who == "router":
                            state["reply"] = block.text
                        with transcript:
                            turn(who, indent,
                                 "router speaks - this is the customer's reply"
                                 if who == "router" else
                                 f"[{who}] reports back to the router",
                                 block.text.replace("\n", "<br>"))

            elif isinstance(msg, UserMessage):
                who, _ = who_of(msg)
                if isinstance(msg.content, str):
                    return
                for block in msg.content or []:
                    if isinstance(block, ToolResultBlock):
                        with transcript:
                            with st.expander("raw rows the MCP server sent back "
                                             f"to [{who}]"):
                                st.code(str(block.content)[:2000], language="json")

            # --- 5. the verdict ---------------------------------------------
            elif isinstance(msg, ResultMessage):
                # Each nested conversation ends with one of these too; the LAST
                # is the verdict for the whole run.
                state["verdict"] = msg
                tile_turns.metric("Turns", msg.num_turns)
                if msg.total_cost_usd:
                    tile_cost.metric("Cost", f"${msg.total_cost_usd:.4f}")

        async def main():
            async for msg in query(prompt=question, options=opts):
                render(msg)

        started = time.time()
        try:
            with st.spinner("Starting the sheets server and running the loop..."):
                asyncio.run(main())
        except Exception as exc:
            low = str(exc).lower()
            if "credit balance" in low:
                st.error("**The Anthropic API key has no credit.** Top it up "
                         "and re-run.")
            elif "enoent" in low or "no such file" in low:
                st.error("**The Claude Code CLI or uvx is missing.** "
                         "`npm install -g @anthropic-ai/claude-code`, and check "
                         "`uv` is on PATH -- the SDK shells out to both.")
            else:
                st.error(f"{type(exc).__name__}: {exc}")

        # ---------------------------------------------------------- the bill
        verdict = state["verdict"]
        with transcript:
            step_header(5)
            v1, v2, v3 = st.columns(3)
            v1.metric("DELEGATIONS", state["delegations"])
            v2.metric("terminal_reason",
                      verdict.terminal_reason if verdict else "?")
            denials = list(getattr(verdict, "permission_denials", None) or []) \
                if verdict else []
            v3.metric("permission_denials", len(denials))

            if verdict and verdict.terminal_reason == "max_turns":
                st.error(
                    f"**Hit the {team.MAX_TURNS}-turn cap. This did NOT succeed.** "
                    "Every delegation is a whole nested conversation -- the "
                    "specialist loads its Skill and reads the sheet, and each of "
                    "those costs a turn. However finished the prose above sounds, "
                    "the verdict is the field, not the paragraph."
                )
            elif verdict and verdict.terminal_reason:
                st.success(f"terminal_reason: `{verdict.terminal_reason}` . "
                           f"{time.time() - started:.1f}s")

            if denials:
                st.warning(
                    "**Calls that were blocked.** A denied call does not raise. It "
                    "comes back as an ordinary tool result and the model carries "
                    "on, usually by guessing, so the run still looks like it "
                    "worked. This field is where the truth is."
                )
                show_json(denials)
            else:
                st.caption("permission_denials: 0 -- printed every run, even when "
                           "it is empty, because a blocked call is otherwise "
                           "invisible.")

            st.divider()
            st.markdown("#### What the customer would see")
            html(f'<div class="qcard">{state["reply"] or "(nothing)"}</div>')
            st.caption(
                "2-3 sentences, no mention of subagents, tools, tabs or the "
                "spreadsheet. That shape is the router's Skill, not this file."
            )

            if state["picks"]:
                st.markdown("#### Which specialist picked which tool")
                for who, name, idx in state["picks"]:
                    colour = AGENT_COLOUR.get(who, GREY)
                    html(f'<div class="rule" style="--c:{colour}">'
                         f'<span class="ev">{who}</span>'
                         f'<span class="fn">{name}</span>'
                         f'<span class="tag">{kind_of(name)}</span>'
                         f'<span class="tag">#{idx or "?"} of {len(names)}</span>'
                         "</div>")
                st.caption(
                    "The [name] prefix is not a guess from the tab name. It was "
                    "looked up from parent_tool_use_id, so it stays correct even "
                    "when both specialists read the same tab."
                )

        st.session_state["raw_log"] = state["raw"]


# =============================================================================
# TAB 2 : THE TEAM
# =============================================================================
with agents_tab:
    st.subheader("Three agents, three separate skills")
    st.markdown(
        "No agent carries another's rules. The router declares its skill in "
        "`ClaudeAgentOptions(skills=[...])`; each specialist declares its own "
        "in `AgentDefinition(skills=[...])`."
    )

    st.code(
        "MAIN      support-router     decides WHO works, writes the customer's reply\n"
        " |-  sub  order-lookup       reads the Order tab\n"
        " `-  sub  delay-analyst      reads the Delays tab",
        language=None,
    )

    c = st.columns(3)
    with c[0]:
        html(f'<div class="agentcard" style="--c:{AGENT_COLOUR["router"]}">'
             '<span class="role">MAIN</span><h4>support-router</h4>'
             "<p>Decides who works, then writes the reply. Never reads the "
             "spreadsheet -- but only because its Skill says not to.</p></div>")
    for col, name in zip(c[1:], ("order-lookup", "delay-analyst")):
        with col:
            d = team.SUBAGENTS[name]
            html(f'<div class="agentcard" style="--c:{AGENT_COLOUR[name]}">'
                 f'<span class="role">SUBAGENT</span><h4>{name}</h4>'
                 f"<p>{d.description}</p></div>")
    st.caption(
        "Those two paragraphs are the `description=` field. That is what the "
        "ROUTER reads when it picks who to hand the job to -- so it is prompt "
        "text, not documentation."
    )

    st.divider()
    st.markdown("#### The routing decision, which is the whole point")
    st.markdown(
        "| the customer asks | order-lookup says | delegations | what the router decides next |\n"
        "|---|---|---|---|\n"
        "| about 3001 | `shipped` | **1** | **Stop.** A shipped order has no "
        "delay reason to fetch, so a second call would cost a turn and return "
        "nothing. |\n"
        "| about 1002 | `delayed` | **2** | **Call `delay-analyst`.** Delayed is "
        "the one status the lookup cannot finish answering -- without a reason "
        "this is half an answer. |\n"
        "| about no ID at all | never called | **0** | **Ask the customer for "
        "the ID.** There is nothing to look up yet, and inventing an ID is "
        "worse than asking. |\n"
        "| about 9999 | `not_found` | **1** | **Stop and say so.** No reason "
        "exists for an order that is not in the sheet; ask them to re-check the "
        "number. |"
    )
    st.info(
        "**A workflow would run both specialists every time**, because it was "
        "written to. The third row is the one worth sitting with: a workflow "
        "written as *look up, then explain* would have called the lookup with an "
        "empty ID and failed. The router spends nothing, because it noticed the "
        "ID was missing."
    )

    st.divider()
    st.markdown("#### The one hard boundary")
    show_lines(L_DISALLOW - 1, L_DISALLOW)
    st.markdown(
        "`disallowedTools=[\"Agent\", \"Task\"]` on each specialist. Neither can "
        "delegate onward, **whatever its prompt says**. Compare that with the "
        "router staying out of the spreadsheet, which is guidance in a Skill and "
        "nothing more."
    )
    st.warning(
        "The CLI renamed the delegation tool from `Task` to `Agent` and answers "
        f"to both (line {L_DELEGTOOLS}). Get the name wrong and the delegation "
        "silently does not count as one -- the run works and the tile reads 0."
    )

    st.divider()
    st.markdown("#### The three Skills, as written")
    which = st.radio("skill", ["support-router", "order-lookup", "delay-analysis"],
                     horizontal=True, label_visibility="collapsed")
    st.code(skill_text(which), language="markdown")


# =============================================================================
# TAB 3 : THE MCP MENU, LIVE
# =============================================================================
with menu_tab:
    st.subheader("The menu the specialists choose from")
    st.markdown(
        "Neither specialist has a `tools=[...]` list. Each sees everything the "
        "server declares and picks for itself. The cost of that openness is real "
        "-- the catalogue includes `update_cells`, `add_rows` and "
        "`share_spreadsheet`, so a support agent could rewrite or re-share the "
        "sheet. Only the Skills say not to, and a Skill is guidance."
    )

    st.info(
        "**Transport: this server is `stdio`, not `https`.** D18 reached "
        "Airtable's server across the network with a URL and a bearer token. This "
        "one is `uvx mcp-google-sheets` -- a local subprocess, spoken to over its "
        "own stdin and stdout. **Same protocol, same handshake, same "
        "`tools/call`. Only the pipe differs.** The button below does that "
        "handshake by hand so you can watch it happen."
    )

    if st.button("Run the MCP handshake against the live server", type="primary"):
        with st.spinner("uvx is launching mcp-google-sheets "
                        "(the first run downloads it)..."):
            st.session_state["probe"] = probe_sheets_server()

    data = st.session_state.get("probe")
    if not data:
        st.caption("Not probed yet -- the list below is the catalogue we know this "
                   "server declares, not a live answer.")
    elif not data["ok"]:
        st.error(f"Could not reach the server: {data['error']}")
    else:
        info = data["info"]
        st.success(f"Handshake OK -- server **{info.get('name', '?')}** "
                   f"v{info.get('version', '?')} . protocol `{data['protocol']}`")

        p1, p2, p3 = st.columns(3)
        p1.metric("Tools", len(data["tools"]))
        p2.metric("Resources", len(data["resources"]))
        p3.metric("Prompts", "not implemented" if data["prompts"] is None
                  else len(data["prompts"]))
        st.caption(
            "MCP defines three primitives -- **tools** (model-controlled, things "
            "to DO), **resources** (application-controlled, things to READ by "
            "URI) and **prompts** (user-controlled templates, surfaced as slash "
            "commands). A server implements only what it needs. Ask it rather "
            "than assuming; that is what discovery is for."
        )
        with st.expander("what the server declared in its capabilities"):
            show_json(data["capabilities"])

    st.divider()
    st.markdown("#### The catalogue, and what your allow-list does to it")
    names = catalogue_names()
    reads = writes = shares = 0
    for name in names:
        full = name if name.startswith("mcp__") else f"mcp__sheets__{name}"
        kind = kind_of(full)
        reads += kind == "read"
        writes += kind == "write"
        shares += kind == "share"

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Tools offered", len(names))
    k2.metric("Read", reads)
    k3.metric("Write", writes)
    k4.metric("Share", shares)
    st.caption(f"Your current preset: **{preset_name}**")

    only_permitted = st.checkbox("show only what the current preset permits",
                                 value=False)
    for i, name in enumerate(names, 1):
        full = name if name.startswith("mcp__") else f"mcp__sheets__{name}"
        kind = kind_of(full)
        ok = permitted(full, allowed)
        if only_permitted and not ok:
            continue
        badge = (f'<span class="code2" style="background:{GREEN}">ALLOWED</span>'
                 if ok else
                 f'<span class="code2" style="background:{GREY}">blocked</span>')
        html(f'<div class="rule{"" if ok else " off"}" '
             f'style="--c:{KIND_COLOUR[kind]}">'
             f'<span class="ev">#{i} of {len(names)}</span>'
             f'<span class="fn">{full}</span>'
             f'<span class="tag">{kind}</span>{badge}</div>')

    st.caption(
        "**Discovery decides what is POSSIBLE. The allow-list decides what is "
        "PERMITTED.** Two different jobs -- you need both. Switch the sidebar to "
        "*read tools only*, ask it to mark 1004 as shipped, and watch DENIED "
        "appear in the verdict."
    )

    if data and data.get("ok") and data["tools"]:
        st.divider()
        st.markdown("#### The raw schema a specialist reads to choose")
        tnames = [t["name"] for t in data["tools"]]
        default = "get_sheet_data" if "get_sheet_data" in tnames else tnames[0]
        pick = st.selectbox("Inspect any tool the server offers", tnames,
                            index=tnames.index(default))
        show_json(next(t for t in data["tools"] if t["name"] == pick))
        st.caption(
            "The **description** is prompt text -- it is how the specialist "
            "decides when to call this. The **inputSchema** is what its arguments "
            "are validated against. We wrote neither."
        )

    with st.expander("the wire format -- what was actually sent down the pipe"):
        st.code("\n".join(json.dumps(m) for m in (data["wire"] if data else []))
                or "(probe not run)", language="json")
        st.caption(
            "Newline-delimited JSON-RPC on stdin. That is the whole stdio "
            "transport. Over HTTPS the same objects travel as a POST body with an "
            "Authorization header instead -- and the SDK does all of this for you "
            "either way."
        )


# =============================================================================
# TAB 4 : THE TWO LISTS
# =============================================================================
with lists_tab:
    st.subheader("Two different lists, and it is easy to confuse them")
    st.markdown(
        "| setting | what it does |\n"
        "|---|---|\n"
        "| `ClaudeAgentOptions.tools` | which tools **exist** -- for everyone |\n"
        "| `ClaudeAgentOptions.allowed_tools` | which of those **run without "
        "asking** -- for everyone |\n"
        "| `AgentDefinition.tools` | which tools **one agent gets** |\n"
        "| `AgentDefinition.disallowedTools` | which tools **one agent loses** |"
    )
    st.error(
        "**`allowed_tools` is GLOBAL.** It is an approval list, not a per-agent "
        "tool set, and that has two consequences you have to design around."
    )

    a, b = st.columns(2)
    with a:
        html('<div class="hookcard"><b>1. the sheets tools must be listed</b>'
             "<span>&quot;mcp__sheets__*&quot; goes in <code>allowed_tools</code> "
             "even though only the SPECIALISTS ever call one. The SDK builds the "
             "approval list from <code>skills</code> and <code>allowed_tools</code> "
             "alone -- it never reads <code>AgentDefinition.mcpServers</code>. Leave "
             "them out and <code>permission_mode=&quot;dontAsk&quot;</code> denies "
             "every read the specialists attempt.</span></div>")
    with b:
        html('<div class="hookcard"><b>2. the specialists\' Skills must be named</b>'
             "<span><code>skills=[&quot;support-router&quot;]</code> auto-adds "
             "<code>Skill(support-router)</code>, but the SPECIALISTS' skills are "
             "NOT added. <code>Skill(order-lookup)</code> and "
             "<code>Skill(delay-analysis)</code> are named by hand for the same "
             "reason.</span></div>")

    show_lines(L_ALLOWED - 2, L_ALLOWED + 6)

    st.warning(
        "**So the router is not actually blind to the spreadsheet.** The tools "
        "are in its session and they are approved. What keeps it out is its "
        "Skill. Do not claim otherwise -- run it and read the init tool count in "
        "tab 1. A hard boundary needs a different shape (a separate session for "
        "the router, or the server declared only on the specialists) and both "
        "cost something."
    )

    st.divider()
    st.markdown("#### Denied calls are invisible unless you look")
    st.markdown(
        "A call blocked by `allowed_tools` **does not raise**. It comes back as "
        "an ordinary tool result and the model carries on, usually by guessing, "
        "so the run still looks like it worked. "
        "`ResultMessage.permission_denials` is where the truth is -- tab 1 prints "
        "it every run, even when it is 0."
    )

    st.divider()
    st.markdown(f"#### Why MAX_TURNS is {team.MAX_TURNS} and not 6")
    show_lines(L_MAXTURNS, L_MAXTURNS)
    st.markdown(
        "Every delegation is a whole nested conversation. Inside each one the "
        "specialist loads its own Skill and reads the sheet, and each of those "
        "costs a turn. Two delegations is comfortably ~11 turns. **A cap hit is "
        "still a WARNING, not a success** -- `terminal_reason == \"max_turns\"`."
    )
    st.caption(
        f"The other cap: `CLAUDE_CODE_MAX_OUTPUT_TOKENS={team.MAX_OUTPUT_TOKENS}` "
        "caps ONE reply. There is no `max_tokens` on ClaudeAgentOptions; the CLI "
        "reads the env var. It applies to the SPECIALISTS' replies too, which is "
        "easy to forget -- three agents, one cap, and a truncated report still "
        "reads like an answer."
    )


# =============================================================================
# TAB 5 : RAW MESSAGES
# =============================================================================
with raw_tab:
    st.subheader("Every message the SDK handed us")
    st.caption(
        "Nothing on this page was inferred. `parent_tool_use_id` is the field "
        "that says which conversation a message belongs to -- `None` means the "
        "router, and anything else is the id of the Agent call that started that "
        "specialist."
    )
    show_lines(L_PARENT - 2, L_PARENT)

    log = st.session_state.get("raw_log")
    if not show_raw:
        st.info("Turn 'keep the raw message stream' back on in the sidebar.")
    elif not log:
        st.info("Run the team in tab 1 first.")
    else:
        st.metric("Messages", len(log))
        st.code("\n\n".join(log), language="text")


# =============================================================================
# TAB 6 : EXAM CONCEPTS
# =============================================================================
with exam_tab:
    st.subheader("Where each concept lives in this code")
    st.caption("Every row points at a real line in agent_sdk_subagent_teaching.py.")

    rows = [
        ("Subagents", f"line {L_SUBAGENTS}",
         "An `AgentDefinition` per specialist, passed as "
         "`ClaudeAgentOptions(agents={...})`. Each gets its own system prompt, "
         "its own Skill, its own model and -- crucially -- its own "
         "**conversation**. The router never sees inside one."),
        ("description= is prompt text", f"line {L_LOOKUP + 1}",
         "The `description` field is what the ROUTER reads when it picks who to "
         "hand the job to. Write it for a reader who has to choose, not for a "
         "developer skimming the file."),
        ("Delegation", f"line {L_DELEGTOOLS}",
         "The router calls the `Agent` tool (the CLI renamed it from `Task` and "
         "answers to both). Every call opens a nested conversation. Count them "
         "and you have measured how much work the question actually cost."),
        ("The hard boundary", f"line {L_DISALLOW}",
         "`disallowedTools=[\"Agent\", \"Task\"]` on each specialist. A specialist "
         "cannot delegate onward, whatever its prompt says. This is enforcement; "
         "a Skill is not."),
        ("parent_tool_use_id", f"line {L_PARENT}",
         "The id of the Agent call that started a nested conversation, carried on "
         "every message from inside it. `None` means the router itself. It is how "
         "this page labels [order-lookup] correctly even when both specialists "
         "read the same tab."),
        ("allowed_tools is global", f"line {L_ALLOWED}",
         "ONE approval list for the whole session, not a per-agent tool set. The "
         "sheets tools go here even though only the specialists call them, and "
         "the specialists' Skills are named by hand because `skills=` only "
         "auto-adds the router's."),
        ("Skills, one per agent", f"line {L_SKILLS}",
         "Three agents, three separate SKILL.md files -- no agent carries "
         "another's rules. The router's routing table, the lookup's report format "
         "and the delay analyst's `no_reason_logged` rule are each invisible to "
         "the other two."),
        ("MCP over stdio", f"line {L_MCP}",
         "`uvx mcp-google-sheets` is launched as a local subprocess and spoken to "
         "over its stdin and stdout. D18's Airtable server was reached over HTTPS "
         "with a URL and a bearer token. Same protocol, same handshake, same "
         "`tools/call` -- only the pipe differs. Tab 3 does the stdio handshake "
         "by hand."),
        ("Credentials go to the SERVER", f"line {L_KEY}",
         "`SERVICE_ACCOUNT_PATH` is passed in the server's `env`. No agent ever "
         "sees the key. MCP adds no permissions of its own -- the agents can do "
         "exactly what that service account can do, so scope it in Google. That "
         "limit survives any mistake in a prompt or an allow-list."),
        ("We do not pick the tool", f"line {L_NOTOOLS}",
         "Handing a specialist `mcp__sheets__get_sheet_data` would mean WE chose "
         "the tool and the model only filled in the arguments -- and then nothing "
         "about this demo is a choice. Each sees all 20 and picks."),
        ("permission_mode", f"line {L_PERM}",
         "`dontAsk`: a script has nobody to answer a prompt, so anything not "
         "pre-approved is denied silently. Read `permission_denials` or you will "
         "never know it happened."),
        ("max_turns and the verdict", f"line {L_MAXTURNS}",
         f"{team.MAX_TURNS}, because each delegation is a whole nested "
         "conversation. `terminal_reason == \"max_turns\"` is a WARNING however "
         "finished the prose sounds."),
        ("The loop is still the SDK's", f"line {L_RUN}",
         "One `async for`. Adding a team changed WHO does the work, not who runs "
         "the loop -- exactly as MCP changed what the agent could reach without "
         "changing how you run one."),
    ]
    for topic, where, detail in rows:
        with st.expander(f"**{topic}**  .  {where}"):
            st.markdown(detail)

    st.divider()
    