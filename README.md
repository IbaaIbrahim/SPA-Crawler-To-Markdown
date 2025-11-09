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

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--start-url` | *required* | Starting URL of the SPA |
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

**Crawl stops early**:
- Increase `--max-pages`
- Check `--same-origin` setting (default only crawls same domain)
- Increase `--timeout-ms` for slow-loading pages

**Memory issues with large crawls**:
- Reduce `--concurrency` (default 5, try 2-3)
- Use `--include-html false` (default)
- Split into multiple smaller crawls

## License

MIT
