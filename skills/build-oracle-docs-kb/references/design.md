# build-oracle-docs-kb Design

Date: 2026-05-10

## Purpose

Create a Codex skill that acquires raw Oracle Database documentation artifacts for a future knowledge base. The skill is part procedural guidance and part bundled code. This design document is the durable record of the decisions made before implementation.

## Skill Identity

- Skill name: `build-oracle-docs-kb`
- Skill location: `C:\Users\bill\.codex\skills\build-oracle-docs-kb`
- Main script: `scripts/collect_oracle_docs.py`
- Design record: `references/design.md`

## Supported Inputs

Phase 1 accepts only these exact Oracle bookshelf URLs:

- Oracle AI Database 26ai: `https://docs.oracle.com/en/database/oracle/oracle-database/26/books.html`
- Oracle Database 21c: `https://docs.oracle.com/en/database/oracle/oracle-database/21/books.html`
- Oracle Database 19c: `https://docs.oracle.com/en/database/oracle/oracle-database/19/books.html`

The script infers the output version from the accepted URL:

- `/26/books.html` maps to `26ai`
- `/21/books.html` maps to `21c`
- `/19/books.html` maps to `19c`

Any other URL is rejected in phase 1.

## Output Layout

Run the collector from the target workspace. By default, ordinary version artifacts are written under:

```text
./raw/{version}
```

HTML API/reference corpora are written under:

```text
./raw/api/{version}
```

Image-heavy technical architecture, diagram, and reference architecture entries are written under:

```text
./raw/images/{version}
```

The output path is relative to the caller's current workspace, not the skill installation directory. A future or initial `--output-root` option may override `./raw`, but downloaded corpora must not be stored inside the skill folder.

The main run manifest and summary remain under `./raw/{version}`. API items in the manifest include `output.collection: "api"` and `output.base_dir: "api/{version}"`; image items use `output.collection: "images"` and `output.base_dir: "images/{version}"`; ordinary items use `output.collection: "docs"` and `output.base_dir: "{version}"`.

## Phase 1 Scope

Phase 1 is raw acquisition only.

In scope:

- Parse one supported Oracle bookshelf page.
- Discover book titles and available `HTML` and `PDF` links.
- Prefer per-book PDFs when available.
- Crawl HTML-only books when no PDF exists.
- Place HTML API/reference corpora in the separate `api` collection.
- Place image-heavy technical architecture, diagram, and reference architecture entries in the separate `images` collection by collecting their underlying SVG payloads.
- Preserve raw PDFs, HTML pages, and downloaded assets in original formats.
- Create stable filesystem-safe slugs for local paths.
- Write `manifest.json`, `summary.txt`, and a console summary.
- Support dry-run planning.
- Support resumability.
- Respect `robots.txt` for crawled pages and assets.

Out of scope:

- Text extraction
- Markdown conversion
- Content normalization
- Chunking
- Embeddings
- Vector storage
- Search or retrieval
- Downloading or extracting the all-books zip file
- Supporting other Oracle documentation libraries
- Concurrent crawling

## Acquisition Rules

The bookshelf page is the authoritative manifest for phase 1. For every discovered book:

1. If a PDF link exists, download the PDF.
2. If no PDF link exists, crawl the HTML book.
3. If an HTML-only book title or slug contains `technical architecture`, `diagram`, `diagrams`, `reference architecture`, or `reference architectures`, collect its underlying SVG files under `./raw/images/{version}`.
4. If an HTML-only book title or slug contains `API`, `APIs`, or `Javadoc`, write it under `./raw/api/{version}/{slug}`.
5. Keep the original title, source URLs, method, collection, local paths, status, byte counts, hashes, and errors in the manifest.

The all-books zip URL is metadata only in phase 1. Record it in `manifest.json` with `zip_deferred: true`.

## HTML Crawl Rules

For HTML-only books:

