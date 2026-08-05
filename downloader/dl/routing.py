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


def through_proxy(url: str, cfg: Config, forced: bool = False) -> bool:
    """Whether this download goes through the proxy.

    A rule that lives with the URL rather than in argv is the only kind -p
    cannot lose: retries, the dashboard's add box and the clipboard watcher all
    reach the same answer the command line did.

    A bare name here covers subdomains, unlike [domains]. Routing a file picks
    one host; reaching a blocked service means reaching every hostname it
    answers on, and listing them by hand is how one gets forgotten.
    """
    if forced:
        return True
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    return any(
        host.endswith(rule[1:]) if rule.startswith("*.") else _covers(rule, host)
        for rule in (r.lower() for r in cfg.proxy_domains)
    )


def _covers(rule: str, host: str) -> bool:
    return host == rule or host.endswith(f".{rule}")


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
