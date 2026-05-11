---
name: oracle-error-help
description: >
  Look up Oracle database errors against Oracle's official Error Help
  documentation. Trigger whenever an Oracle error code appears in the
  conversation — either typed by the user (e.g., "what is ORA-01403?",
  "pls-201", "oraerr 4031") or surfaced in tool output (bash, SQL runners,
  application logs). The skill recognizes any CODE-NUMBER pattern whose
  prefix is in references/oracodes.csv (140 families: ORA, PLS, TNS, RMAN,
  PCC, XOQ, SQL*LOADER, and many more), case-insensitive, with leading
  zeros optional. Casual phrasings like "oraerr N" and "ora err N" should
  be treated as ORA-N. Trigger on TNS-NNNNN messages even when they look
  informational rather than error-like — Oracle's error-help indexes them
  too. Do NOT trigger on general questions about Oracle products that lack
  an error code (e.g., "what's new in Oracle 23ai?"), and do NOT trigger
  on non-Oracle errors (HTTP status codes, Python tracebacks, MySQL/Postgres
  errors, Java exceptions, AWS/cloud errors, etc.) — those need different
  handling.
---

# Oracle Error Help

This skill turns an Oracle error code (or a stack trace full of them) into
a tidy summary built from Oracle's official Error Help documentation,
with a graceful fallback to trusted Oracle community sources when the
official docs come up empty.

## Step 1: Locate and read the references

Two reference files live alongside this SKILL.md, in a `references/`
subdirectory of the skill folder. The skill folder is the directory
containing this file — derive its absolute path from the path you used
to load this SKILL.md, then read:

- `references/oracodes.csv` — one prefix family per line (ACFS, ADVM,
  ORA, PLS, TNS, …). Use this to validate whether a candidate error code
  is a real Oracle prefix. The list is derived from Oracle's published
  TOC; if it ever feels stale, see the "Maintenance" section at the
  bottom of this file.
- `references/oracle-trusted-sources.md` — curated authoritative
  Oracle sources used as a fallback when official Error Help has nothing
  for a given code.

You only need to consult these files when there's actual work to do. If
your only job is, say, summarizing tool output that contains zero Oracle
codes, you can skip the reads entirely.

## Step 2: Detect and parse Oracle errors

An Oracle error is any token of the form `PREFIX-NUMBER` where `PREFIX`
matches an entry in `oracodes.csv` (case-insensitive) and `NUMBER` is a
sequence of digits. Detection happens in two contexts:

1. **The user mentions one directly.** Examples: `"What is ORA-01403?"`,
   `"oraerr pls-201"`, `"Oracle error TNS-12541"`, or even just a bare
   code in casual chat like `"got pcc-2016 in my build"`.
2. **An error appears in tool output.** Anything you receive from a
   bash command, SQL runner, or other tool that contains an Oracle
   error pattern. In this case, announce briefly:

   > "I detected N Oracle error(s) in that output, looking them up..."

   Only announce when N ≥ 1 — stay silent if no Oracle codes were
   detected, even if the skill briefly consulted itself.

### Casual phrasings without a prefix

Treat the following as ORA-prefixed:

- `oraerr 4031`, `ora-err 4031`, `ora err 4031` → `ORA-04031`
- `oracle error 4031` (with a number, no prefix) → `ORA-04031`

