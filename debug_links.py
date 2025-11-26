#!/usr/bin/env python3
import asyncio
from playwright.async_api import async_playwright

async def extract_links():
    url = "https://pro.arcgis.com/en/pro-app/latest/help/main/welcome-to-the-arcgis-pro-app-help.htm"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        print(f"Visiting {url}")
        await page.goto(url, timeout=60000, wait_until="load")
        
        # Wait for content to load
        await page.wait_for_timeout(5000)
        
        # Check for navigation structure
        print("\n=== DEBUGGING NAVIGATION STRUCTURE ===")
        
        # Look for common navigation elements
        nav_selectors = [
            'nav', '.nav', '.navigation', '.sidebar', '.menu', 
            '.toc', '.table-of-contents', '[role="navigation"]',
            '#toc', '#sidebar', '#nav', '#navigation'
        ]
        
        for selector in nav_selectors:
            try:
                elements = await page.query_selector_all(selector)
                if elements:
                    print(f"\nFound {len(elements)} element(s) with selector: {selector}")
                    for i, el in enumerate(elements[:3]):
                        text = await el.inner_text()
                        if text:
                            print(f"  Content preview: {text[:200]}...")
            except Exception as e:
                pass
        
        # Extract all links
        js_extract = """
        (() => {
            const links = new Set();
            
            // Get all anchor tags
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.getAttribute('href');
                if (href) links.add(href);
            });
            
            return Array.from(links);
        })()
        """
        
        links = await page.evaluate(js_extract)
        
        print("\n=== LINK EXTRACTION ===")        
        
        print(f"\nFound {len(links)} raw links:")
        for i, link in enumerate(links[:20], 1):  # Show first 20
            print(f"{i}. {link}")
        
        if len(links) > 20:
            print(f"... and {len(links) - 20} more")
        
        # Now convert to absolute URLs
        from urllib.parse import urljoin
        absolute_links = []
        for link in links:
            abs_link = urljoin(url, link)
            absolute_links.append(abs_link)
        
        print(f"\n\nAfter converting to absolute URLs:")
        for i, link in enumerate(absolute_links[:20], 1):
            print(f"{i}. {link}")
        
        if len(absolute_links) > 20:
            print(f"... and {len(absolute_links) - 20} more")
        
        # Filter by pattern
        pattern = "https://pro.arcgis.com/en/pro-app/latest/help/"
        filtered = [link for link in absolute_links if link.startswith(pattern)]
        
        print(f"\n\nAfter filtering by pattern '{pattern}':")
        print(f"Filtered to {len(filtered)} links:")
        for i, link in enumerate(filtered[:20], 1):
            print(f"{i}. {link}")
        
        if len(filtered) > 20:
            print(f"... and {len(filtered) - 20} more")
        
        # Look for links that might be in iframes or dynamically loaded
        print("\n=== CHECKING FOR IFRAMES AND DYNAMIC CONTENT ===")
        frames = page.frames
        print(f"Found {len(frames)} frames")
        for i, frame in enumerate(frames):
            print(f"Frame {i}: {frame.url}")
            if i > 0:  # Skip main frame
                try:
                    frame_links = await frame.eval_on_selector_all(
                        "a[href]", "els => els.map(a => a.href)"
                    )
                    print(f"  Found {len(frame_links)} links in frame")
                    frame_filtered = [link for link in frame_links if pattern in link]
                    if frame_filtered:
                        print(f"  Matching pattern: {len(frame_filtered)}")
                        for link in frame_filtered[:5]:
                            print(f"    - {link}")
                except Exception as e:
                    print(f"  Error extracting from frame: {e}")
        
        # Check if links are loaded via JavaScript after page load
        print("\n=== WAITING FOR ADDITIONAL CONTENT ===")
        print("Waiting 10 seconds for dynamic content...")
        await page.wait_for_timeout(10000)
        
        links2 = await page.evaluate(js_extract)
        print(f"After waiting: Found {len(links2)} links (was {len(links)} before)")
        
        absolute_links2 = [urljoin(url, link) for link in links2]
        filtered2 = [link for link in absolute_links2 if link.startswith(pattern)]
        
        if len(filtered2) > len(filtered):
            print(f"New matching links found:")
            new_links = set(filtered2) - set(filtered)
            for i, link in enumerate(list(new_links)[:10], 1):
                print(f"{i}. {link}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(extract_links())
