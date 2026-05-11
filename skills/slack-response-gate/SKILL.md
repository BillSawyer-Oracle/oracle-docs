---
name: slack-response-gate
description: Send a Slack message to one or more people, collect a response through typed replies or clickable Slack buttons when the runtime supports Block Kit interactivity, classify the response against an expected contract, and take different follow-up actions based on that response. Use when Codex needs Slack-mediated acknowledgement, approval, human input, routing decisions, yes/no gates, selectable button labels or colors, multi-recipient response collection, or any workflow where later work depends on how someone responds in Slack.
---

# Slack Response Gate

## Overview

Use this skill for Slack-mediated response gates. Coordinate Slack messaging, response collection, and conditional follow-up without pretending Codex is a long-running listener.

The wait step should be implemented with a Codex heartbeat or cron automation when available. Do not use shell sleeps, busy loops, or unverifiable promises that Codex will keep watching after the turn ends.

Clickable Slack buttons require Block Kit message support plus a Slack interactivity receiver that can receive and acknowledge button-click payloads. If the active Slack runtime cannot send blocks and receive interactive payloads, use the typed-reply fallback.

## Workflow

1. Define the gate contract before sending:
   - recipient or destination, with enough detail to resolve Slack users or conversations
   - exact question or request
   - response mode: typed replies or interactive buttons
   - answer count: one, two, or three
   - answer labels supplied by the user, or sensible defaults
   - button styles supplied by the user, or sensible defaults
   - branch map from response to action
   - multi-recipient aggregation rule when more than one person receives the message
   - deadline, timeout behavior, and timezone
   - whether the user requested immediate send, draft-first, or scheduled delivery

2. Resolve the Slack destination:
   - Use the Slack skill for reading, searching, and destination resolution.
   - Use the Slack outgoing-message skill before composing or sending final Slack text.
   - Prefer a DM for a single recipient unless the user specifies a channel, thread, or group DM.
   - For multiple recipients, prefer an existing group DM when a shared visible response is desired and the Slack runtime can find one. Do not silently fan out separate DMs.

3. Choose the response shape:
   - Use a one-answer message for acknowledgement. The single button is usually `Acknowledge`, but use the label the user provides.
   - Use a two-answer message for a decision, such as yes/no, true/false, approve/reject, accept/decline, or any two labels the user provides.
   - Use a three-answer message when the first two answers are decision buttons and the third button is acknowledgement without decision.
   - Treat a three-button acknowledgement as a distinct non-decision response. Do not execute either decision branch from an acknowledgement click.
   - Ask how to handle acknowledgement on a three-button message when it matters: stop with "acknowledged", keep waiting for a later decision, or create a follow-up reminder.

4. Choose button styles:
   - Use only Slack-supported button styles: `default` (omit the `style` field, visually gray), `primary` (green), and `danger` (red).
   - Default one-answer messages to `default`.
   - Default two-answer decision messages to `primary` for the affirmative/positive button and `danger` for the negative/reject button.
   - Default three-answer messages to `primary`, `danger`, and `default` for the acknowledgement button.
   - Let the user override the style for any button.
   - Use `primary` for at most one button in a set unless the user explicitly asks otherwise.
   - Use `danger` only for destructive, rejecting, negative, or stop/hold actions unless the user explicitly asks otherwise.

5. Check button capability:
   - If the runtime exposes a Slack send API that accepts Block Kit `blocks` plus an action receiver for `block_actions` payloads, use interactive buttons.
   - If the runtime can send blocks but cannot receive button-click payloads, do not present the workflow as automated. Either use typed replies or generate the Block Kit payload for another system to send.
   - If the runtime only supports plain Slack messages, send a typed-reply fallback that lists the allowed responses exactly.

6. Send the gate message:
   - State the question, accepted responses, and deadline plainly.
   - Ask the recipient to click a button when interactive buttons are available, or reply in the same thread or DM when using typed replies.
   - Include a short correlation marker when useful, such as `[gate:project-approval-2026-05-09]`, especially if later search may be needed.
   - Store the returned Slack message identifiers, such as channel/conversation id, timestamp, permalink, thread timestamp, recipient, and sent time.
   - For button messages, also store the gate id, block id, action ids, button labels, normalized answers, button styles, and expected responder ids.

7. Set up the wait:
   - For this thread continuing later, create a heartbeat automation attached to the current thread.
   - For detached monitoring across a workspace, create a cron automation only when the user asked for a standalone recurring job.
   - Make the automation prompt self-contained: include the Slack message identifiers, recipient, response mode, accepted responses, branch map, deadline, timeout behavior, aggregation rule, and what to report.
   - Use a reasonable polling interval based on urgency, usually 5-30 minutes for same-day gates and daily for low-urgency gates.

