import re

# What aria2 was built with, spelled the way aria2 spells it, against the hex
# length of each digest. The length is what catches a half-copied paste, which
# is the usual way one of these goes wrong.
ALGORITHMS = {
    "md5": 32,
    "sha-1": 40,
    "sha-224": 56,
    "sha-256": 64,
    "sha-384": 96,
    "sha-512": 128,
    "adler32": 8,
}

# Nobody types the hyphen. Every checksum published next to a download is
# "sha256" or "SHA256SUMS", so accept that and hand aria2 what it wants.
_ALIASES = {name.replace("-", ""): name for name in ALGORITHMS}

# aria2 reports a failed verification as exit status 32 and says nothing else
# at all, so without this the row reads "error" with no reason given.
MISMATCH_CODE = "32"
MISMATCH = "checksum did not match"

_HEX = re.compile(r"^[0-9a-f]+$")


class Invalid(ValueError):
    pass


def parse(value: str) -> tuple[str, str]:
    """An algorithm and digest from `sha256=abc…`.

    Checked here rather than left to aria2, which refuses a bad one with "We
    encountered a problem while processing the option '--checksum'" and no clue
    which part was wrong.
    """
    text = value.strip()
    if "=" not in text:
        raise Invalid(f"write it as <algorithm>=<digest>, e.g. sha256={'a' * 8}…")
    raw_algorithm, _, digest = text.partition("=")
    algorithm = raw_algorithm.strip().lower()
    algorithm = _ALIASES.get(algorithm.replace("-", ""), algorithm)
    if algorithm not in ALGORITHMS:
        raise Invalid(f"unknown algorithm {raw_algorithm.strip()!r} — try {', '.join(sorted(_ALIASES))}")
    digest = digest.strip().lower()
    if not _HEX.match(digest):
        raise Invalid("a digest is hexadecimal")
    wanted = ALGORITHMS[algorithm]
    if len(digest) != wanted:
        raise Invalid(f"{algorithm} is {wanted} characters, this is {len(digest)}")
    return algorithm, digest


def normalise(value: str) -> str:
    """The option string aria2 takes."""
    algorithm, digest = parse(value)
    return f"{algorithm}={digest}"


def mismatched(status: dict) -> bool:
    return str(status.get("errorCode", "")) == MISMATCH_CODE


def explain(status: dict) -> str:
    """Why this download failed, in words, when aria2 offers none."""
    said = (status.get("errorMessage") or "").strip()
    if said:
        return said
    return MISMATCH if mismatched(status) else ""
