import os

from astrbot.api.all import Image


def _normalize_file_path(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/")


def normalize_image_ref(image: str) -> str:
    if not isinstance(image, str):
        return str(image)
    if image.startswith("file:///"):
        path = image[8:]
        if not path:
            return image
        return _normalize_file_path(path)
    if image.startswith(("http://", "https://", "base64://")):
        return image
    if os.path.exists(image):
        return _normalize_file_path(image)
    return image


def build_image_aliases(image: str) -> set[str]:
    aliases: set[str] = set()
    if not isinstance(image, str) or not image:
        return aliases

    aliases.add(image)

    if image.startswith("file:///"):
        path = image[8:]
        if path:
            normalized = _normalize_file_path(path)
            aliases.add(path)
            aliases.add(normalized)
            aliases.add(f"file:///{normalized}")
        return aliases

    if image.startswith(("http://", "https://", "base64://")):
        return aliases

    normalized = normalize_image_ref(image)
    aliases.add(normalized)
    aliases.add(f"file:///{normalized}")
    return aliases


def extract_image_src(component: Image) -> str | None:
    for attr in ("file", "url", "path"):
        value = getattr(component, attr, None)
        if not value:
            continue

        if not isinstance(value, str):
            return value

        if value.startswith("base64://"):
            if len(value) > len("base64://"):
                return value
            continue

        if value.startswith(("http://", "https://")):
            return value

        if value.startswith("file:///"):
            file_path = value[8:]
            if not os.path.exists(file_path) or os.path.getsize(file_path) <= 0:
                continue
            return value

        if os.path.exists(value) and os.path.getsize(value) > 0:
            return value

    return None

