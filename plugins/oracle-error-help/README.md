# Oracle Error Help Plugin

Oracle Error Help recognizes Oracle error codes during normal LLM sessions, retrieves official Oracle Error Help guidance, and falls back to a user-maintained list of trusted sources when needed.

## Install

```text
codex plugin marketplace add BillSawyer-Oracle/oracle-docs --ref main
codex plugin add oracle-error-help@oracle-docs
```

Start a new Codex thread after installation. The skill invokes implicitly for recognized errors such as `ORA-01403`, `PLS-00201`, `TNS-12541`, and `RMAN-00569`.

## Standalone app

The app runs independently of an LLM and does not require the NoDoc Tools server. From this plugin directory, start it with:

```text
node app/server.mjs
```

Open the printed localhost URL. The dependency-free app provides two tabs:

- **Error Lookup** accepts pasted diagnostics or a UTF-8 `.trc`, `.log`, or `.txt` file, supports 26ai, 21c, and 19c, and displays official Message, Cause, and Action content.
- **Reference Editor** refreshes or edits recognized error prefixes and adds, removes, or reorders trusted fallback sources.

## Helpful Checks

When a detected error has curated supplemental diagnostics, **Helpful Checks** appears below the official results with runnable SQL and a Copy button for each block. The section appears only when it adds practical information not already supplied by Oracle.

Checks are stored in `skills/oracle-error-help/references/oracle-trusted-sources.md`. Add another `### CODE-NNNNN` section there to extend the app and background skill. Saving trusted-source rows in the Reference Editor preserves these Markdown sections.

When outbound HTTPS requires `HTTP_PROXY`, `HTTPS_PROXY`, or `NO_PROXY`, start it with:

```text
node --use-env-proxy app/server.mjs
```

The app starts only when invoked; it does not run during ordinary conversational lookups.
