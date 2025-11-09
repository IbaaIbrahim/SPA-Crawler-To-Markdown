import argparse
import asyncio
import json
from pathlib import Path
from .crawler import SpaCrawler

def main():
    parser = argparse.ArgumentParser(description="Crawl a React SPA and export discovered links.")
    parser.add_argument("--start-url", required=False, help="Starting URL of the SPA.")
    parser.add_argument("--urls-file", required=False, help="Path to a JSON file containing a list of URLs (e.g., outputs/sitemap.json).")
    parser.add_argument("--no-discover", action="store_true", help="Do not discover new links; only visit the provided URLs.")
    parser.add_argument("--out", default="outputs/sitemap.json", help="Path to JSON output.")
    parser.add_argument("--same-origin", type=str, default="true", help="Limit to same origin (true/false).")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--timeout-ms", type=int, default=20000)
    parser.add_argument("--headless", type=str, default="true")
    parser.add_argument("--wait-until", type=str, default="networkidle", choices=["load","domcontentloaded","networkidle"])
    parser.add_argument("--scrape", type=str, default="true", help="Scrape page content and include it in the JSON (true/false).")
    parser.add_argument("--markdown-out", type=str, default=None, help="Optional: path to write a combined Markdown file of all pages.")
    parser.add_argument("--wait-selector", type=str, default=None, help="CSS selector to wait for before extracting content.")
    parser.add_argument("--wait-text-growth-ms", type=int, default=0, help="Poll for text growth up to N milliseconds (dynamic content).")
    parser.add_argument("--include-html", type=str, default="false", help="Include raw HTML for each page (true/false).")
    parser.add_argument("--retry-failed", type=str, default="true", help="Automatically retry timed-out URLs with doubled timeout (true/false).")
    parser.add_argument("--log-console", type=str, default="false", help="Print page console warnings/errors and runtime errors (true/false).")
    parser.add_argument("--log-network", type=str, default="false", help="Print network responses with status >= 400 (true/false).")

    args = parser.parse_args()

    if not args.start_url and not args.urls_file:
        parser.error("You must provide either --start-url or --urls-file")

    same_origin = args.same_origin.lower() == "true"
    headless = args.headless.lower() == "true"
    scrape = args.scrape.lower() == "true"
    include_html = args.include_html.lower() == "true"
    retry_failed = args.retry_failed.lower() == "true"
    log_console = args.log_console.lower() == "true"
    log_network = args.log_network.lower() == "true"

    out_json = Path(args.out)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    # Load URLs from file if provided
    start_urls = None
    if args.urls_file:
        p = Path(args.urls_file)
        if not p.exists():
            parser.error(f"URLs file not found: {args.urls_file}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            parser.error(f"Failed to parse URLs file: {e}")
        urls: list[str] = []
        def collect_from(obj):
            nonlocal urls
            if isinstance(obj, list):
                for item in obj:
                    collect_from(item)
            elif isinstance(obj, dict):
                # common keys that may hold URLs
                for key in ("url", "href", "loc", "link"):  # sitemap variants
                    v = obj.get(key)
                    if isinstance(v, str):
                        urls.append(v)
                # nested arrays commonly used
                for key in ("urls", "links", "items", "pages"):
                    if key in obj and isinstance(obj[key], list):
                        collect_from(obj[key])
            elif isinstance(obj, str):
                urls.append(obj)
        collect_from(data)
        # de-duplicate while preserving order
        seen = set()
        deduped = []
        for u in urls:
            if not isinstance(u, str):
                continue
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        start_urls = deduped

    crawler = SpaCrawler(
        start_url=args.start_url,
        start_urls=start_urls,
        same_origin_only=same_origin,
        max_pages=args.max_pages,
        concurrency=args.concurrency,
        timeout_ms=args.timeout_ms,
        wait_until=args.wait_until,
        headless=headless,
        scrape_content=scrape,
        wait_selector=args.wait_selector,
        wait_text_growth_ms=args.wait_text_growth_ms,
        include_html=include_html,
        log_network=log_network,
        log_console=log_console,
        discover_links=(not args.no_discover),
        retry_failed=retry_failed,
    )

    asyncio.run(crawler.run())

    data = crawler.to_json()
    with out_json.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Optionally write markdown aggregation
    if args.markdown_out:
        md_path = Path(args.markdown_out)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        def to_md(item):
            title = item.get("title") or item.get("url")
            body = item.get("text") or ""
            return f"# {title}\n\nURL: {item.get('url')}\n\n{body}\n\n---\n\n"
        content = "".join(to_md(item) for item in data)
        md_path.write_text(content, encoding="utf-8")

    print(f"Wrote {len(crawler.results)} pages to {out_json}{' and ' + args.markdown_out if args.markdown_out else ''}")
