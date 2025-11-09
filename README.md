# SPA Crawler

A Playwright-based crawler designed for React SPAs and dynamic JavaScript applications. Discovers links, scrapes content, and exports to JSON/Markdown.

## Features

- **React/SPA Aware**: Waits for JS execution and React hydration before extracting content
- **Deep DOM Extraction**: Traverses shadow DOMs and React roots to capture dynamically rendered content
- **Content Scraping**: Extracts page title and readable text from each visited page
- **Multiple Output Formats**: JSON (structured data) and Markdown (combined or per-page)
- **Configurable Crawling**: Control concurrency, timeouts, wait conditions, and more

## Install

```bash
pip install -e .
playwright install chromium
```

Or using requirements.txt:

```bash
pip install -r requirements.txt
playwright install chromium
```

## Basic Usage

### Crawl and extract links only

```bash
python -m spa_crawler \
  --start-url "https://your-react-app.example" \
  --out "outputs/sitemap.json"
```

### Crawl and scrape full content

```bash
python -m spa_crawler \
  --start-url "https://your-react-app.example" \
  --out "outputs/sitemap.json" \
  --scrape true \
  --markdown-out "outputs/all-content.md"
```

This creates:
- `outputs/sitemap.json`: Array of pages with URL, status, depth, title, and text
- `outputs/all-content.md`: Single Markdown file with all page content

### Seed from existing sitemap (re-crawl specific URLs)

```bash
python -m spa_crawler \
  --urls-file "outputs/sitemap.json" \
  --no-discover \
  --scrape true \
  --markdown-out "outputs/content.md"
```

This reads URLs from an existing sitemap and only visits those pages (no new link discovery).

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--start-url` | *optional* | Starting URL of the SPA (required if `--urls-file` not provided) |
| `--urls-file` | `None` | Path to JSON file containing URLs to crawl (alternative to `--start-url`) |
| `--no-discover` | `false` | Disable link discovery; only visit provided URLs |
| `--out` | `outputs/sitemap.json` | Path to JSON output file |
| `--scrape` | `true` | Scrape page content (title + text) |
| `--markdown-out` | `None` | Optional: path to combined Markdown output |
| `--same-origin` | `true` | Limit crawling to same origin |
| `--max-pages` | `1000` | Maximum number of pages to crawl |
| `--concurrency` | `5` | Number of concurrent browser contexts |
| `--timeout-ms` | `20000` | Page load timeout in milliseconds |
| `--wait-until` | `networkidle` | Playwright wait condition: `load`, `domcontentloaded`, or `networkidle` |
| `--wait-selector` | `None` | CSS selector to wait for before extracting |
| `--wait-text-growth-ms` | `0` | Poll for text growth (dynamic content loading) |
| `--include-html` | `false` | Include raw HTML in output |
| `--retry-failed` | `true` | Automatically retry timed-out URLs with doubled timeout |
| `--headless` | `true` | Run browser in headless mode |

## Examples

### Knowledge base crawl with content extraction

```bash
python -m spa_crawler \
  --start-url "https://docs.example.com" \
  --max-pages 100 \
  --scrape true \
  --markdown-out "outputs/docs.md" \
  --concurrency 3
```

### Wait for specific content to load

```bash
python -m spa_crawler \
  --start-url "https://spa.example.com" \
  --wait-selector ".article-body" \
  --wait-text-growth-ms 3000 \
  --scrape true
```

### Debug mode with HTML capture

```bash
python -m spa_crawler \
  --start-url "https://app.example.com" \
  --max-pages 10 \
  --include-html true \
  --headless false
```

### Seed from existing URLs (no discovery)

```bash
# Re-scrape specific URLs without discovering new links
python -m spa_crawler \
  --urls-file "outputs/sitemap.json" \
  --no-discover \
  --scrape true \
  --out "outputs/content.json" \
  --markdown-out "outputs/content.md"
```

### Seed with additional discovery

```bash
# Start from known URLs but allow discovery of new links
python -m spa_crawler \
  --urls-file "outputs/seed-urls.json" \
  --max-pages 500 \
  --scrape true
```

## Output Format

### JSON Structure

```json
[
  {
    "url": "https://example.com/page",
    "status": 200,
    "depth": 0,
    "title": "Page Title",
    "text": "Extracted readable text content...",
    "raw_html": null
  }
]
```

### Markdown Format

Each page in the combined Markdown file:

```markdown
# Page Title

