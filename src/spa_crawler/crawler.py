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
    parent_url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    raw_html: Optional[str] = None

# JavaScript injected before any page script executes to remove automation fingerprints
_STEALTH_INIT_SCRIPT = """
// 1. Remove the webdriver property entirely
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Restore a realistic plugins array
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [1, 2, 3, 4, 5].map((_, i) => ({
      name: ['Chrome PDF Plugin','Chrome PDF Viewer','Native Client','Widevine Content Decryption Module','Microsoft Edge PDF Viewer'][i],
      filename: ['internal-pdf-viewer','mhjfbmdgcfjbbpaeojofohoefgiehjai','internal-nacl-plugin','widevinecdmadapter.dll','edge-pdf-viewer'][i],
      description: '',
      length: 0,
    }));
    arr.__proto__ = PluginArray.prototype;
    return arr;
  },
});

// 3. Realistic languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// 4. Realistic hardware concurrency and device memory
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(e) {}

// 5. Restore window.chrome that headless removes
if (!window.chrome) {
  window.chrome = {
    runtime: { id: undefined },
    loadTimes: function() {},
    csi: function() {},
    app: {},
  };
}

// 6. Prevent permission API from leaking automation
const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (_origQuery) {
  window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _origQuery(parameters);
}

// 7. Spoof WebGL vendor/renderer — headless leaks 'SwiftShader'
try {
  const getParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';  // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0, D3D11)';  // UNMASKED_RENDERER_WEBGL
    return getParam.call(this, param);
  };
  const getParam2 = WebGL2RenderingContext.prototype.getParameter;
  WebGL2RenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    return getParam2.call(this, param);
  };
} catch(e) {}

// 8. Canvas fingerprint noise — add imperceptible pixel variation to defeat hash-based detection
try {
  const toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
    const ctx = this.getContext('2d');
    if (ctx) {
      const imageData = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
      imageData.data[0] = imageData.data[0] ^ 1;  // flip 1 bit — invisible but changes hash
      ctx.putImageData(imageData, 0, 0);
    }
    return toDataURL.call(this, type, quality);
  };
} catch(e) {}

// 9. Realistic screen dimensions
try {
  Object.defineProperty(screen, 'width',       { get: () => 1920 });
  Object.defineProperty(screen, 'height',      { get: () => 1080 });
  Object.defineProperty(screen, 'availWidth',  { get: () => 1920 });
  Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
  Object.defineProperty(screen, 'colorDepth',  { get: () => 24 });
  Object.defineProperty(screen, 'pixelDepth',  { get: () => 24 });
} catch(e) {}

// 10. window.outerWidth/height must match viewport (headless often shows 0)
try {
  if (!window.outerWidth)  Object.defineProperty(window, 'outerWidth',  { get: () => 1920 });
  if (!window.outerHeight) Object.defineProperty(window, 'outerHeight', { get: () => 1080 });
} catch(e) {}
""";

