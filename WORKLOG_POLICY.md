# Worklog Policy

## Goal

Worklog is for future recall, not for replaying every step of execution.

The default style should be:

- short
- decision-oriented
- result-oriented
- low-token

One sentence rule:

`Do not log what was tried. Log what became true.`

## Default Policy

By default, write the smallest useful log entry.

A normal entry should answer only these questions:

1. what important topic was handled
2. what changed or was decided
3. what result or blocker matters later
4. what next step is now clear

If one of these is not meaningful, omit it.

## What To Record

Record only high-value items.

### 1. Key decisions

Examples:

- chose one architecture over another
- changed a sync strategy
- confirmed a routing rule
- locked a contract or policy

### 2. Real outcomes

Examples:

- sync succeeded with actual count
- tests passed
- root cause was identified
- a worker or scheduler path is now live

### 3. Important blockers

Examples:

- permission denied
- credential invalid
- API capability missing
- document and real behavior disagree

### 4. Meaningful next steps

Examples:

- next job family to implement
- next migration slice
- exact follow-up needed from user or platform

## What Not To Record

Do not log low-value process detail by default.

Skip these unless they are the point:

- ordinary file reading
- normal code search
- repeated test reruns
- intermediate trial and error
- partial thoughts with no decision
- implementation micro-steps
- duplicate summaries of the same outcome
- command-by-command narration

One sentence rule:

`If future-you would not need it to understand the outcome, do not log it.`

## Log Levels

Use only two levels.

### L1: Brief

This is the default.

Use for most work.

Format:

```md
## YYYY-MM-DD HH:mm

- Topic: ...
- Change: ...
- Result: ...
- Next: ...
```

Notes:

- keep to 2 to 4 bullets
- each bullet should be one sentence
- omit empty bullets

### L2: Detailed

Use only when the event has long-term value.

Examples:

- architecture decisions
- production incidents
- multi-module refactors
- external dependency breakage
- policy changes

Format:

```md
## YYYY-MM-DD HH:mm

- Topic: ...
- Decision: ...
- Why it matters: ...
- Result: ...
- Next: ...
```

Notes:

- still keep it compact
- do not turn it into a transcript

## Escalation Rule

Start with `L1`.

Upgrade to `L2` only if at least one is true:

- the decision changes future implementation direction
- the failure or blocker will likely return
- the change crosses multiple systems
- the result will be referenced in later planning

If not, stay brief.

## Writing Style

Prefer:

- concrete nouns
- actual results
- short sentences
- stable facts

Avoid:

- long background
- repeated context
- emotional filler
- speculative narration
- exhaustive step history

Good:

- `List sync fixed; fetched the full record set after the pagination stop condition was corrected.`

Bad:

- `Spent time checking several files, tried multiple hypotheses, then finally changed a few things and it seems better now.`

## Examples

### Good brief entry

```md
## 2026-04-18 21:10

- Topic: target routing behavior
- Change: writeback no longer replays source prefixes into target sections
- Result: mapped items now write directly under the target tag
- Next: keep this behavior covered by regression tests
```

### Good detailed entry

```md
## 2026-04-18 19:40

- Topic: unified job architecture
- Decision: daily patrol, sync flows, and task execution share one contract/orchestration model with polymorphic execution
- Why it matters: avoids growing separate schedulers while preserving simple workers for simple jobs
- Result: architecture, protocol draft, and scheduler design docs were written and linked
- Next: extend phase one from list sync to generic job definitions and detail backfill
```

### Bad entry

```md
## 2026-04-18 20:00

- Read several files
- Searched for logic
- Thought about architecture
- Tried a few changes
- Ran tests many times
```

This is process noise and should not be recorded.

## Daily Practice Rule

When writing a worklog entry, ask:

1. what will matter tomorrow
2. what would be annoying to rediscover
3. what can be safely omitted

If the answer is "not much", write a very short `L1` entry or skip logging entirely.

## Default Recommendation

Use this default unless explicitly asked otherwise:

- one `L1` entry per meaningful outcome
- no more than one `L2` entry for a topic unless direction truly changed
- prefer fewer, denser entries over frequent process updates

The purpose of the worklog is not completeness.

The purpose is efficient memory.