URL: https://example.com/page

Extracted readable text content...

---
```

## URLs File Format

The `--urls-file` option accepts JSON files in multiple formats:

### Array of strings
```json
[
  "https://example.com/page1",
  "https://example.com/page2",
  "https://example.com/page3"
]
```

### Array of objects (sitemap format)
```json
[
  {"url": "https://example.com/page1", "status": 200},
  {"url": "https://example.com/page2", "status": 200}
]
```

The parser automatically detects common URL keys: `url`, `href`, `loc`, `link`.

### Nested structures
```json
{
  "urls": [
    "https://example.com/page1",
    "https://example.com/page2"
  ]
}
```

Common nested keys like `urls`, `links`, `items`, `pages` are automatically extracted.

## React/SPA Support

The crawler is optimized for React applications:

1. **Waits for React hydration**: Detects common React root elements (`#root`, `#app`, `#__next`, `[data-reactroot]`)
2. **Dynamic content detection**: Waits until body has meaningful text content (>100 chars)
3. **Shadow DOM traversal**: Extracts text from shadow roots and nested components
4. **Noise filtering**: Automatically filters out navigation, headers, footers, and accessibility widgets

## Advanced Usage

### Convert JSON to per-page Markdown files

```python
import json
from pathlib import Path

data = json.loads(Path("outputs/sitemap.json").read_text())

for item in data:
    slug = item['url'].split('/')[-1] or 'index'
    Path(f"outputs/md/{slug}.md").write_text(
        f"# {item.get('title', item['url'])}\n\n{item.get('text', '')}"
    )
```

## Troubleshooting

**No content extracted (text is null)**:
- Increase `--wait-text-growth-ms` (try 3000-5000ms)
- Specify `--wait-selector` for a key content element
- Try `--headless false` to see what the browser renders
- Check if site requires authentication (cookies not yet supported)

**Crawl discovers fewer links than expected (React/SPA sites)**:
- React sites render links dynamically after page load
- Use `--wait-text-growth-ms 5000` to wait for React to finish rendering
- Use `--wait-until load` instead of `domcontentloaded`
- Try single-threaded discovery: `--concurrency 1`
- **Recommended**: Use two-phase approach (see below)
- If some URLs time out, the crawler automatically retries them with both `timeout-ms` and `wait-text-growth-ms` doubled for the retry phase. This increases the chance of capturing slow or late-rendered content.

**Browser crashes or "Target closed" errors**:
- Reduce `--concurrency` to 1 or 2
- Increase `--timeout-ms` for slow sites
- Use `--wait-until domcontentloaded` instead of `load` (faster)
- Split crawl into discovery phase (no scraping) + content phase (no discovery)

**Crawl stops early**:
- Increase `--max-pages`
- Check `--same-origin` setting (default only crawls same domain)
- Increase `--timeout-ms` for slow-loading pages

**Memory issues with large crawls**:
- Reduce `--concurrency` (default 5, try 2-3)
- Use `--include-html false` (default)
- Split into multiple smaller crawls

### Two-Phase Crawl Strategy (Recommended for Large Sites)

For React/SPA sites or large knowledge bases, use a two-phase approach for better reliability:

**Phase 1: URL Discovery (slow, thorough)**
```bash
# Discover all URLs without scraping content
python -m spa_crawler \
  --start-url "https://your-site.com/kb" \
  --scrape false \
  --wait-until load \
  --wait-text-growth-ms 5000 \
  --timeout-ms 60000 \
  --concurrency 1 \
  --max-pages 500 \
  --out outputs/all-urls.json
```

**Phase 2: Content Extraction (fast, parallel)**
```bash
# Scrape content from discovered URLs
python -m spa_crawler \
  --urls-file outputs/all-urls.json \
  --no-discover \
  --scrape true \
  --wait-until domcontentloaded \
  --timeout-ms 40000 \
  --concurrency 2 \
  --markdown-out outputs/final-content.md
```

**Benefits**:
- ✅ Phase 1 is slow but reliable (single-threaded prevents crashes)
- ✅ Phase 2 is fast (parallel processing, only known URLs)
- ✅ Can retry Phase 2 without re-discovering URLs
- ✅ Handles React lazy-loading with `wait-text-growth-ms`
- ✅ Failed URLs are automatically retried with doubled timeout and wait-text-growth-ms for better coverage of slow or dynamic pages

## License

MIT