- Crawl sequentially by default.
- Stay on the same origin as the starting book URL.
- Stay within the starting book URL subtree for HTML pages.
- Track both fetched pages and queued pages so large API references do not repeatedly enqueue duplicate navigation links.
- Exclude global navigation, search, sign-in, and unrelated links by enforcing the subtree rule.
- Download useful same-origin assets referenced by crawled HTML pages when bounded and practical.
- Skip external domains.
- Respect Oracle `robots.txt` with Python's `urllib.robotparser`.
- Mark robots-disallowed pages or assets in the manifest and continue.
- Treat optional asset fetch failures as warnings when the HTML pages themselves are captured.

## Profile-Aware HTML Acquisition

HTML-only acquisition is a phase 1 raw acquisition responsibility, not a later phase. The collector must not treat all HTML-only books as one generic shape. It should classify each HTML item into a crawl profile, record that profile in the manifest, and validate completion with profile-specific success criteria.

Supported profiles:

- `ohc_book`: Oracle Help Center book pages. These pages often expose the real book through metadata navigation such as `<link rel="contents" href="toc.htm">` rather than visible body links.
- `javadoc_api`: Java API reference documentation, including old frames-based Javadoc and newer no-frames Javadoc layouts.
- `versioned_latest_book`: Oracle product documentation landing pages that list multiple release folders and require selecting only the latest release before raw acquisition.
- `generic_html`: fallback for HTML-only items that are neither Oracle Help Center books nor Javadoc/API-shaped documents.

Profile detection starts with the bookshelf item metadata, then fetches the start page and inspects its HTML. Title/slug API classification remains the first pass for collection placement, but profile detection may conservatively override an HTML item into the `api` collection when the fetched page is clearly Javadoc/API-shaped. Ordinary docs must not be moved into `api` merely because they contain incidental code or API terms.

Every HTML item should include an `html_profile` manifest block with fields such as:

- `profile`
- `start_url`
- `effective_root`
- `required_pages`
- `required_pages_saved`
- `html_pages_saved`
- `asset_warnings`
- `validation_status`
- `validation_errors`

The profile block is the review surface for answering why the collector considered an HTML crawl complete.

### Oracle Help Center Books

For `ohc_book`:

- Treat `<link rel="contents">`, especially `toc.htm`, as an authoritative crawl seed.
- Crawl TOC-listed pages as required pages.
- After TOC pages are queued, recursively follow bounded same-subtree content links discovered inside content pages.
- Treat TOC-listed content pages as required. Treat additional same-subtree content links as best-effort unless they look like ordinary book pages.
- Ignore localized `rel="alternate"` links such as `hreflang` variants; phase 1 collects the language selected by the bookshelf URL.
- Do not execute JavaScript. Static `toc.htm` and same-subtree HTML pages are authoritative for required content.
- Download same-origin shared JS, CSS, images, and other display assets as best-effort assets when referenced, but do not require them for success.

An `ohc_book` crawl succeeds only when it saves the TOC and at least one content page beyond `index.html`. A shell-only capture is a validation failure even if `index.html` and assets were saved.

### Javadoc API References

For `javadoc_api`:

- Support frames-based entry pages by seeding from `<frame src="...">` and `<iframe src="...">`.
- Support no-frames layouts by seeding from well-known pages such as `overview-summary.html`, `overview-frame.html`, `allclasses-frame.html`, `allclasses-index.html`, `index-all.html`, `help-doc.html`, `package-list`, and `element-list` when present.
- Use the effective final response URL as the crawl root when the bookshelf URL is a lookup or redirect URL such as `/pls/topic/lookup?...`.
- Preserve Javadoc directory structure under `raw/api/{version}/{slug}/`, such as `oracle/pgx/api/package-summary.html` or `oracle/jdbc/OracleConnection.html`. Hash filenames only for query strings or true path collisions.
- Crawl exhaustively within the effective Javadoc root, including overview pages, frame targets, package summaries, class/interface/enum/annotation pages, index pages, member/search pages, and same-root linked support files.
- Download generated search/index support files such as `element-list`, `package-list`, `member-search-index.js`, `type-search-index.js`, and `search.js` when referenced or when well-known for the detected Javadoc version.
- Treat missing display assets such as CSS, JavaScript, images, and other non-HTML presentation files as warnings when package/class HTML pages are successfully saved.

