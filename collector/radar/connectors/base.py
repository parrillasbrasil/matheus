"""Contrato dos conectores: cada fonte vira RawItems com texto normalizado (ou dados estruturados)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import Config, SourceCfg
from ..http import PoliteClient
from ..schema import Extraction


class ConnectorOptionsError(Exception):
    pass


class NoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class RawItem:
    url: str                                  # URL canônica
    title: str | None
    text: str                                 # texto normalizado ('' se precisa de OCR)
    content_type: str                         # html | pdf | json
    external_id: str | None = None
    published_at: datetime | None = None
    needs_ocr: bool = False
    structured: Extraction | None = None      # extração determinística (feeds estruturados): dispensa LLM
    metadata: dict = field(default_factory=dict)


def parse_options(model: type[BaseModel], options: dict, source_id: str) -> BaseModel:
    try:
        return model.model_validate(options)
    except ValidationError as exc:
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConnectorOptionsError(f"opções inválidas na fonte {source_id}: {detail}") from exc


class Connector:
    options_model: type[BaseModel] = NoOptions

    def __init__(self, source: SourceCfg, cfg: Config) -> None:
        self.source, self.cfg = source, cfg
        self.options = parse_options(self.options_model, source.options, source.id)

    def fetch(self, client: PoliteClient, now: datetime, stats: dict) -> Iterator[RawItem]:  # pragma: no cover
        raise NotImplementedError
