import httpx
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

STRIP_TAGS = [
    "script", "style", "noscript", "iframe", "svg",
    "nav", "footer", "header", "aside",
    "form", "input", "button", "select", "textarea",
]


async def fetch_and_clean(url: str, timeout: float = 30.0) -> tuple[str, str]:
    """Fetch a URL and return (cleaned_html, page_title)."""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=_HEADERS,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    # Remove non-content elements
    for tag_name in STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove comments
    from bs4 import Comment
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Remove hidden elements
    for tag in soup.find_all(attrs={"style": lambda v: v and "display:none" in v.replace(" ", "")}):
        tag.decompose()
    for tag in soup.find_all(attrs={"hidden": True}):
        tag.decompose()

    # Get the main content area if it exists, otherwise use body
    main = soup.find("main") or soup.find("article") or soup.find("body") or soup
    cleaned = str(main)

    return cleaned, title
