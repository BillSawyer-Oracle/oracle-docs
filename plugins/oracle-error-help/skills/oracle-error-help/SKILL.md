---
name: oracle-error-help
description: Look up Oracle database errors in Oracle's official Error Help documentation during normal LLM sessions, and open the bundled reference editor when explicitly requested. Trigger implicitly whenever the user or tool output contains a recognized Oracle error such as ORA-01403, PLS-201, TNS-12541, RMAN-00569, SQL*Loader-2, or casual forms such as "oraerr 4031". Also trigger when the user asks to edit, refresh, add, or remove Oracle Error Help prefixes or trusted sources. Do not trigger for general Oracle questions without an error code or for non-Oracle error families.
---

# Oracle Error Help

Use Oracle's official Error Help pages first and trusted Oracle sources only as a fallback.

## Runtime behavior

Participate through normal implicit skill invocation whenever a recognized Oracle error appears in user input or tool output. This lookup behavior requires no local server or UI.

Do not start the bundled web app during ordinary error lookup. Start it only when the user explicitly asks to open the editor or maintain the prefix or trusted-source references.

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

## Fall back safely

If the official page is empty or unavailable, say so and name the attempted version. Ask before broadening the search. When the user agrees, read `references/oracle-trusted-sources.md`, search those domains in listed order, and cite the result.

If a prefix is not in the prefix reference, say it is not recognized as an Oracle error family and offer a general search.

## Open the reference editor on request

When the user requests the editor, start this command as a long-running background process from the plugin root:

```text
node app/server.mjs
```

Give the user the printed localhost URL. The editor can refresh prefixes from Oracle, add or remove prefixes, and add or remove trusted sources. It writes only the two reference files in this skill. No packages or build step are required. Stop the server when the user asks to close it; otherwise leave it running for the active session.
