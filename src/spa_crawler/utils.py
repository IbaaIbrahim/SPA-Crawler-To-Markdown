from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode

def canonicalize(url: str) -> str:
    """
    Canonicalize a URL to prevent duplicate crawling:
    - Convert to lowercase (scheme and domain)
    - Remove fragments (#)
    - Normalize trailing slashes
    - Sort query parameters
    - Remove default ports
    """
    if not url:
        return ""
    p = urlparse(url)
    scheme = p.scheme.lower()
    netloc = p.hostname.lower() if p.hostname else ""
    if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)):
        netloc = f"{netloc}:{p.port}"
    path = p.path or "/"
    # Normalize trailing slashes: keep trailing slash only for root path
    if len(path) > 1 and path.endswith('/'):
        path = path.rstrip('/')
    query_pairs = sorted(parse_qsl(p.query, keep_blank_values=True))
    query = urlencode(query_pairs, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))

def same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlparse(a), urlparse(b)
        # Handle cases where hostname might be None (invalid URLs)
        if not pa.hostname or not pb.hostname:
            return False
        return (pa.scheme.lower(), pa.hostname.lower(), pa.port or 80) == (pb.scheme.lower(), pb.hostname.lower(), pb.port or 80)
    except Exception:
        return False

def absolutize(base_url: str, href: str) -> str:
    try:
        return urljoin(base_url, href)
    except Exception:
        return ""
