"""Conector de FIXTURES: só roda com RADAR_ENV=test; nunca representa pesquisa ao vivo."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, ConfigDict

from ..http import PoliteClient
from .base import Connector, RawItem
from .web import html_to_text


class FixtureOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    content_type: Literal["html"] = "html"


def repo_root() -> Path:
    return Path(os.environ.get("RADAR_REPO_ROOT") or Path(__file__).resolve().parents[3])


class FixtureConnector(Connector):
    options_model = FixtureOptions

    def fetch(self, client: PoliteClient, now: datetime, stats: dict) -> Iterator[RawItem]:
        if os.environ.get("RADAR_ENV") != "test":
            raise RuntimeError("fontes de fixture só podem rodar com RADAR_ENV=test")
        path = repo_root() / self.options.file
        title, text, _ = html_to_text(path.read_text(encoding="utf-8"))
        yield RawItem(url=self.source.url, title=title, text=text, content_type="html",
                      metadata={"fixture": True, "file": self.options.file})
