"""Conectores de páginas HTML, PDFs e 'hubs' (páginas índice que apontam para editais/bandos)."""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

from ..http import BlockedByRobots, FetchError, PoliteClient, RequestBudgetExceeded
from ..official import is_official_url
from ..textutil import normalize_text
from .base import Connector, RawItem

_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid")
_DROP = ["script", "style", "noscript", "svg", "form", "iframe", "nav", "footer", "aside"]
_BLOCK = ["p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "article", "blockquote"]


def clean_url(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ""))


def html_to_text(html: str) -> tuple[str | None, str, str | None]:
    """Devolve (título, texto principal normalizado, href canônico)."""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(strip=True) if soup.title else None) or None
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else None
    link = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
    canonical = link.get("href") if link else None
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article") or soup.body or soup
    for tag in main.find_all(_DROP):
        tag.decompose()
    for el in main.find_all(_BLOCK):
        el.insert_before("\n\n")
        el.insert_after("\n\n")
    for br in main.find_all("br"):
        br.replace_with("\n")
    return title, normalize_text(main.get_text()), canonical


def pdf_to_text(content: bytes) -> tuple[str | None, str]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise FetchError("PDF protegido por senha")
        pages = [(p.extract_text() or "") for p in reader.pages[:80]]
        title = (reader.metadata.title if reader.metadata else None) or None
    except (PyPdfError, ValueError, KeyError) as exc:
        raise FetchError(f"PDF ilegível: {exc.__class__.__name__}") from exc
    return title, normalize_text("\n\n".join(pages))


def _is_pdf(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "").lower()
    return "application/pdf" in ctype or urlparse(str(resp.url)).path.lower().endswith(".pdf") or resp.content[:5] == b"%PDF-"


def item_from_response(resp: httpx.Response) -> RawItem:
    final = str(resp.url)
    if _is_pdf(resp):
        title, text = pdf_to_text(resp.content)
        return RawItem(url=clean_url(final), title=title, text=text, content_type="pdf", needs_ocr=not text.strip())
    title, text, canonical = html_to_text(resp.text)
    url = clean_url(final)
    if canonical:
        canon = urljoin(final, canonical)
        if urlparse(canon).hostname == urlparse(final).hostname:
            url = clean_url(canon)
    return RawItem(url=url, title=title, text=text, content_type="html")


class WebPageConnector(Connector):
    """html_page e pdf_document: uma URL, um documento."""

    def fetch(self, client: PoliteClient, now: datetime, stats: dict) -> Iterator[RawItem]:
        yield item_from_response(client.get(self.source.url))


class HubOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    follow_patterns: list[str] = []
    max_links: int = 20


class HubConnector(Connector):
    options_model = HubOptions

    def fetch(self, client: PoliteClient, now: datetime, stats: dict) -> Iterator[RawItem]:
        resp = client.get(self.source.url)
        hub_item = item_from_response(resp)
        yield hub_item

        patterns = [re.compile(p) for p in self.options.follow_patterns]
        soup = BeautifulSoup(resp.text if not _is_pdf(resp) else "", "lxml")
        seen = {clean_url(self.source.url), hub_item.url}
        children: list[str] = []
        for a in soup.find_all("a", href=True):
            target = clean_url(urljoin(str(resp.url), a["href"]))
            if target in seen or not target.startswith(("http://", "https://")):
                continue
            label = a.get_text(" ", strip=True)
            if not any(p.search(a["href"]) or p.search(label) for p in patterns):
                continue
            if not is_official_url(self.cfg, target):
                stats["skipped_unofficial_links"] = stats.get("skipped_unofficial_links", 0) + 1
                continue
            seen.add(target)
            children.append(target)
            if len(children) >= self.options.max_links:
                break

        stats.setdefault("blocked_by_robots", 0)
        stats.setdefault("child_errors", 0)
        for child in children:
            try:
                yield item_from_response(client.get(child))
            except BlockedByRobots:
                stats["blocked_by_robots"] += 1
            except RequestBudgetExceeded:
                stats["budget_exhausted"] = True
                break
            except FetchError:
                stats["child_errors"] += 1
