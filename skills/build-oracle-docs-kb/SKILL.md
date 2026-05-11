---
name: build-oracle-docs-kb
description: Build a raw Oracle documentation corpus for a future knowledge base. Use when Codex needs to acquire Oracle Database documentation artifacts from the supported Oracle bookshelf URLs for Oracle AI Database 26ai, Oracle Database 21c, or Oracle Database 19c; prefer per-book PDFs, split API references and image-heavy architecture/diagram SVGs into dedicated raw collections, and produce acquisition manifests and summaries.
---

# Build Oracle Docs KB

## Workflow

Use the bundled collector script. Do not hand-roll Oracle documentation crawling unless the script itself needs to be fixed.

1. Confirm the input is one of the supported bookshelf URLs:
   - `https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html`
   - `https://docs.oracle.com/en/database/oracle/oracle-database/21/books.html`
   - `https://docs.oracle.com/en/database/oracle/oracle-database/19/books.html`
2. Run a dry run when planning, validating, or estimating scope.
3. Run the collector from the caller's target workspace so outputs land under `./raw/{version}`, `./raw/api/{version}`, and `./raw/images/{version}` as appropriate.
4. Inspect `./raw/{version}/summary.txt` first, then `./raw/{version}/manifest.json` for detailed status.
5. For incomplete HTML/API captures, use targeted repair mode before rerunning a whole bookshelf.
6. Report totals, failures, skipped robots entries, repair results, and the raw output path to the user.

## Commands

Use these examples from the caller's workspace, replacing `<skill-dir>` with this skill directory when needed:

```powershell
python <skill-dir>\scripts\collect_oracle_docs.py https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html --dry-run
python <skill-dir>\scripts\collect_oracle_docs.py https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html
python <skill-dir>\scripts\collect_oracle_docs.py https://docs.oracle.com/en/database/oracle/oracle-database/21/books.html
python <skill-dir>\scripts\collect_oracle_docs.py https://docs.oracle.com/en/database/oracle/oracle-database/19/books.html
```

For targeted profile-aware HTML repair:

```powershell
python <skill-dir>\scripts\collect_oracle_docs.py https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html --repair-slug database-sample-schemas,trusted-answer-search-user-s-guide
python <skill-dir>\scripts\collect_oracle_docs.py https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html --repair-suspicious-html
```

For offline parser validation, use the bundled fixture:

```powershell
python <skill-dir>\scripts\collect_oracle_docs.py https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html --dry-run --fixture <skill-dir>\scripts\fixtures\bookshelf_fixture.html
```

## Scope

Phase 1 is raw acquisition only. Preserve PDFs, HTML pages, and downloaded same-origin assets in their original formats. Do not extract text, convert to Markdown, chunk content, create embeddings, build vector indexes, or implement search in this phase.

Prefer the per-book PDF when a book has a PDF link. Crawl the HTML book only when no PDF exists. Record the all-books zip URL in `manifest.json`, but do not download or extract it in this phase.

Write ordinary version artifacts under `./raw/{version}`. Write HTML API/reference corpora whose titles or slugs contain `API`, `APIs`, or `Javadoc` under `./raw/api/{version}`. For image-heavy technical architecture, diagram, and reference architecture entries, collect only discovered SVG files under `./raw/images/{version}`. Record each item's collection in `manifest.json`.

HTML-only books are profile-aware. Oracle Help Center books should capture TOC-driven content pages, Javadoc/API references should capture frame/index/package/class pages under `./raw/api/{version}`, and multi-release landing pages should select only the latest release. When a latest-release PDF is available, ingest it like any other PDF at `./raw/{version}/{slug}.pdf` while recording `versioned_latest_book` provenance in the manifest. Each HTML item should include an `html_profile` validation block in the manifest. Repair mode stages replacements first and only replaces the active item folder after profile validation succeeds.

## Design Record

Read `references/design.md` before modifying behavior, changing scope, adding command options, or implementing later phases. That document records the approved decisions, deferred work, manifest schema, and validation criteria.