A `javadoc_api` crawl succeeds only when it saves API content beyond the landing page, such as frame targets, package summaries, class pages, or API indexes. Asset failures are warnings unless the missing item is an HTML frame, package, class, or navigation page required to traverse the API.

### Generic HTML

`generic_html` preserves the original bounded crawler behavior and the looser success criterion of saving at least one page. It still records an `html_profile` block so suspicious successes can be found later.

### Versioned Latest Books

For `versioned_latest_book`:

- Detect the profile conservatively when a start page is not an ordinary book page and contains multiple same-product links with version-like path segments such as `19.1`, `20.1`, `21.2`, `23.2`, or `25.2`.
- Choose the latest release by parsing version-like segments and comparing dot-separated numeric components. Ignore non-version links and localized alternates.
- Apply the behavior generically to any matching Oracle documentation landing page, but treat Spatial Studio as the current 26ai acceptance case. Use 21c and 19c later as validation surfaces for similar pages.
- Record the original landing URL, selected release, selected release URL, and selected release books page in `html_profile`.
- Prefer the selected release PDF when one is discoverable from the selected release page or books page. In that case, ingest it like any other PDF under `./raw/{version}/{slug}.pdf`.
- Preserve the top-level item `method: "html_crawl"` because the bookshelf entry itself has no PDF link, but record `output.acquisition_method: "versioned_latest_pdf"` and the source PDF URL/filename.
- If no PDF is available for the latest release, crawl only the latest release HTML and strip the selected release path segment from local paths. For example, `25.2/spstu/index.html` should become `spatial-studio-guide/spstu/index.html`, not `spatial-studio-guide/25.2/spstu/index.html`.
- Delete any old multi-version active folder only after the latest-release PDF or stripped HTML fallback is successfully staged and validated.

The immediate acceptance case is `spatial-studio-guide` for 26ai. Success means:

- `selected_release` is `25.2`
- the source PDF URL is recorded
- the active artifact is `./raw/26ai/spatial-studio-guide.pdf`
- the old `./raw/26ai/spatial-studio-guide/` folder is gone after success
- the manifest item status is `success`
- `output.acquisition_method` is `versioned_latest_pdf`
- `html_profile.profile` is `versioned_latest_book`
- remaining failures decrease by one

### Suspicious HTML Detection

For repair planning, flag any non-image `html_crawl` item as suspicious when it has fewer than two saved HTML pages or when profile-specific required evidence is missing. For `ohc_book`, missing `toc.htm` or missing content pages is suspicious. For `javadoc_api`, missing frame targets, package summaries, class pages, or API indexes beyond the landing page is suspicious.

### Targeted Repair Mode

Add targeted repair options to the existing collector script rather than creating a separate repair script. The repair flow should support:

- explicit slug targeting, for example `--repair-slug database-sample-schemas,trusted-answer-search-user-s-guide`
- suspicious HTML targeting, for example `--repair-suspicious-html`

Repair mode must stage each repaired item first, validate the staged result, and replace the old active item folder only after profile validation succeeds. If staged repair fails, keep the previous active folder and move the failed staging output under a diagnostic path such as:

```text
./raw/_repair_failed/{version}/{slug}-{timestamp}
```

Record lightweight repair history on repaired items, including prior status, prior file count, repair timestamp, repair mode/profile, and whether the old active folder was replaced. Do not store full prior manifest copies inside each item.

The first acceptance run for this design targets only these 26ai slugs:

