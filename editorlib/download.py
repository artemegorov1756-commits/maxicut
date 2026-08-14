"""Input handling: local path or URL.

Ported near-verbatim from the MoviePy version. None of this touched MoviePy in
the first place - it is plain urllib - so the security properties (scheme
allow-list, no-downgrade redirects, sniffed-not-trusted suffix, size cap,
private 0600 temp file) carry over unchanged. ffmpeg's own libavformat can
open http(s) URLs directly, but doing that would hand ffmpeg the redirect and
scheme handling instead of us, which is exactly the control this module
exists to keep.
"""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class TitleMakerError(RuntimeError):
    """Any failure we can explain to the user without a traceback."""


#: Only these URL schemes are ever fetched. Anything else (file://, ftp://,
#: gopher://, data:) is rejected before a request is made.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Hard ceiling on a downloaded file, so a hostile or mistyped URL cannot fill
#: the disk. Override with --max-download-mb.
DEFAULT_MAX_DOWNLOAD_MB = 2048

#: Socket timeout (seconds) applied to the download.
DOWNLOAD_TIMEOUT = 30

#: Streaming chunk size for the download.
CHUNK_SIZE = 1 << 20  # 1 MiB

#: Suffixes we are willing to copy from a URL onto our temp file. The suffix is
#: cosmetic (FFmpeg sniffs the real container), so an unknown one becomes .mp4
#: rather than being trusted.
SAFE_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".ts"}
)


def is_url(source: str) -> bool:
    """True if `source` is an http(s) URL.

    Deliberately checks against a scheme allow-list rather than "does it contain
    '://'", so a Windows path like ``C:\\clips\\a.mp4`` (which urlparse reads as
    scheme "c") is still treated as a local file.
    """
    return urllib.parse.urlparse(source).scheme.lower() in ALLOWED_SCHEMES


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that leave http/https.

    Without this, a 302 to ``file:///etc/passwd`` would be followed by urllib's
    default handler and happily copied to our temp file.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        scheme = urllib.parse.urlparse(newurl).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise TitleMakerError(
                f"Refusing redirect to non-http(s) URL: {newurl!r}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _suffix_for(url: str, content_type: str | None) -> str:
    """Pick a temp-file suffix without trusting attacker-controlled names.

    Only the extension is considered, and only if it is on the allow-list, so a
    URL path of ``../../.bashrc`` or ``x.mp4.sh`` cannot influence the file we
    create.
    """
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in SAFE_VIDEO_SUFFIXES:
        return suffix
    if content_type:
        subtype = content_type.split(";")[0].strip().lower().removeprefix("video/")
        candidate = f".{subtype}"
        if candidate in SAFE_VIDEO_SUFFIXES:
            return candidate
    return ".mp4"


def download_video(url: str, max_bytes: int) -> Path:
    """Stream `url` to a newly created private temp file and return its path.

    The caller owns the file and is responsible for deleting it.
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise TitleMakerError(
            f"Unsupported URL scheme {scheme!r}; only http and https are allowed."
        )

    opener = urllib.request.build_opener(_StrictRedirectHandler())
    request = urllib.request.Request(
        url, headers={"User-Agent": "titel-maker-ffmpeg/1.0"}
    )

    print(f"[1/5] Downloading {url}")
    try:
        response = opener.open(request, timeout=DOWNLOAD_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise TitleMakerError(f"Download failed: HTTP {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TitleMakerError(f"Download failed: {exc}") from exc

    with response:
        content_type = response.headers.get("Content-Type", "")
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise TitleMakerError(
                f"Remote file is {int(declared) / 1e6:.0f} MB, over the "
                f"{max_bytes / 1e6:.0f} MB limit (raise it with --max-download-mb)."
            )
        if content_type and not content_type.startswith(("video/", "application/")):
            print(f"      note: server reports Content-Type {content_type!r}")

        # mkstemp creates the file atomically with 0600 permissions, so another
        # local user cannot pre-create or swap it mid-download.
        fd, tmp_name = tempfile.mkstemp(
            prefix="titelmaker_", suffix=_suffix_for(url, content_type)
        )
        tmp_path = Path(tmp_name)
        downloaded = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                while chunk := response.read(CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise TitleMakerError(
                            f"Download exceeded the {max_bytes / 1e6:.0f} MB limit "
                            "(raise it with --max-download-mb)."
                        )
                    handle.write(chunk)
                    print(f"\r      {downloaded / 1e6:8.1f} MB", end="", flush=True)
            print()
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    if downloaded == 0:
        tmp_path.unlink(missing_ok=True)
        raise TitleMakerError("Download produced an empty file.")
    return tmp_path


#: Windows exposes each WSL distro's filesystem under one of these UNC roots.
#: wsl.localhost is current; wsl$ is the older name kept working for back
#: compat. Both are tried, in this order, since either can be the one a given
#: Windows build actually shares.
WSL_UNC_ROOTS = (r"\\wsl.localhost", r"\\wsl$")


def _default_wsl_distro() -> str | None:
    """Name of the WSL distro `wsl -l -v` marks as default, or None.

    wsl.exe prints UTF-16LE regardless of the console's own encoding, so this
    decodes explicitly rather than trusting subprocess's text-mode guess.
    """
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-v"], capture_output=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.decode("utf-16-le", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("*"):
            parts = line[1:].split()
            if parts:
                return parts[0]
    return None


def wsl_path_to_windows(source: str, distro: str | None = None) -> Path | None:
    """Translate a POSIX-style WSL path (``/home/user/clip.mp4``) to the
    Windows UNC path WSL shares it under, or None if `source` doesn't look
    like one, WSL isn't reachable, or no file exists there.

    Only fires on Windows, and only for paths starting with ``/`` - a
    Windows-native path (``C:\\...`` or already ``\\\\wsl.localhost\\...``)
    is left to the plain `Path(source).is_file()` check the caller already
    does.
    """
    if platform.system() != "Windows" or not source.startswith("/"):
        return None
    resolved_distro = distro or _default_wsl_distro()
    if not resolved_distro:
        return None
    tail = source.lstrip("/").replace("/", "\\")
    for root in WSL_UNC_ROOTS:
        candidate = Path(f"{root}\\{resolved_distro}\\{tail}")
        if candidate.is_file():
            return candidate
    return None


def resolve_source(
    source: str, max_bytes: int, wsl_distro: str | None = None
) -> tuple[Path, bool]:
    """Return (local path to the video, whether it is a temp file we created).

    `source` may be a Windows path, a UNC path (including one already
    pointing at `\\\\wsl.localhost\\...`), or a POSIX WSL path such as
    `/home/user/clip.mp4` - the latter is translated via `wsl_path_to_windows`.
    """
    if is_url(source):
        return download_video(source, max_bytes), True

    path = Path(source).expanduser()
    if not path.is_file():
        wsl_path = wsl_path_to_windows(source, wsl_distro)
        if wsl_path is None:
            raise TitleMakerError(f"Input video not found: {path}")
        path = wsl_path
    print(f"[1/5] Using local file {path}")
    return path, False
