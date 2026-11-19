from __future__ import annotations

from ..config import Config, SourceCfg
from .base import Connector, ConnectorOptionsError, RawItem
from .fixture import FixtureConnector
from .seasonal_jobs import SeasonalJobsConnector
from .web import HubConnector, WebPageConnector

_REGISTRY: dict[str, type[Connector]] = {
    "html_page": WebPageConnector,
    "pdf_document": WebPageConnector,
    "html_hub": HubConnector,
    "seasonal_jobs_json": SeasonalJobsConnector,
    "fixture": FixtureConnector,
}


def build_connector(source: SourceCfg, cfg: Config) -> Connector:
    return _REGISTRY[source.kind](source, cfg)


__all__ = ["Connector", "ConnectorOptionsError", "RawItem", "build_connector"]
