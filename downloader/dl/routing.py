from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import Category, Config

OTHER = Category(name="other", dir=Path("."), ext=(), icon="📥", hue="#8a8a8a")


@dataclass(frozen=True)
class Resolution:
    path: Path
    category: Category


def filename_from_url(url: str) -> str:
    path = urlsplit(url).path
    if not path:
        return ""
    return unquote(path.rsplit("/", 1)[-1])


def _extension(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _by_domain(url: str, cfg: Config) -> Category | None:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    name = cfg.domains.get(host)
    if name is None:
        for pattern, target in cfg.domains.items():
            if pattern.startswith("*.") and host.endswith(pattern[1:]):
                name = target
                break
    return cfg.categories.get(name) if name else None


def _by_extension(filename: str, cfg: Config) -> Category | None:
    ext = _extension(filename)
    if not ext:
        return None
    for category in cfg.categories.values():
        if ext in category.ext:
            return category
    return None


def resolve(
    url: str, filename: str, cfg: Config, explicit_dir: Path | None = None
) -> Resolution:
    if explicit_dir is not None:
        return Resolution(Path(explicit_dir).expanduser(), OTHER)
    name = filename or filename_from_url(url)
    category = _by_domain(url, cfg) or _by_extension(name, cfg)
    if category is None:
        return Resolution(cfg.general.default_dir, OTHER)
    return Resolution(category.dir, category)
