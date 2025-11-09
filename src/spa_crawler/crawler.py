import asyncio
from typing import Set, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from tqdm import tqdm
from playwright.async_api import async_playwright
from .utils import canonicalize, absolutize, same_origin

@dataclass
class VisitResult:
    url: str
    status: Optional[int]
    depth: int
    title: Optional[str] = None
    text: Optional[str] = None
    raw_html: Optional[str] = None

class SpaCrawler:
    def __init__(self, start_url: Optional[str] = None, start_urls: Optional[List[str]] = None, same_origin_only: bool = True, max_pages: int = 1000, concurrency: int = 5, timeout_ms: int = 20000, wait_until: str = "networkidle", user_agent: Optional[str] = None, headless: bool = True, extra_headers: Optional[Dict[str, str]] = None, scrape_content: bool = False, max_text_chars: int = 100_000, wait_selector: Optional[str] = None, wait_text_growth_ms: int = 0, include_html: bool = False, screenshot_dir: Optional[str] = None, log_network: bool = False, log_console: bool = False, discover_links: bool = True, retry_failed: bool = True):
        self.start_url = canonicalize(start_url) if start_url else None
        # Normalize and set starting URLs list (prefer start_urls; fall back to start_url)
        initial_urls = start_urls or ([start_url] if start_url else [])
        self.start_urls = [canonicalize(u) for u in initial_urls if u]

        self.same_origin_only = same_origin_only
        self.max_pages = max_pages
        self.concurrency = concurrency
        self.timeout_ms = timeout_ms
        self.wait_until = wait_until
        self.user_agent = user_agent
        self.headless = headless
        self.extra_headers = extra_headers or {}
        self.scrape_content = scrape_content
        self.max_text_chars = max_text_chars
        self.wait_selector = wait_selector
        self.wait_text_growth_ms = wait_text_growth_ms
        self.include_html = include_html
        self.screenshot_dir = screenshot_dir
        self.log_network = log_network
        self.log_console = log_console
        self.discover_links = discover_links
        self.retry_failed = retry_failed

        # Base origin to compare for same_origin filter (use first start URL if present)
        self.origin_base_url = self.start_urls[0] if self.start_urls else self.start_url

        self.visited: Set[str] = set()
        self.results: List[VisitResult] = []
        self.failed_urls: List[Tuple[str, int]] = []  # Track URLs that timed out or failed
        self.queue: asyncio.Queue[Tuple[str, int]] = asyncio.Queue()

    async def _extract_links(self, page) -> List[str]:
        # Collect links from the main page and all frames (helps with sites that render inside iframes)
        links: List[str] = []
        async def collect_from_frame(frame):
            try:
                # Enhanced link extraction for React SPAs
                js_extract = """
                (() => {
                    const links = new Set();
                    const base = window.location.origin;
                    
                    // 1. Traditional anchor tags
                    document.querySelectorAll('a[href]').forEach(a => {
                        const href = a.getAttribute('href');
                        if (href) links.add(href);
                    });
                    
                    // 2. React Router links (onClick handlers, data attributes)
                    document.querySelectorAll('[data-href], [data-url], [data-link]').forEach(el => {
                        const href = el.getAttribute('data-href') || 
                                    el.getAttribute('data-url') || 
                                    el.getAttribute('data-link');
                        if (href) links.add(href);
                    });
                    
                    // 3. Look for href in onclick attributes
                    document.querySelectorAll('[onclick]').forEach(el => {
                        const onclick = el.getAttribute('onclick') || '';
                        const match = onclick.match(/(?:href|url|link)\\s*=\\s*['"]([^'"]+)['"]/);
                        if (match) links.add(match[1]);
                    });
                    
                    // 4. Check for React Router style links (href="#/..." or href="/...")
                    document.querySelectorAll('a, [role="link"], button').forEach(el => {
                        const href = el.getAttribute('href');
                        if (href) {
                            links.add(href);
                        }
                        // Check for data attributes that might contain URLs
                        for (const attr of el.attributes) {
                            if (attr.value && (attr.value.startsWith('/') || attr.value.startsWith('http'))) {
                                // Validate it looks like a URL
                                if (attr.value.match(/^(https?:\\/\\/|\\/).+/)) {
                                    links.add(attr.value);
                                }
                            }
                        }
                    });
                    
                    return Array.from(links);
                })()
                """
                anchors = await frame.evaluate(js_extract)
            except Exception:
                # Fallback to basic extraction
                try:
                    anchors = await frame.eval_on_selector_all(
                        "a[href]", "els => els.map(a => a.getAttribute('href'))"
                    )
                except Exception:
                    anchors = []
            base = frame.url or page.url
            for href in anchors:
                if href:
                    abs_url = canonicalize(absolutize(base, href))
                    if abs_url:
                        links.append(abs_url)

        await collect_from_frame(page.main_frame)
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            await collect_from_frame(frame)
        # De-duplicate while preserving order
        seen = set()
        unique = []
        for u in links:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    async def _extract_text_dom(self, page) -> str:
        # Extract visible text from React SPA after JS execution
        try:
            js = """
            (() => {
              // Helper to get text from an element, traversing shadow DOMs
              const getText = (root) => {
                if (!root) return '';
                // Try innerText first (includes visible text only)
                if (root.innerText) return root.innerText.trim();
                // Fallback to textContent
                if (root.textContent) return root.textContent.trim();
                return '';
              };
              
              // Traverse shadow roots recursively
              const getAllText = (root, collected = []) => {
                if (!root) return collected;
                
                // Get text from this element
                const text = getText(root);
                if (text) collected.push(text);
                
                // Check for shadow root
                if (root.shadowRoot) {
                  getAllText(root.shadowRoot, collected);
                }
                
                // Recurse into children
                if (root.children) {
                  for (const child of root.children) {
                    getAllText(child, collected);
                  }
                }
                
                return collected;
              };
              
              // Try specific selectors first (common React app containers)
              const selectors = [
                '#root', '#app', '#__next', '[data-reactroot]',
                'article', 'main', '[role="main"]', '[role="article"]',
                '.article', '.content', '.post', '.entry-content',
                '.kb-article', '.knowledge-base-article', '.kbContent', '.z_kb',
                '.article-body', '.article-content', '.post-content', '.page-content'
              ];
              
              let parts = [];
              
              // Try each selector
              for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                  const t = getText(el);
                  if (t && t.length > 100) { // Only meaningful content
                    parts.push(t);
                  }
                }
              }
              
              // If we found content in specific areas, use that
              if (parts.length > 0) {
                // Deduplicate (child content might be repeated)
                const unique = [...new Set(parts)];
                return unique.join('\\n\\n');
              }
              
              // Fallback: get all text from body, but filter out nav/header/footer
              const body = document.body;
              if (!body) return '';
              
              // Remove noise elements
              const noise = body.querySelectorAll('script, style, nav, header, footer, .nav, .header, .footer, .sidebar, .menu');
              const tempDiv = body.cloneNode(true);
              tempDiv.querySelectorAll('script, style, nav, header, footer, .nav, .header, .footer, .sidebar, .menu').forEach(el => el.remove());
              
              const bodyText = getText(tempDiv);
              return bodyText || getText(body) || '';
            })()
            """
            text = await page.evaluate(js)
            return text or ""
        except Exception as e:
            return ""

    async def _visit(self, browser, url: str, depth: int) -> Tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
        context = await browser.new_context(user_agent=self.user_agent, extra_http_headers=self.extra_headers)
        page = await context.new_page()
        is_timeout_error = False
        try:
            network_log: List[Dict] = []
            if self.log_network:
                def _on_response(resp):
                    try:
                        entry = {
                            "url": resp.url,
                            "status": resp.status,
                            "content_type": resp.headers.get('content-type', '')
                        }
                        network_log.append(entry)
                        # Print only problematic responses to keep noise low
                        if resp.status and resp.status >= 400:
                            print(f"[network:{resp.status}] {resp.url} ({entry['content_type']})")
                    except Exception:
                        pass
                page.on("response", _on_response)

            # Log page console and runtime errors when enabled
            if self.log_console:
                def _on_console(msg):
                    try:
                        t = msg.type
                        # Only surface warnings and errors by default
                        if t in ("warning", "error"):  # Playwright types: 'log','debug','info','warning','error'
                            loc = msg.location
                            where = f"{loc.get('url','') or page.url}:{loc.get('lineNumber','?')}:{loc.get('columnNumber','?')}" if isinstance(loc, dict) else page.url
                            print(f"[console:{t}] {url} :: {where} :: {msg.text}")
                    except Exception:
                        pass
                def _on_page_error(err):
                    try:
                        print(f"[pageerror] {url} :: {err}")
                    except Exception:
                        pass
                page.on("console", _on_console)
                page.on("pageerror", _on_page_error)
            resp = await page.goto(url, timeout=self.timeout_ms, wait_until=self.wait_until)
            status = resp.status if resp else None
            
            # For React apps: wait for React to hydrate and render
            await page.wait_for_timeout(1000)  # Give React time to hydrate
            
            # Try to wait for content to appear (but don't fail if timeout)
            try:
                await page.wait_for_function(
                    "() => document.body && document.body.innerText.length > 50",
                    timeout=5000
                )
            except Exception:
                pass
            
            # Optional selector wait (dynamic content)
            if self.wait_selector:
                try:
                    await page.wait_for_selector(self.wait_selector, timeout=min(self.timeout_ms, 10_000))
                except Exception:
                    pass
            
            # Additional wait for any lazy-loaded content
            await page.wait_for_timeout(250)
            if self.discover_links:
                links = await self._extract_links(page)
                for link in links:
                    if self.same_origin_only and self.origin_base_url and not same_origin(self.origin_base_url, link):
                        continue
                    if link not in self.visited and len(self.visited) + self.queue.qsize() < self.max_pages:
                        await self.queue.put((link, depth + 1))
            title = None
            text = None
            raw_html = None
            if self.scrape_content:
                try:
                    title = await page.title()
                except Exception:
                    title = None
                try:
                    # First try DOM-based extraction (innerText from key areas)
                    dom_text = await self._extract_text_dom(page)
                    # Extract readable text from main page + all frames
                    from bs4 import BeautifulSoup
                    texts: List[str] = []
                    if dom_text:
                        texts.append(dom_text)
                    # main frame
                    try:
                        html = await page.content()
                        soup = BeautifulSoup(html, "html.parser")
                        for tag in soup(["script", "style", "noscript"]):
                            tag.decompose()
                        texts.append(soup.get_text(separator=" "))
                    except Exception:
                        pass
                    # other frames
                    for frame in page.frames:
                        if frame is page.main_frame:
                            continue
                        try:
                            fhtml = await frame.content()
                            fsoup = BeautifulSoup(fhtml, "html.parser")
                            for tag in fsoup(["script", "style", "noscript"]):
                                tag.decompose()
                            texts.append(fsoup.get_text(separator=" "))
                        except Exception:
                            continue
                    raw_text = "\n".join(t for t in texts if t)
                    # Optionally poll for growth
                    if self.wait_text_growth_ms > 0:
                        import time
                        start = time.time()
                        last_len = len(raw_text)
                        while (time.time() - start) * 1000 < self.wait_text_growth_ms:
                            try:
                                html2 = await page.content()
                                from bs4 import BeautifulSoup as _BS
                                soup2 = _BS(html2, "html.parser")
                                for tag in soup2(["script", "style", "noscript"]):
                                    tag.decompose()
                                new_text = soup2.get_text(separator=" ")
                                # Prefer growth vs previous value
                                if len(new_text) > last_len:
                                    raw_text = new_text
                                    last_len = len(new_text)
                            except Exception:
                                break
                            await page.wait_for_timeout(200)
                    # Normalize whitespace
                    norm = " ".join(raw_text.split())
                    if len(norm) > self.max_text_chars:
                        norm = norm[: self.max_text_chars]
                    text = norm or None
                    if self.include_html:
                        try:
                            raw_html = await page.content()
                        except Exception:
                            raw_html = None
                except Exception:
                    text = None
            # Optional screenshot
            if self.screenshot_dir:
                try:
                    import re, os
                    os.makedirs(self.screenshot_dir, exist_ok=True)
                    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", url)[:200]
                    path = os.path.join(self.screenshot_dir, f"{safe}.png")
                    await page.screenshot(path=path, full_page=True)
                except Exception:
                    pass
            return status, title, text, raw_html
        except Exception as e:
            import traceback
            # Check if it's a timeout error
            error_str = str(e).lower()
            if 'timeout' in error_str or 'exceeded' in error_str:
                is_timeout_error = True
            print(f"Error visiting {url}: {e}")
            traceback.print_exc()
            return None, None, None, None
        finally:
            # Track failed/timed-out URLs for retry
            if is_timeout_error and self.retry_failed:
                self.failed_urls.append((url, depth))
            await context.close()

    async def _worker(self, browser, pbar):
        while True:
            try:
                url, depth = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                return
            if url in self.visited or len(self.visited) >= self.max_pages:
                self.queue.task_done()
                continue
            self.visited.add(url)
            status, title, text, raw_html = await self._visit(browser, url, depth)
            self.results.append(VisitResult(url=url, status=status, depth=depth, title=title, text=text, raw_html=raw_html))
            pbar.update(1)
            self.queue.task_done()

    async def run(self):
        # Seed initial queue with provided URLs
        if self.start_urls:
            for u in self.start_urls:
                await self.queue.put((u, 0))
        elif self.start_url:
            await self.queue.put((self.start_url, 0))
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                with tqdm(total=self.max_pages, desc="Crawling", unit="page") as pbar:
                    workers = [asyncio.create_task(self._worker(browser, pbar)) for _ in range(self.concurrency)]
                    await self.queue.join()
                    for w in workers:
                        w.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
                
                # Retry failed URLs with doubled timeout and wait_text_growth_ms
                if self.retry_failed and self.failed_urls:
                    original_timeout = self.timeout_ms
                    original_wait_text_growth_ms = self.wait_text_growth_ms
                    self.timeout_ms = original_timeout * 2
                    self.wait_text_growth_ms = original_wait_text_growth_ms * 2 if original_wait_text_growth_ms > 0 else 0
                    retry_count = len(self.failed_urls)
                    print(f"\n{retry_count} URLs timed out. Retrying with timeout={self.timeout_ms}ms and wait_text_growth_ms={self.wait_text_growth_ms}ms...")

                    # Remove failed URLs from visited set so they can be retried
                    for url, _ in self.failed_urls:
                        if url in self.visited:
                            self.visited.remove(url)

                    # Re-queue failed URLs
                    for url, depth in self.failed_urls:
                        await self.queue.put((url, depth))

                    # Clear failed list for this retry round
                    self.failed_urls.clear()

                    # Run workers again for retry
                    with tqdm(total=retry_count, desc="Retrying", unit="page") as retry_pbar:
                        workers = [asyncio.create_task(self._worker(browser, retry_pbar)) for _ in range(self.concurrency)]
                        await self.queue.join()
                        for w in workers:
                            w.cancel()
                        await asyncio.gather(*workers, return_exceptions=True)

                    # Restore original timeout and wait_text_growth_ms
                    self.timeout_ms = original_timeout
                    self.wait_text_growth_ms = original_wait_text_growth_ms

                    if self.failed_urls:
                        print(f"\n{len(self.failed_urls)} URLs still failed after retry.")
            finally:
                await browser.close()

    def to_json(self) -> List[Dict]:
        return [asdict(r) for r in self.results]
