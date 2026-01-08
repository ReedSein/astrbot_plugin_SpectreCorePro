from __future__ import annotations

import asyncio
import os
import ssl
from typing import Optional
from urllib.parse import urlsplit

import aiohttp
import certifi

from astrbot import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.io import save_temp_img


_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _build_headers(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    referer = ""
    if parts.scheme and parts.netloc:
        referer = f"{parts.scheme}://{parts.netloc}/"
    headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _looks_like_html(data: bytes) -> bool:
    head = data[:64].lstrip().lower()
    return head.startswith(b"<html") or head.startswith(b"<!doctype html")


async def download_image_by_url_safe(
    url: str,
    *,
    timeout: float = 15.0,
    retries: int = 1,
) -> Optional[str]:
    """下载图片并返回本地临时文件路径。

    相比 AstrBot 内置 download_image_by_url：
    - 增加 UA/Referer，提升部分 CDN 可用性
    - 校验 HTTP 状态码与空响应，避免产生 0 字节文件
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    try:
        os.makedirs(get_astrbot_temp_path(), exist_ok=True)
    except Exception:
        pass

    headers = _build_headers(url)
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(
                trust_env=True,
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=headers,
            ) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.read()
                    if not data:
                        raise RuntimeError("empty body")
                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if (
                        content_type.startswith("text/")
                        or content_type.startswith("application/json")
                        or _looks_like_html(data)
                    ):
                        raise RuntimeError(f"non-image response ({content_type})")
                    path = save_temp_img(data)
                    if (
                        not path
                        or not os.path.exists(path)
                        or os.path.getsize(path) <= 0
                    ):
                        raise RuntimeError("saved file empty")
                    return os.path.abspath(path)
        except (
            aiohttp.ClientConnectorSSLError,
            aiohttp.ClientConnectorCertificateError,
        ) as e:
            last_exc = e
            try:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                async with aiohttp.ClientSession(
                    trust_env=True,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers=headers,
                ) as session:
                    async with session.get(
                        url,
                        allow_redirects=True,
                        ssl=ssl_context,
                    ) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        data = await resp.read()
                        if not data:
                            raise RuntimeError("empty body")
                        content_type = (
                            resp.headers.get("Content-Type") or ""
                        ).lower()
                        if (
                            content_type.startswith("text/")
                            or content_type.startswith("application/json")
                            or _looks_like_html(data)
                        ):
                            raise RuntimeError(f"non-image response ({content_type})")
                        path = save_temp_img(data)
                        if (
                            not path
                            or not os.path.exists(path)
                            or os.path.getsize(path) <= 0
                        ):
                            raise RuntimeError("saved file empty")
                        return os.path.abspath(path)
            except Exception as e2:
                last_exc = e2
        except Exception as e:
            last_exc = e

        if attempt < retries:
            await asyncio.sleep(0.3 * (attempt + 1))

    logger.debug(f"[SpectreCore] 图片下载失败: {url} ({last_exc})")
    return None
