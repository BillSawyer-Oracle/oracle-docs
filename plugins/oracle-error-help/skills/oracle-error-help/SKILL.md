---
name: oracle-error-help
description: Look up Oracle database errors in Oracle's official Error Help documentation during normal LLM sessions, and open the bundled lookup UI or reference editor when explicitly requested. Trigger implicitly whenever the user or tool output contains a recognized Oracle error such as ORA-01403, PLS-201, TNS-12541, RMAN-00569, SQL*Loader-2, or casual forms such as "oraerr 4031". Also trigger when the user asks for the Oracle Error Help app, lookup UI, reference editor, or to edit, refresh, add, or remove prefixes or trusted sources. Do not trigger for general Oracle questions without an error code or for non-Oracle error families.
---

# Oracle Error Help

Use Oracle's official Error Help pages first and trusted Oracle sources only as a fallback.

## Runtime behavior

Participate through normal implicit skill invocation whenever a recognized Oracle error appears in user input or tool output. This lookup behavior requires no local server or UI.

Do not start the bundled web app during ordinary conversational error lookup. Start it only when the user explicitly asks for the lookup UI, reference editor, or reference maintenance.

## Detect errors

1. Read `references/oracle-error-prefixes.txt` only when the input may contain an error code.
2. Match `PREFIX-NUMBER` case-insensitively when the prefix is in that file.
3. Treat `oraerr 4031`, `ora err 4031`, and `oracle error 4031` as `ORA-04031`.
4. Extract distinct errors in appearance order. Look up at most five per response and offer to continue when more remain.
5. Treat ORA-06512 as a PL/SQL backtrace marker unless the user explicitly asks for its documentation. Give lookup slots to substantive errors first.

When codes appear in tool output, say briefly that Oracle errors were detected before looking them up.

## Look up errors

Use the persisted version in `<workspace>/.oracle-error-help/version.md`, or `26ai` when absent. Supported values are `26ai`, `21c`, and `19c`. Save an explicitly requested version for later lookups.

Build the official URL as:

```text
https://docs.oracle.com/en/error-help/db/{lowercase-prefix}-{five-digit-number}/?r={version}
```

Remove the asterisk from `SQL*LOADER` in the URL slug. For example, `PCC-2010` becomes `pcc-02010` and `SQL*LOADER-2` becomes `sqlloader-00002`.

Read `references/lookup-guide.md` for page extraction, version variants, stack interpretation, and output rules. Reuse results already fetched for the same code and version in the current conversation.

## Add helpful checks

After the official results, read the `## Helpful Checks` section in `references/oracle-trusted-sources.md`. Include only sections matching detected codes and only when they add runnable diagnostics or practical context that the official content does not already provide. Label them supplemental, preserve SQL as fenced code blocks, and never let them override or contradict the official Message, Cause, or Action.

## Fall back safely

If the official page is empty or unavailable, say so and name the attempted version. Ask before broadening the search. When the user agrees, read `references/oracle-trusted-sources.md`, search those domains in listed order, and cite the result.

If a prefix is not in the prefix reference, say it is not recognized as an Oracle error family and offer a general search.

## Open the standalone app on request

When the user requests the lookup UI or reference editor, start this command as a long-running background process from the plugin root:

```text
node --use-env-proxy app/server.mjs
```

Give the user the printed localhost URL. The Error Lookup tab accepts pasted diagnostics or a UTF-8 `.trc`, `.log`, or `.txt` file and shows official Message, Cause, and Action results. The Reference Editor tab can refresh prefixes from Oracle and edit prefixes or trusted sources. The Node flag uses configured `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` values when present; no packages or build step are required. Stop the server when the user asks to close it; otherwise leave it running for the active session.