- `database-sample-schemas`
- `trusted-answer-search-user-s-guide`
- `ai-database-advanced-queuing-java-api-reference`
- `graph-java-api-reference-for-property-graph-javadoc`

After those four pass and are reviewed, decide whether to run suspicious HTML repair across all API docs.

## API Collection

Classify HTML-only books as API/reference corpora when the title or slug contains `API`, `APIs`, or `Javadoc`, case-insensitively. Store those crawls under `./raw/api/{version}/{slug}`. Keep PDFs under `./raw/{version}` even when their titles mention APIs, because this phase separates HTML API corpora from ordinary HTML/prose corpora.

The manifest for the version remains `./raw/{version}/manifest.json`. For each item, record:

- `output.collection`: `api` or `docs`
- `output.base_dir`: `api/{version}` or `{version}`
- `output.paths`: paths relative to the collection base directory

This preserves a single run manifest while allowing downstream ingestion to apply different parsing, chunking, and indexing rules to API references.

## Images Collection

Classify HTML-only books as image collection items when the title or slug contains `technical architecture`, `diagram`, `diagrams`, `reference architecture`, or `reference architectures`, case-insensitively. This includes image-heavy Oracle interactive architecture and diagram entries whose initial page is only a wrapper or redirect page.

For image collection items:

- Start from the bookshelf item's `html_url`.
- Follow HTTP redirects and HTML meta-refresh redirects, such as `URL=db_dbserver.html`.
- Scan fetched same-origin HTML, JavaScript, and config-like text for `.svg` references, including URLs embedded in inline scripts or JSON-like text rather than HTML attributes.
- For Oracle interactive diagram pages, follow the diagram/control script path, such as `app-config`, `manifest`, `app-main`, and `article`, rather than broad-scanning generic framework libraries whose minified code may contain non-URL tokens ending in `.html` or `.svg`.
- Resolve relative SVG references against the document URL where they were found.
- Enforce same-origin and the item's effective subtree before downloading SVGs.
- Do not use a broad scan of the entire already-downloaded raw tree as the collection strategy.
- Save only discovered `.svg` files as raw corpus artifacts.
- Ignore common page chrome SVGs, such as Oracle logos and favicons, and navigation-only pages such as `all_diagrams.html` when deriving SVG payload names.
- Do not retain wrapper HTML, favicon, JavaScript, CSS, or other non-SVG assets for these items.

Flatten SVG files under `./raw/images/{version}`. Use the source SVG basename as the local filename when unique. If two distinct SVG URLs share the same basename, append a short URL hash suffix such as `{basename}-{hash8}.svg`. If two URLs produce identical SVG content hashes, keep one file and record all source URLs as duplicates in the manifest.

Treat zero discovered SVG URLs as a hard item failure. Save successfully downloaded SVGs when possible, but mark the image item failed if any discovered SVG cannot be downloaded. For failed image items, record scanned pages and errors/notes in the manifest.

When replacing an existing shallow HTML capture, delete the old `./raw/{version}/{slug}` folder only after the corresponding image collection item succeeds. If no SVGs are found or any discovered SVG download fails, keep the old shallow folder for troubleshooting.

The manifest for the version remains `./raw/{version}/manifest.json`. For each image item, record:

- `output.collection`: `images`
- `output.base_dir`: `images/{version}`
- `output.paths`: flattened SVG filenames relative to `./raw/images/{version}`
- discovered SVG source URLs
- duplicate URL/content-hash information when applicable
- scanned source pages and image-specific errors

This preserves a single run manifest while allowing downstream ingestion to apply image-specific processing to architecture and diagram SVGs.

## Network Behavior

Use polite defaults:

- User agent: `build-oracle-docs-kb/0.1`
- Request timeouts
- Retry transient failures with backoff
- Delay between crawled HTML page requests
- Sequential downloads and crawls in phase 1

Do not add concurrency until the baseline collector, manifest, and resumability are validated.

## Resumability

On rerun:

