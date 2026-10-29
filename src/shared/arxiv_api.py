"""The arXiv API for steps 03 and 04: the search query a topic becomes, and a client that keeps to arXiv's limit of
one request every 3 seconds, so what 03 counts is exactly what 04 downloads.

Settings (the [download.arxiv] table):
    max_papers   papers per topic, best match first
    categories   arXiv categories a paper must be in, e.g. ["cs.*", "eess.IV", "q-bio.*"] (empty = any)
    fulltext     also download each paper's HTML full text, where arXiv has one (papers since late 2023 mostly)
"""
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from lxml import etree

API = "https://export.arxiv.org/api/query"
HTML = "https://arxiv.org/html/"
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
      "os": "http://a9.com/-/spec/opensearch/1.1/"}
HEADERS = {"User-Agent": "lacuna (Indiana University research)"}
GAP = 3.0
PAGE = 200


def query(terms: str, settings: dict) -> str:
    """A topic's search terms with the categories filter on."""
    cats = " OR ".join(f"cat:{c}" for c in settings["categories"])
    return f"({terms}) AND ({cats})" if cats else terms


def arxiv_id(entry) -> tuple[str, str]:
    """(id, version) from an entry: http://arxiv.org/abs/2410.18710v3 -> ("2410.18710", "v3")."""
    full = entry.findtext("a:id", namespaces=NS).split("/abs/", 1)[1]
    base, _, version = full.rpartition("v")
    return (base, f"v{version}") if version.isdigit() else (full, "")


class Client:
    def __init__(self):
        self.next = 0.0

    def get(self, url: str) -> bytes | None:
        """The response, or None for a 404; retried on server errors."""
        for attempt in range(4):
            time.sleep(max(0.0, self.next - time.monotonic()))
            self.next = time.monotonic() + GAP
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as r:
                    return r.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code not in (429, 500, 502, 503) or attempt == 3:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
            time.sleep(5 * 2 ** attempt)
        raise AssertionError("unreachable")

    def feed(self, params: dict):
        return etree.fromstring(self.get(f"{API}?{urlencode(params)}"))

    def count(self, search: str) -> int:
        # max_results=0 makes the API report 1 match for any query, so one entry is asked for
        return int(self.feed({"search_query": search, "max_results": 1}).findtext("os:totalResults", namespaces=NS))

    def search(self, search: str, limit: int) -> list:
        """Entries for a query, best match first."""
        entries = []
        while len(entries) < limit:
            feed = self.feed({"search_query": search, "start": len(entries), "sortBy": "relevance",
                              "max_results": min(PAGE, limit - len(entries))})
            page = feed.findall("a:entry", NS)
            entries += page
            if not page or len(entries) >= int(feed.findtext("os:totalResults", namespaces=NS)):
                break
        return entries[:limit]