class SpaCrawler:
    def __init__(self, start_url: Optional[str] = None, start_urls: Optional[List[str]] = None, same_origin_only: bool = True, max_pages: int = 1000, concurrency: int = 5, timeout_ms: int = 20000, wait_until: str = "networkidle", user_agent: Optional[str] = None, headless: bool = True, extra_headers: Optional[Dict[str, str]] = None, scrape_content: bool = False, max_text_chars: int = 100_000, wait_selector: Optional[str] = None, wait_text_growth_ms: int = 0, include_html: bool = False, screenshot_dir: Optional[str] = None, log_network: bool = False, log_console: bool = False, discover_links: bool = True, retry_failed: bool = True, url_pattern: Optional[str] = None, stealth: bool = False):
        self.start_url = canonicalize(start_url) if start_url else None
        # Normalize and set starting URLs list (prefer start_urls; fall back to start_url)
        initial_urls = start_urls or ([start_url] if start_url else [])
        self.start_urls = [canonicalize(u) for u in initial_urls if u]

        self.same_origin_only = same_origin_only
        self.url_pattern = url_pattern
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
        self.stealth = stealth

        # Default to a realistic Chrome user agent when stealth is enabled and no explicit UA given
        if stealth and not self.user_agent:
            self.user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )

        # Base origin to compare for same_origin filter (use first start URL if present)
        self.origin_base_url = self.start_urls[0] if self.start_urls else self.start_url

        self.visited: Set[str] = set()
        self.queued: Set[str] = set()  # Track URLs already in queue to prevent duplicates
        self.results: List[VisitResult] = []
        self.failed_urls: List[Tuple[str, int, Optional[str]]] = []  # Track URLs that timed out or failed
        self.queue: asyncio.Queue[Tuple[str, int, Optional[str]]] = asyncio.Queue()

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
        ctx_kwargs: Dict = {
            "user_agent": self.user_agent,
            "extra_http_headers": self.extra_headers,
        }
        if self.stealth:
            ctx_kwargs["viewport"] = {"width": 1920, "height": 1080}
            ctx_kwargs["locale"] = "en-US"
            ctx_kwargs["timezone_id"] = "America/New_York"
            # Merge stealth-friendly headers with any caller-supplied ones
            stealth_headers = {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
            stealth_headers.update(self.extra_headers)  # caller headers take precedence
            ctx_kwargs["extra_http_headers"] = stealth_headers
        context = await browser.new_context(**ctx_kwargs)
        if self.stealth:
            await context.add_init_script(_STEALTH_INIT_SCRIPT)
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
            # Track the last navigation response so we can capture the real status
            # after a Cloudflare JS challenge redirects to the actual page.
            last_nav_status: List[Optional[int]] = [None]
            def _on_nav_response(resp):
                try:
                    if resp.request.resource_type == "document":
                        last_nav_status[0] = resp.status
                except Exception:
                    pass
            page.on("response", _on_nav_response)

            resp = await page.goto(url, timeout=self.timeout_ms, wait_until=self.wait_until)
            status = resp.status if resp else None

            # Detect Cloudflare / generic bot-protection challenge pages and wait
            # for the JS challenge to self-resolve before scraping content.
            _CF_TITLES = (
                "just a moment", "one more step", "checking your browser",
                "attention required", "performing security verification",
                "please wait", "ddos-guard",
            )
            try:
                current_title = (await page.title()).lower()
            except Exception:
                current_title = ""
            if any(t in current_title for t in _CF_TITLES):
                # Challenge page detected: wait until title changes to something real.
                # Use up remaining timeout minus a 3 s buffer.
                challenge_wait = max(self.timeout_ms - 3000, 8000)
                try:
                    await page.wait_for_function(
                        """() => {
                            const t = (document.title || '').toLowerCase();
                            return !(
                                t.includes('just a moment') ||
                                t.includes('one more step') ||
                                t.includes('checking your browser') ||
                                t.includes('attention required') ||
                                t.includes('performing security verification') ||
                                t.includes('please wait') ||
                                t.includes('ddos-guard')
                            );
                        }""",
                        timeout=challenge_wait,
                    )
                    # Challenge cleared — use the last captured navigation status
                    status = last_nav_status[0] or 200
                    print(f"[stealth] Cloudflare challenge cleared for {url}")
                except Exception:
                    print(f"[stealth] Cloudflare challenge did NOT clear within timeout for {url}")

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
                    # Filter by URL pattern if specified
                    if self.url_pattern and not link.startswith(self.url_pattern):
                        continue
                    # Prevent duplicate crawling: check both visited and queued sets
                    if link not in self.visited and link not in self.queued and len(self.visited) + self.queue.qsize() < self.max_pages:
                        # Mark as queued before adding to queue
                        self.queued.add(link)
                        # Propagate parent_url for the discovered link
                        await self.queue.put((link, depth + 1, url))
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
                # Preserve the parent_url by peeking at the current task's context if available is not trivial,
                # so we track None here; worker passes actual parent_url when available.
                # The worker handles re-queueing with preserved parent_url below.
                self.failed_urls.append((url, depth, None))
            await context.close()

    async def _worker(self, browser, pbar):
        while True:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                # Support both old and new tuple sizes for safety
                if isinstance(item, tuple) and len(item) == 3:
                    url, depth, parent_url = item
                else:
                    url, depth = item  # type: ignore
                    parent_url = None
            except asyncio.TimeoutError:
                return
            if url in self.visited or len(self.visited) >= self.max_pages:
                self.queue.task_done()
                continue
            # Mark as visited and remove from queued set
            self.visited.add(url)
            self.queued.discard(url)
            status, title, text, raw_html = await self._visit(browser, url, depth)
            self.results.append(VisitResult(url=url, status=status, depth=depth, parent_url=parent_url, title=title, text=text, raw_html=raw_html))
            pbar.update(1)
            self.queue.task_done()

    async def run(self):
        # Seed initial queue with provided URLs
        if self.start_urls:
            for u in self.start_urls:
                self.queued.add(u)
                await self.queue.put((u, 0, None))
        elif self.start_url:
            self.queued.add(self.start_url)
            await self.queue.put((self.start_url, 0, None))
        
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
                    for url, _, _ in self.failed_urls:
                        if url in self.visited:
                            self.visited.remove(url)
                        # Add back to queued set
                        self.queued.add(url)

                    # Re-queue failed URLs
                    for url, depth, parent_url in self.failed_urls:
                        await self.queue.put((url, depth, parent_url))

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