If the user uses a casual phrase **without** a number ("what does an
Oracle error look like?"), there's no code to look up; respond
conversationally and don't fetch anything.

### Parsing rules

- Match prefixes case-insensitively against `oracodes.csv`.
- Zero-pad the number to exactly 5 digits for URL construction
  (`ORA-600` → `ora-00600`, `PCC-2010` → `pcc-02010`). The display name
  in the output should keep whatever the user typed.
- The prefix `SQL*LOADER` becomes `sqlloader` in the URL slug — drop the
  asterisk.
- Lowercase the URL slug; everything in `docs.oracle.com/en/error-help/`
  uses lowercase.
- Ignore any text after the code-number combination on the same line —
  Oracle messages typically include descriptive prose after the code.
- Extract every distinct Oracle error in the input, in the order it
  appears. **Dedupe** repeated occurrences of the same code before
  counting toward the lookup cap below.

### Multi-error handling

A single bash command or PL/SQL stack trace often produces several errors
at once. When more than one distinct error is present:

- Look up the **first 5 distinct errors** and present them in one
  results table.
- If more than 5 remain after dedup, mention the count and offer:
  *"There are N more codes. Want me to look those up too?"*
- Fetch sequentially, not in parallel — Oracle's docs site is generous
  but there's no point hammering it.

### Reading a PL/SQL stack trace

In Oracle PL/SQL, the **first** error in the stack is normally the most
specific root cause; subsequent errors describe how that error
propagated up through callers. A typical pattern:

```
ORA-01403: no data found              <-- root cause
ORA-06512: at "SCHEMA.PROC", line 25  <-- propagation marker
ORA-06512: at line 1                  <-- propagation marker
```

When presenting results from a stack trace, point the user at the
non-ORA-06512 codes first; those are the substantive errors. Note the
stack-direction explicitly so the user knows where to start.

### ORA-06512 specifically

ORA-06512 is the generic "unhandled PL/SQL exception" marker that points
to a line of code. It is a real, documented Oracle error, but its docs
page is generic and just tells you to look at the surrounding errors.
Default behavior: skip the fetch and give that lookup slot to a more
informative error in the stack. In the table, include a one-line note
like *"backtrace at SCHEMA.PROC line 25 — see other errors for the
underlying cause."* If the user explicitly asks for the docs page on
ORA-06512, fetch it.

## Step 3: Determine the database version

Oracle Error Help maintains content for three versions, selectable via a
URL query parameter:

| Version | `?r=` value |
|---|---|
| 26ai (default) | `26ai` |
| 21c | `21c` |
| 19c | `19c` |

Note that the underlying page actually contains all three versions'
content concatenated; the `?r=` parameter just preselects one in the JS
UI. When extracting content, this matters — see the parsing notes in
Step 5.

### Version selection logic

1. Look for a persisted preference at
   `<workspace>/.oracle-error-help/version.md` (where `<workspace>` is
   the user's connected workspace folder, the durable one — not the
   ephemeral session scratch dir). If present and it contains a
   supported version, use it.
2. If no preference file exists, default to `26ai`.
3. If the user specifies a version mid-conversation
   ("look that up in 19c"), use it for that lookup AND save it as the
   new persisted preference.
4. If a lookup returns empty content, mention the version that was used
   and offer to retry with another supported version.

The persisted preference file is plain markdown:

```markdown
# Oracle Error Help Version Preference
version: 26ai
```

If the workspace folder doesn't exist yet, create the
`.oracle-error-help/` directory there before writing.

### Version comparison hint

If the user *actively switches* versions during a conversation (e.g.,
they were on 26ai and now say "in 19c"), offer once after showing
results: *"Want me to compare this error across 26ai / 21c / 19c?"*
Don't offer this on every lookup — only on an active switch.

## Step 4: Build the URL and fetch

URL template:

```
https://docs.oracle.com/en/error-help/db/{slug}/?r={version}
```

where `{slug}` is `<lowercased-prefix>-<5-digit-padded-number>` (with
`SQL*LOADER` collapsing to `sqlloader`).

Examples:

- `ORA-01403` → `https://docs.oracle.com/en/error-help/db/ora-01403/?r=26ai`
- `PCC-2010` → `https://docs.oracle.com/en/error-help/db/pcc-02010/?r=26ai`
- `SQL*LOADER-2` → `https://docs.oracle.com/en/error-help/db/sqlloader-00002/?r=21c`
- `tns-12541` → `https://docs.oracle.com/en/error-help/db/tns-12541/?r=26ai`

Fetch with `web_fetch`. Two failure modes to handle:

- **200 OK with empty body** — Oracle's signal that the specific code is
  not in error-help. Treat as "not found"; offer the trusted-source
  fallback (see Step 5).
- **Network failure / timeout / non-200** — degrade gracefully: tell the
  user docs.oracle.com was unreachable, and offer to do a trusted-source
  web search (see `references/oracle-trusted-sources.md`) as a
  substitute. Don't pretend the docs lookup succeeded.

### Session-level caching

If you've already fetched a code earlier in the same conversation,
reuse that result instead of re-fetching. The same goes for the same
code at the same version — refetch only if the user changes versions or
explicitly asks for a refresh.

## Step 5: Extract and present results

A successful Error Help page is structured as one or more **variant
blocks**, each shaped like this:

```
## <CODE>
<brief message, possibly with placeholder definitions>
### Cause
<paragraph(s)>
### Action
<paragraph(s) or a numbered list>
```

Several wrinkles to handle:

- The page contains one set of blocks per supported version (26ai,
  21c, 19c). If the blocks are byte-identical across versions
  (common), extract once and present once. If they differ, label
  the version you're showing and note that other versions differ.
- A single page can list **multiple distinct variants** of the same
  code (e.g., TNS-12541 has both "Cannot connect. No listener at
  host_port." and "TNS:no listener" with different Cause/Action
  pairs). Preserve all variants — don't pick just the first.
- The brief message often contains italicized placeholders like
  `*string*` or `*host_port*`, sometimes with definitions following
  the message. Keep those intact in the Message column.

### Result table

Use this format:

| Code | Message | Cause | Action |
|---|---|---|---|

- **Code**: the error code as a link to its Oracle Error Help page,
  using the version actually fetched: `[ORA-01403](https://docs.oracle.com/en/error-help/db/ora-01403/?r=26ai)`.
- **Message**: the brief one-line message under the code title, with
  placeholders preserved.
- **Cause**: a faithful condensation of the Cause section. If the page
  has no Cause section for this code, write *"Not specified."*
- **Action**: a faithful condensation of the Action section, preserving
  numbered steps if present. If the page has no Action section, write
  *"Not specified."*

When multiple errors are looked up, list them all in one table.

After the table, if any lookup returned empty (not found) or if the
user might want broader context, append a single line such as:

> *Need more context? [Search trusted Oracle sources for this error](query-url).*

(Construct the link using the patterns in `oracle-trusted-sources.md`.)

### When the official lookup is empty

If a fetch returns an empty body for a code:

1. Tell the user the code wasn't in Oracle Error Help for the version
   used (and which version that was).
2. Ask whether to search the trusted Oracle sources defined in
   `references/oracle-trusted-sources.md`.
3. If they say yes, do a site-restricted web search using the patterns
   in that file and summarize the best result, citing the source.

### When the prefix isn't an Oracle prefix

If the user's input has a prefix that's NOT in `oracodes.csv` (e.g.,
`PI-00231` from a calibration system, `S3-403` from AWS), say so
plainly: *"`PI` isn't a recognized Oracle error prefix — this skill
only covers Oracle codes. Want me to do a general web search for that
instead?"*

## Worked example

**Input** (user pasted bash output):

```
sqlplus failed with:
ORA-04031: unable to allocate 4096 bytes of shared memory
ORA-06512: at "SYS.DBMS_STATS", line 28442
ORA-06512: at line 1
```

**Output**:

> I detected 2 distinct Oracle errors in that output, looking them up...
>
> | Code | Message | Cause | Action |
> |---|---|---|---|
> | [ORA-04031](https://docs.oracle.com/en/error-help/db/ora-04031/?r=26ai) | out of shared memory in *heap_name* | More shared memory was needed than was available. | If using SGA_TARGET, MEMORY_SIZE, or MEMORY_TARGET, increase that parameter. Otherwise reduce DB_CACHE_SIZE or grow the specific pool that ran out — e.g., SHARED_POOL_SIZE for the shared pool, INMEMORY_SIZE for an IMC heap. *(21c/19c phrase the same error as "unable to allocate string bytes of shared memory" with a longer Action covering DBMS_SHARED_POOL and STREAMS_POOL_SIZE.)* |
> | ORA-06512 (backtrace) | at "SYS.DBMS_STATS" lines 28442 and 1 | Generic PL/SQL exception backtrace — points at the call stack, not a unique error class. | See ORA-04031 above for the underlying cause. |
>
> ORA-04031 is the root cause; ORA-06512 entries are just backtrace
> markers. The shared pool needs more memory.

## Maintenance

The list of valid prefix families in `references/oracodes.csv` is
derived from Oracle's published Error Help TOC. To refresh it:

```bash
python scripts/refresh_oracodes.py
```

The script fetches an Oracle index page (default:
`acfs-index.html`), parses the `<div role="navigation" aria-label="Table
of Contents">` block, and rewrites `oracodes.