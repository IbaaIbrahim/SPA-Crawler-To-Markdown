import argparse
import asyncio
import json
from pathlib import Path
from .crawler import SpaCrawler

def main():
    parser = argparse.ArgumentParser(description="Crawl a React SPA and export discovered links.")
    parser.add_argument("--start-url", required=True, help="Starting URL of the SPA.")
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

    args = parser.parse_args()
    same_origin = args.same_origin.lower() == "true"
    headless = args.headless.lower() == "true"
    scrape = args.scrape.lower() == "true"
    include_html = args.include_html.lower() == "true"

    out_json = Path(args.out)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    crawler = SpaCrawler(
        start_url=args.start_url,
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