8. Check for the response when resumed:
   - For typed replies, read replies in the target Slack conversation or thread after the sent timestamp.
   - For button workflows, read the stored interaction payloads or durable gate state created by the action receiver.
   - Verify the response came from the expected person or an explicitly allowed delegate.
   - Ignore unrelated messages and messages sent before the gate prompt.
   - Classify only clear replies. If the reply is ambiguous, ask the user or send one clarifying Slack follow-up when that matches the requested workflow.
   - If no response has arrived and the deadline has not passed, re-arm the heartbeat.
   - If the deadline has passed, follow the timeout branch or report that no response arrived.

9. Take the branch action:
   - Execute the mapped action only when the user already authorized that class of action.
   - Preserve normal safety checks for destructive, external, financial, customer-facing, or permission-sensitive actions.
   - Report what response was received, which branch was taken, and what action was completed.
   - Stop re-arming the automation once the gate is resolved.

## Multi-Recipient Rules

When sending to more than one person, define the aggregation rule before sending:

- `per-recipient`: track and report each person's answer independently.
- `any-first`: the first clear eligible response resolves the gate.
- `all-required`: wait until every recipient responds or the deadline passes.
- `threshold`: resolve when a specified count or percentage is reached.
- `owner-decides`: collect responses, then ask the original user to decide.

Prefer separate DMs when responses should be private or independently attributable. Use a shared channel or group DM only when visible responses are acceptable.

For button messages sent to multiple people, the action receiver must record which Slack user clicked which button and prevent duplicate or conflicting clicks according to the aggregation rule.

## Button Payload Guidance

For interactive Slack buttons, create one actions block with one to three button elements. Each button needs a stable `action_id` and a `value` that carries the gate id plus the normalized answer.

Use this shape conceptually when the runtime supports blocks:

```json
{
  "type": "actions",
  "block_id": "gate_<gate_id>",
  "elements": [
    {
      "type": "button",
      "text": { "type": "plain_text", "text": "<label>" },
      "style": "<primary_or_danger_only_when_selected>",
      "action_id": "gate_<gate_id>_<answer>",
      "value": "{\"gate_id\":\"<gate_id>\",\"answer\":\"<answer>\"}"
    }
  ]
}
```

Omit the `style` field for default gray buttons. Keep button labels short; Slack button text may truncate visually at around 30 characters.

## Branch Contract

Use a compact contract like this in your own working notes or automation prompt:

```text
Slack response gate
Recipients: <Slack users or conversation>
Prompt message: <permalink or channel + ts>
Response mode: typed replies | buttons
Answer count: <1 | 2 | 3>
Expected responders: <people>
Aggregation rule: <per-recipient | any-first | all-required | threshold | owner-decides>
Accepted responses:
- approve: label="Approve", style=primary, typed synonyms=approve, yes, ship it
- reject: label="Reject", style=danger, typed synonyms=reject, no, hold
- acknowledge: label="Acknowledge", style=default, typed synonyms=ack, received
Branch actions:
- approve -> <action>
- reject -> <action>
- acknowledge -> <action or no-decision behavior>
Deadline: <absolute date/time and timezone>
Timeout action: <action or report only>
```

## Automation Prompt Pattern

When creating a heartbeat, write a direct, self-contained prompt:

```text
Check Slack for responses to <message permalink or channel/thread ts> from <expected responders> after <sent timestamp>. Response mode is <typed replies | buttons>. Classify the latest clear response into <answers> using these button values, labels, or typed phrases: <phrases>. Apply this aggregation rule: <rule>. If the gate is resolved, take this branch map: <branch map>. If no clear response exists and the deadline <deadline> has not passed, re-arm this heartbeat for <interval>. If the deadline has passed, take the timeout action: <timeout action>. Report the Slack response and completed action in this thread.
```

## Guardrails

- Do not claim continuous monitoring unless an automation was actually created.
- Do not promise clickable buttons unless the active Slack runtime can send Block Kit blocks and receive interactive action payloads.
- Do not invent Slack user ids, channel ids, response contents, approvals, or decisions.
- Treat broad mentions, customer-facing channels, and sensitive data as high-impact.
- Keep the Slack message concise and action-oriented.
- Do not proceed with a branch if the response does not clearly match the contract.
- Make branch actions idempotent where possible. If an action may have already run, verify before repeating it.

## Workflow Responsibility

Let this skill handle response-gate orchestration: contract creation, Slack delivery, response capture, validation, classification, idempotency checks, timeout handling, and reporting.

Let this skill execute downstream business actions only when the action is low-risk, clearly authorized, and idempotent. For destructive, external, financial, customer-facing, or ambiguous actions, use the Slack response to prepare the next step or ask for explicit confirmation before execution.

## Slack References

- Button element style options: https://docs.slack.dev/reference/block-kit/block-elements/button-element/
- Actions block structure: https://docs.slack.dev/reference/block-kit/blocks/actions-block/
- Interactive component handling: https://docs.slack.dev/tools/java-slack-sdk/guides/interactive-components/
