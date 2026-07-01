# Oracle Error Help Plugin

Oracle Error Help recognizes Oracle error codes during normal LLM sessions, retrieves official Oracle Error Help guidance, and falls back to a user-maintained list of trusted sources when needed.

## Install

```text
codex plugin marketplace add BillSawyer-Oracle/oracle-docs --ref main
codex plugin add oracle-error-help@oracle-docs
```

Start a new Codex thread after installation. The skill invokes implicitly for recognized errors such as `ORA-01403`, `PLS-00201`, `TNS-12541`, and `RMAN-00569`.

## Reference editor

Ask Codex to open the Oracle Error Help reference editor, or run it directly from the plugin directory:

```text
node app/server.mjs
```

Open the printed localhost URL. The dependency-free editor can refresh and edit recognized error prefixes and add, remove, or reorder trusted fallback sources. The web app does not run during ordinary error lookups.
