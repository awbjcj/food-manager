from __future__ import annotations

from io import BytesIO
from urllib.parse import urlsplit

import httpx
from PIL import Image

from app.messaging.contracts import DownloadedImage, ErrorCode, MessagingError

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def checked_image(data: bytes) -> DownloadedImage:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise MessagingError(
            ErrorCode.INVALID_INPUT, "Receipt image exceeds the size limit"
        )
    if data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise MessagingError(
            ErrorCode.INVALID_INPUT, "Use a JPEG, PNG or WebP receipt image"
        )
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as picture:
            if (
                picture.width * picture.height > 25_000_000
                or getattr(picture, "n_frames", 1) != 1
            ):
                raise ValueError("Image dimensions or frame count exceeded")
            picture.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise MessagingError(
            ErrorCode.INVALID_INPUT, "Receipt image could not be decoded safely"
        ) from None
    return DownloadedImage(data=data, mime_type=mime)


async def download(
    http: httpx.AsyncClient, url: str, *, token: str, hosts: tuple[str, ...]
) -> DownloadedImage:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in hosts
        or parts.username
        or parts.password
        or parts.port not in {None, 443}
    ):
        raise MessagingError(
            ErrorCode.INVALID_INPUT, "Provider returned an unsupported media location"
        )
    try:
        async with http.stream(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise MessagingError(
                    ErrorCode.NOT_FOUND, "Receipt image is no longer available"
                )
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise MessagingError(
                        ErrorCode.INVALID_INPUT, "Receipt image exceeds the size limit"
                    )
        return checked_image(bytes(data))
    except httpx.HTTPError:
        raise MessagingError(
            ErrorCode.TEMPORARY_FAILURE, "Media download failed"
        ) from None


async def json_request(
    http: httpx.AsyncClient, method: str, url: str, *, sends: bool = False, **kwargs
) -> dict:
    try:
        response = await http.request(method, url, follow_redirects=False, **kwargs)
    except httpx.HTTPError:
        raise MessagingError(
            ErrorCode.DELIVERY_UNKNOWN if sends else ErrorCode.TEMPORARY_FAILURE,
            "Provider request could not be confirmed",
        ) from None
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "60")
        raise MessagingError(
            ErrorCode.RATE_LIMITED,
            "Provider rate limit",
            retry_after_seconds=int(retry) if retry.isdigit() else 60,
        )
    if response.status_code in {401, 403}:
        raise MessagingError(
            ErrorCode.AUTH_FAILED, "Provider credentials were rejected"
        )
    if response.status_code >= 500:
        raise MessagingError(
            ErrorCode.DELIVERY_UNKNOWN if sends else ErrorCode.TEMPORARY_FAILURE,
            "Provider is unavailable",
        )
    if not 200 <= response.status_code < 300:
        raise MessagingError(ErrorCode.INVALID_INPUT, "Provider rejected the request")
    try:
        result = response.json()
    except ValueError:
        result = None
    if not isinstance(result, dict):
        raise MessagingError(
            ErrorCode.DELIVERY_UNKNOWN if sends else ErrorCode.TEMPORARY_FAILURE,
            "Provider returned an invalid response",
        )
    return result
