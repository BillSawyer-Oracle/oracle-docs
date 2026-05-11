# Oracle Trusted Sources for Error Lookup

When an Oracle error is not found in Oracle Error Help, use these curated
sources for fallback web searches. Sources are listed in priority order.

## High-Quality Sources (prioritize these)

These are authoritative, well-maintained sources with accurate Oracle content.

| Source | URL Pattern | Notes |
|---|---|---|
| Oracle Documentation | `docs.oracle.com` | Official docs, manuals, reference guides |
| My Oracle Support (MOS) | `support.oracle.com` | Oracle's official support portal; some content requires login |
| Ask Tom | `asktom.oracle.com` | Oracle expert Q&A by Tom Kyte and team; excellent for practical advice |
| Oracle Blogs | `blogs.oracle.com` | Official Oracle employee and product blogs |
| Oracle-Base | `oracle-base.com` | Tim Hall's community site; widely respected, thorough articles |
| Any other oracle.com subdomain | `*.oracle.com` | Official Oracle properties (e.g., community.oracle.com, forums.oracle.com) |

## How to use this list

When constructing a fallback web search query:

1. Search for the error code (e.g., `ORA-01403`) along with relevant context
2. Prefer results from High-Quality Sources above
3. If high-quality sources do not return useful results, results from other
   domains may be presented, but note to the user that they are not from
   Oracle-curated sources
4. Never direct users to sites known for low-quality, scraped, or
   ad-heavy Oracle content

## Search query format

Use a query like:

```
ORA-01403 site:docs.oracle.com OR site:support.oracle.com OR site:asktom.oracle.com OR site:blogs.oracle.com OR site:oracle-base.com
```

If that yields no results, broaden to a general search for the error code
without site restrictions, but flag the source quality to the user.
