"""HTTP utilities for handling compressed responses."""
from __future__ import annotations

import io
import logging
import gzip
import zlib
from typing import Iterable

import aiohttp
import brotli
import zstandard as zstd


LOGGER = logging.getLogger(__name__)


async def read_response_text(response: aiohttp.ClientResponse) -> str:
    """Read an HTTP response and decode its payload respecting encodings."""

    raw = await response.read()
    encodings = [
        encoding.strip().lower()
        for encoding in response.headers.get("Content-Encoding", "").split(",")
        if encoding.strip()
    ]

    data = _decompress_chain(raw, encodings)
    charset = response.charset or "utf-8"
    return data.decode(charset, errors="replace")


def _decompress_chain(data: bytes, encodings: Iterable[str]) -> bytes:
    decompressed = data
    for encoding in encodings:
        try:
            decompressed = _decompress_bytes(decompressed, encoding)
        except Exception:  # pragma: no cover - defensive logging for unexpected encodings
            LOGGER.warning(
                "Failed to decompress %s encoded response", encoding, exc_info=True
            )
            break
    return decompressed


def _decompress_bytes(data: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(data)
    if encoding == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    if encoding == "br":
        return brotli.decompress(data)
    if encoding in {"zstd", "zstandard"}:
        decompressor = zstd.ZstdDecompressor()
        with decompressor.stream_reader(io.BytesIO(data)) as reader:
            return reader.read()
    if encoding == "identity":
        return data
    raise ValueError(f"Unsupported content encoding: {encoding}")

