"""NCBI E-utilities for steps 02 and 03: credentials, a rate-limited client, and the PubMed query text both use,
so what 02 counts is exactly what 03 downloads.

Credentials (NCBI_EMAIL, NCBI_API_KEY) come from the environment, else the --env-file given, else the nearest
.env.local above lacuna, else ~/.ncbi.env. They never go in code.

Settings (the [download.pubmed] table):
    max_fulltext        open-access papers per topic, best match first; these get PMC full text (0 = abstracts only)
    max_abstract_only   further papers per topic that aren't open access; abstracts only
    language            adds AND <language>[lang] ("" to drop it)
    require_abstract    adds AND hasabstract
    years               [first, last] adds a publication-date range
    require / exclude   one more clause ANDed onto / NOTed off every query
"""
import json
import os
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TOOL = "lacuna"
CAP = 9_999  # esearch won't page past its 10,000th result
DEFAULTS = {"max_fulltext": 5000, "max_abstract_only": 300, "language": "english", "require_abstract": True,
            "years": None, "require": None, "exclude": None}
LACUNA = Path(__file__).resolve().parents[2]


def credentials(env_file: Path | None = None) -> tuple[str | None, str | None]:
    if env_file and not env_file.is_file():
        raise FileNotFoundError(f"--env-file {env_file} does not exist")
    found = [env_file] if env_file else [p / ".env.local" for p in LACUNA.parents] + [Path.home() / ".ncbi.env"]
    for path in (p for p in found if p.is_file()):
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().startswith("NCBI_"):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        break
    return os.environ.get("NCBI_EMAIL") or None, os.environ.get("NCBI_API_KEY") or None


def filters(settings: dict) -> str:
    """The clauses every topic's query gets. With the defaults: AND english[lang] AND hasabstract."""
    s = {**DEFAULTS, **settings}
    parts = [f"{s['language']}[lang]"] if s["language"] else []
    parts += ["hasabstract"] if s["require_abstract"] else []
    parts += [f'("{s["years"][0]}"[dp] : "{s["years"][1]}"[dp])'] if s["years"] else []
    parts += [f"({s['require']})"] if s["require"] else []
    text = " ".join(f"AND {p}" for p in parts)
    return (text + (f" NOT ({s['exclude']})" if s["exclude"] else "")).strip()


def everything(query: str, settings: dict) -> str:
    return f"({query}) {filters(settings)}"


def open_access(query: str, settings: dict) -> str:
    return f'{everything(query, settings)} AND "pubmed pmc open access"[filter]'


def notes(result: dict) -> list[str]:
    """What PubMed quietly dropped from a query ("quotedphrasesnotfound: ..."); each one changes what it matches."""
    return [f"{kind}: {v}" for kind, values in {**result.get("errorlist", {}), **result.get("warninglist", {})}.items()
            for v in values or [] if v]


class Pace:
    """At most `rate` request starts per second, across threads."""

    def __init__(self, rate: float):
        self.gap, self.next, self.lock = 1 / rate, 0.0, threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next)
            self.next = start + self.gap
        time.sleep(start - now)


class NCBI:
    def __init__(self, env_file: Path | None = None):
        email, key = credentials(env_file)
        self.auth = {"tool": TOOL, **({"email": email} if email else {}), **({"api_key": key} if key else {})}
        # NCBI allows 10 requests a second with a key, 3 without; efetch answers slowly, so several run at once.
        self.pace = Pace(9 if key else 2.8)
        self.workers = 6 if key else 2

    def call(self, endpoint: str, params: dict) -> bytes:
        # POST, because a batch of 200 IDs or a long query doesn't fit in a URL.
        data = urlencode({**params, **self.auth}).encode()
        for attempt in range(4):
            self.pace.wait()
            try:
                with urlopen(Request(EUTILS + endpoint, data=data, headers={"User-Agent": TOOL}), timeout=300) as r:
                    return r.read()
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == 3:
                    raise
            time.sleep(5 * 2 ** attempt)
        raise AssertionError("unreachable")

    def esearch(self, term: str, retstart: int = 0, retmax: int = 0) -> dict:
        for attempt in range(4):
            result = json.loads(self.call("esearch.fcgi", {"db": "pubmed", "term": term, "sort": "relevance",
                                                           "retmode": "json", "retstart": retstart, "retmax": retmax}))
            result = result["esearchresult"]
            # A busy PubMed answers 200 with "Search Backend failed" inside; that's worth asking again.
            if "ERROR" not in result:
                return result
            if "backend failed" not in result["ERROR"].lower() or attempt == 3:
                raise RuntimeError(f"PubMed rejected the query {term!r}: {result['ERROR']}")
            time.sleep(5 * 2 ** attempt)
        raise AssertionError("unreachable")

    def search(self, term: str, limit: int) -> list[str]:
        """PMIDs for a query, best match first."""
        ids: list[str] = []
        limit = min(limit, CAP)
        while len(ids) < limit:
            result = self.esearch(term, retstart=len(ids), retmax=limit - len(ids))
            ids += result["idlist"]
            if not result["idlist"] or len(ids) >= int(result["count"]):
                break
        return ids[:limit]
