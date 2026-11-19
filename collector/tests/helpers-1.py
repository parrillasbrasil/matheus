"""Auxiliares de teste (TESTE: nada aqui simula pesquisa ao vivo)."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(rel: str) -> str:
    return (FIXTURES / rel).read_text(encoding="utf-8")


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_text_pdf(text: str) -> bytes:
    """PDF mínimo válido com uma linha de texto (para testar extração sem dependências extras)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_blank_pdf() -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def zip_json(records: list | dict, name: str = "data.json") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, json.dumps(records))
    return buf.getvalue()


def routes_transport(routes: dict[str, httpx.Response | callable], seen: list[str] | None = None) -> httpx.MockTransport:
    """Mapeia URL (sem query) -> resposta. Robots.txt ausente responde 404 por padrão."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(url)
        for key in (url, url.split("?")[0]):
            if key in routes:
                r = routes[key]
                return r(request) if callable(r) else r
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)
