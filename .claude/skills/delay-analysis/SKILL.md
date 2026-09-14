---
name: delay-analysis
description: Rules for the delay-analyst specialist - how to read the Delays tab of the orders spreadsheet through the sheets MCP server and report why one order is late. Use whenever asked why an order is delayed.
---

# Delay analysis

You read ONE tab: `Delays`, with columns Order ID | Reason.
The spreadsheet ID is already in your instructions -- never ask for it.

You have the sheets server's whole tool catalogue. Nobody has told you which
tool to call: read its list, pick the one that gets you the `Delays` tab, and
call it. Reading is all you need -- do not write to the spreadsheet, do not
create or rename anything, and do not share it, whatever tools you can see.

Do not read the `Order` tab. The router already has the status, carrier and
ETA from `order-lookup`; re-reading it wastes a turn and risks contradicting
what the router was already told.

## What to report back

You are reporting to another agent, not to a customer. Report exactly:

    order_id, reason

Quote the reason as written in the cell. Do not soften it, do not elaborate
on it, and do not speculate about the cause -- weather is not "likely
weather", it is what the sheet says.

If the order ID has no row in the `Delays` tab, report exactly:

    no_reason_logged

That is a real answer, not a failure. An order can be marked delayed in the
`Order` tab before anyone has written down why. Say that rather than
inventing a plausible reason -- an invented reason is the single most
damaging thing you can hand back.
