
import pytest
import gzip
import zlib
import brotli
import zstandard as zstd
from unittest.mock import AsyncMock, MagicMock
from gw2_tools_bot import http_utils

@pytest.mark.asyncio
async def test_read_response_text_gzip():
    data = b"hello world"
    compressed = gzip.compress(data)
    
    response = AsyncMock()
    response.read.return_value = compressed
    response.headers = {"Content-Encoding": "gzip"}
    response.charset = "utf-8"
    
    text = await http_utils.read_response_text(response)
    assert text == "hello world"

@pytest.mark.asyncio
async def test_read_response_text_deflate():
    data = b"hello world"
    compressed = zlib.compress(data)
    
    response = AsyncMock()
    response.read.return_value = compressed
    response.headers = {"Content-Encoding": "deflate"}
    response.charset = "utf-8"
    
    text = await http_utils.read_response_text(response)
    assert text == "hello world"

@pytest.mark.asyncio
async def test_read_response_text_brotli():
    data = b"hello world"
    compressed = brotli.compress(data)
    
    response = AsyncMock()
    response.read.return_value = compressed
    response.headers = {"Content-Encoding": "br"}
    response.charset = "utf-8"
    
    text = await http_utils.read_response_text(response)
    assert text == "hello world"

@pytest.mark.asyncio
async def test_read_response_text_zstd():
    data = b"hello world"
    cctx = zstd.ZstdCompressor()
    compressed = cctx.compress(data)
    
    response = AsyncMock()
    response.read.return_value = compressed
    response.headers = {"Content-Encoding": "zstd"}
    response.charset = "utf-8"
    
    text = await http_utils.read_response_text(response)
    assert text == "hello world"

@pytest.mark.asyncio
async def test_read_response_text_identity():
    data = b"hello world"
    
    response = AsyncMock()
    response.read.return_value = data
    response.headers = {"Content-Encoding": "identity"}
    response.charset = "utf-8"
    
    text = await http_utils.read_response_text(response)
    assert text == "hello world"