- Reuse completed items when local files still exist and hashes match the prior manifest.
- Retry missing, failed, changed, or incomplete items.
- Provide a `--force` option to redownload even when prior output appears valid.
- Write atomic checkpoint versions of `manifest.json` and `summary.txt` after each completed item so long runs can resume after interruption.
- When a run is interrupted before a completed manifest exists, reuse already-downloaded nonempty PDFs by hashing the local file and recording the reuse source. Re-crawl HTML books unless a prior manifest marks them complete, because a partially interrupted HTML crawl can leave an incomplete directory.

The script continues through item failures by default, records failures in the manifest, and exits nonzero when any required item failed. A future `--fail-fast` option may be added if needed.

## Dry Run

Dry run mode must:

- Validate the URL.
- Parse the bookshelf.
- Infer the version.
- Choose `pdf` or `html_crawl` for every discovered book.
- Generate stable planned slugs and output paths.
- Write or print planned manifest information without downloading book contents.

Dry run may create the output directory and write planning artifacts.

## File Naming

Generate stable filesystem-safe slugs from book titles:

- Lowercase words.
- ASCII transliteration when practical.
- Replace punctuation and whitespace with hyphens.
- Collapse repeated separators.
- De-duplicate collisions with a short deterministic suffix.

Examples:

- `Database Concepts` -> `database-concepts.pdf`
- `AI Database Advanced Queuing Java API Reference` -> `ai-database-advanced-queuing-java-api-reference/`

The manifest preserves exact titles and URLs.

## Manifest Schema

Use this top-level v1 shape:

```json
{
  "schema_version": 1,
  "skill": "build-oracle-docs-kb",
  "source": {
    "bookshelf_url": "...",
    "version": "26ai",
    "zip_url": "...",
    "zip_deferred": true
  },
  "run": {
    "started_at": "...",
    "finished_at": "...",
    "dry_run": false,
    "status": "success|partial_failure|failed"
  },
  "items": [
    {
      "title": "...",
      "slug": "...",
      "method": "pdf|html_crawl",
      "html_url": "...",
      "pdf_url": "...",
      "output": {
        "collection": "docs|api|images",
        "base_dir": "26ai|api/26ai|images/26ai",
        "paths": ["..."],
        "bytes": 123,
        "sha256": "..."
      },
      "status": "planned|success|failed|skipped|reused",
      "errors": []
    }
  ],
  "deferred": [
    "Revisit zip acceleration path after baseline per-book collector is validated.",
    "Revisit support for other Oracle documentation library URLs and test against non-database products."
  ]
}
```

The script may add detailed crawl fields under each item as needed, such as per-file hashes, skipped URLs, and crawl counts.

## Summary

Every run writes a human-readable `summary.txt` next to `manifest.json`. The summary includes:

- Source URL and version
- Dry-run flag
- Run status
- Counts by method
- Counts by collection, including `docs`, `api`, and `images`
- Counts by item status
- Failure list
- Robots skips
- Deferred zip URL metadata

For image item failures, include a clear first reason such as `zero SVGs discovered` or `2 SVG downloads failed`. The console summary should mirror the important parts of `summary.txt`.

## Dependencies

Start with Python standard library only. If the Oracle HTML proves too brittle for standard parsing, `beautifulsoup4` may be added without further approval.

## Validation Before Done

Minimum validation:

1. Run skill validation with `quick_validate.py`.
2. Run fixture dry-run against the bundled local bookshelf fixture.
3. Run one live dry-run against an accepted Oracle bookshelf URL without downloading books.

Do not perform a full Oracle documentation download during skill creation unless explicitly requested later.

## Deferred Decisions

- Revisit zip acceleration after the baseline per-book collector is validated.
- Revisit support for other Oracle documentation library URLs and non-database products.
- Revisit concurrency only after correctness, politeness, and resumability are proven.
- Revisit text extraction, chunking, embeddings, indexing, and retrieval in later phases.
