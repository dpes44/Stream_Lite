#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("STREAM_LITE_CONFIG", ROOT / "media.config.json"))
CACHE_ROOT = ROOT / ".stream_lite_cache"
THUMB_ROOT = CACHE_ROOT / "thumbs"
HLS_ROOT = CACHE_ROOT / "hls"

VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogm",
    ".ogv",
    ".ts",
    ".vob",
    ".webm",
    ".wmv",
}

MIME_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".ts": "video/mp2t",
    ".webm": "video/webm",
}

DEFAULT_CONFIG = {
    "libraryPaths": ["./videos"],
    "server": {"host": "0.0.0.0", "port": 8080},
    "tools": {
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
    },
    "thumbnails": {
        "enabled": False,
    },
    "transcoding": {
        "enabled": True,
        "maxSessions": 4,
        "maxHeight": 720,
        "crf": 23,
        "preset": "veryfast",
        "audioBitrate": "160k",
        "idleTimeoutSeconds": 900,
    },
}

CONFIG: dict = {}
LIBRARY: list[dict] = []
MEDIA_BY_ID: dict[str, dict] = {}
TRANSCODES: dict[str, dict] = {}
SCAN_ERRORS: list[str] = []
STATE_LOCK = threading.RLock()


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            user_config = json.load(handle)
    else:
        user_config = {}
    return deep_merge(DEFAULT_CONFIG, user_config)


def resolve_library_path(raw_path: str) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    if not expanded.is_absolute():
        expanded = ROOT / expanded
    return expanded.resolve()


def media_id_for(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]


def clean_title(stem: str) -> str:
    title = re.sub(r"\.mobile$", "", stem, flags=re.IGNORECASE)
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title or stem


def scan_library() -> None:
    items: list[dict] = []
    scan_errors: list[str] = []
    for raw_root in CONFIG.get("libraryPaths", []):
        root_path = resolve_library_path(raw_root)
        try:
            root_path.stat()
        except FileNotFoundError:
            scan_errors.append(f"{root_path}: path does not exist")
            continue
        except PermissionError:
            scan_errors.append(f"{root_path}: permission denied")
            continue
        except OSError as error:
            scan_errors.append(f"{root_path}: {error.strerror or error}")
            continue

        if not root_path.is_dir():
            scan_errors.append(f"{root_path}: not a folder")
            continue

        def on_walk_error(error: OSError) -> None:
            scan_errors.append(f"{error.filename}: {error.strerror or error}")

        for current_root, dirnames, filenames in os.walk(root_path, onerror=on_walk_error):
            dirnames[:] = [
                name
                for name in dirnames
                if not name.startswith(".") and name not in {"__pycache__", ".stream_lite_cache"}
            ]
            current_path = Path(current_root)
            for filename in filenames:
                file_path = current_path / filename
                extension = file_path.suffix.lower()
                if extension not in VIDEO_EXTENSIONS:
                    continue

                try:
                    stat = file_path.stat()
                except OSError:
                    continue

                relative_folder = str(file_path.parent.relative_to(root_path))
                if relative_folder == ".":
                    relative_folder = "Root"

                items.append(
                    {
                        "id": media_id_for(file_path.resolve()),
                        "path": str(file_path.resolve()),
                        "title": clean_title(file_path.stem),
                        "filename": filename,
                        "folder": relative_folder,
                        "root": str(root_path),
                        "rootName": root_path.name or str(root_path),
                        "extension": extension,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "probe": None,
                    }
                )

    items.sort(key=lambda item: (item["folder"].lower(), item["title"].lower()))
    with STATE_LOCK:
        LIBRARY[:] = items
        MEDIA_BY_ID.clear()
        MEDIA_BY_ID.update({item["id"]: item for item in items})
        SCAN_ERRORS[:] = scan_errors


def public_media(item: dict) -> dict:
    return {
        "id": item["id"],
        "title": item["title"],
        "filename": item["filename"],
        "folder": item["folder"],
        "rootName": item["rootName"],
        "extension": item["extension"].lstrip(".").upper(),
        "size": item["size"],
        "modified": item["modified"],
        "thumbnailUrl": f"/api/media/{item['id']}/thumb.jpg",
    }


def public_scan_errors() -> list[str]:
    with STATE_LOCK:
        return list(SCAN_ERRORS)


def configured_tool(name: str) -> str:
    return str(CONFIG.get("tools", {}).get(name, name))


def resolve_tool(name: str) -> str | None:
    configured = configured_tool(name)
    found = shutil.which(configured)
    if found:
        return found

    path = Path(configured)
    if path.exists():
        return str(path)

    return None


def require_tool(name: str) -> str:
    tool = resolve_tool(name)
    if tool:
        return tool

    install_hint = (
        "Install FFmpeg and make sure ffmpeg.exe and ffprobe.exe are available in PATH, "
        "or set their full paths in media.config.json."
    )
    raise HttpError(HTTPStatus.SERVICE_UNAVAILABLE, f"{name} was not found. {install_hint}")


def probe_media(item: dict) -> dict:
    if item.get("probe"):
        return item["probe"]

    command = [
        require_tool("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,channels",
        "-of",
        "json",
        item["path"],
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=25, check=True)
        probe = json.loads(completed.stdout or "{}")
    except HttpError:
        raise
    except Exception as error:
        probe = {"error": str(error), "streams": [], "format": {}}

    item["probe"] = probe
    return probe


def first_stream(probe: dict, codec_type: str) -> dict | None:
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == codec_type:
            return stream
    return None


def direct_playable(item: dict) -> bool:
    if not resolve_tool("ffprobe"):
        return item["extension"] in {".mp4", ".m4v", ".mov", ".webm", ".ogg", ".ogv"}

    probe = probe_media(item)
    video = first_stream(probe, "video")
    audio = first_stream(probe, "audio")
    video_codec = (video or {}).get("codec_name")
    audio_codec = (audio or {}).get("codec_name")
    extension = item["extension"]

    if not video_codec:
        return False

    has_browser_audio = audio_codec in {None, "aac", "mp3", "opus", "vorbis"}
    if extension in {".mp4", ".m4v", ".mov"}:
        return video_codec == "h264" and has_browser_audio
    if extension == ".webm":
        return video_codec in {"vp8", "vp9", "av1"} and has_browser_audio
    if extension in {".ogg", ".ogv"}:
        return video_codec in {"theora", "vp8"} and has_browser_audio
    return False


def validate_media_access(item: dict) -> None:
    file_path = Path(item["path"])
    try:
        if not file_path.exists():
            raise HttpError(HTTPStatus.NOT_FOUND, "File is no longer available. Rescan the library.")
        with file_path.open("rb") as handle:
            handle.read(1)
    except HttpError:
        raise
    except PermissionError:
        raise HttpError(
            HTTPStatus.FORBIDDEN,
            f"No permission to read {file_path}. Remount the drive for the dpes user.",
        )
    except OSError as error:
        raise HttpError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            f"Could not read {file_path}: {error.strerror or error}. Check the drive connection.",
        )


def active_transcode_count() -> int:
    return sum(1 for session in TRANSCODES.values() if session["process"].poll() is None)


def ensure_transcode(item: dict) -> dict:
    if not CONFIG["transcoding"].get("enabled", True):
        raise HttpError(HTTPStatus.BAD_REQUEST, "Transcoding is disabled")

    media_id = item["id"]
    with STATE_LOCK:
        existing = TRANSCODES.get(media_id)
        if existing and existing["playlist"].exists():
            existing["lastAccess"] = time.time()
            return existing

        max_sessions = int(CONFIG["transcoding"].get("maxSessions", 4))
        if active_transcode_count() >= max_sessions:
            raise HttpError(
                HTTPStatus.TOO_MANY_REQUESTS,
                f"Transcode limit reached ({max_sessions}). Stop another stream and try again.",
            )

        output_dir = HLS_ROOT / media_id
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        playlist = output_dir / "stream.m3u8"
        log_path = output_dir / "ffmpeg.log"
        ffmpeg = require_tool("ffmpeg")
        max_height = int(CONFIG["transcoding"].get("maxHeight", 720))
        vf_filter = f"scale=-2:min({max_height}\\,ih)"
        args = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            item["path"],
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-c:v",
            "libx264",
            "-preset",
            str(CONFIG["transcoding"].get("preset", "veryfast")),
            "-crf",
            str(CONFIG["transcoding"].get("crf", 23)),
            "-vf",
            vf_filter,
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-b:a",
            str(CONFIG["transcoding"].get("audioBitrate", "160k")),
            "-f",
            "hls",
            "-hls_time",
            "6",
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_flags",
            "independent_segments",
            "-hls_segment_filename",
            str(output_dir / "segment_%05d.ts"),
            str(playlist),
        ]

        with log_path.open("ab") as log_file:
            process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log_file)

        session = {
            "process": process,
            "dir": output_dir,
            "playlist": playlist,
            "log": log_path,
            "lastAccess": time.time(),
        }
        TRANSCODES[media_id] = session

    if not wait_for_path(playlist, process, timeout=18):
        if process.poll() is not None:
            raise HttpError(HTTPStatus.INTERNAL_SERVER_ERROR, read_tail(log_path))
        raise HttpError(HTTPStatus.ACCEPTED, "Stream is still preparing")

    return session


def wait_for_path(path: Path, process: subprocess.Popen | None = None, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        if process is not None and process.poll() is not None:
            return path.exists() and path.stat().st_size > 0
        time.sleep(0.25)
    return False


def read_tail(path: Path, limit: int = 3000) -> str:
    try:
        data = path.read_bytes()[-limit:]
        return data.decode("utf-8", errors="replace") or "FFmpeg failed"
    except OSError:
        return "FFmpeg failed"


def stop_transcode(media_id: str, remove_files: bool = False) -> bool:
    with STATE_LOCK:
        session = TRANSCODES.pop(media_id, None)
    if not session:
        return False

    process = session["process"]
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    if remove_files:
        shutil.rmtree(session["dir"], ignore_errors=True)
    return True


def cleanup_transcodes() -> None:
    timeout = int(CONFIG["transcoding"].get("idleTimeoutSeconds", 900))
    while True:
        time.sleep(60)
        now = time.time()
        with STATE_LOCK:
            stale_ids = [
                media_id
                for media_id, session in TRANSCODES.items()
                if session["process"].poll() is None and now - session["lastAccess"] > timeout
            ]
        for media_id in stale_ids:
            stop_transcode(media_id)


def generate_thumbnail(item: dict, output_path: Path) -> bool:
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = ["00:01:00", "00:00:05", "00:00:01"]
    for timestamp in attempts:
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            timestamp,
            "-i",
            item["path"],
            "-frames:v",
            "1",
            "-vf",
            "scale=520:-2",
            "-q:v",
            "4",
            str(output_path),
        ]
        try:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            if output_path.exists() and output_path.stat().st_size > 0:
                return True
        except Exception:
            pass
    return False


def parse_range(range_header: str, size: int) -> tuple[int, int] | None:
    if not range_header.startswith("bytes="):
        return None
    value = range_header.replace("bytes=", "", 1).split(",", 1)[0]
    start_raw, _, end_raw = value.partition("-")
    try:
        if start_raw == "":
            length = int(end_raw)
            return max(size - length, 0), size - 1
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


class StreamLiteHandler(BaseHTTPRequestHandler):
    server_version = "StreamLite/2.0"

    def do_GET(self) -> None:
        self.handle_request(head_only=False)

    def do_HEAD(self) -> None:
        self.handle_request(head_only=True)

    def do_POST(self) -> None:
        self.handle_post()

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def handle_request(self, head_only: bool) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path == "/":
                self.serve_file(ROOT / "index.html", "text/html; charset=utf-8", head_only)
                return
            if path in {"/styles.css", "/script.js", "/vendor/hls.min.js"}:
                self.serve_file(ROOT / path.lstrip("/"), None, head_only)
                return
            if path == "/api/library":
                self.send_json(
                    {
                        "items": [public_media(item) for item in LIBRARY],
                        "count": len(LIBRARY),
                        "errors": public_scan_errors(),
                    }
                )
                return
            if path.startswith("/api/media/"):
                self.handle_media(path, head_only)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except HttpError as error:
            self.send_json({"error": error.message}, status=error.status)
        except BrokenPipeError:
            return
        except Exception as error:
            self.send_json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_post(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path == "/api/rescan":
                scan_library()
                self.send_json(
                    {
                        "items": [public_media(item) for item in LIBRARY],
                        "count": len(LIBRARY),
                        "errors": public_scan_errors(),
                    }
                )
                return
            if path.startswith("/api/media/") and path.endswith("/stop"):
                media_id = path.strip("/").split("/")[2]
                stopped = stop_transcode(media_id)
                self.send_json({"stopped": stopped})
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as error:
            self.send_json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_media(self, path: str, head_only: bool) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            raise HttpError(HTTPStatus.NOT_FOUND, "Unknown media route")

        media_id = parts[2]
        item = MEDIA_BY_ID.get(media_id)
        if not item:
            raise HttpError(HTTPStatus.NOT_FOUND, "Media not found")

        action = parts[3] if len(parts) >= 4 else ""
        if action == "direct":
            validate_media_access(item)
            self.serve_file(Path(item["path"]), guess_mime(Path(item["path"])), head_only)
            return
        if action == "thumb.jpg":
            thumb_path = THUMB_ROOT / f"{media_id}.jpg"
            if not thumb_path.exists() and CONFIG.get("thumbnails", {}).get("enabled", False):
                generate_thumbnail(item, thumb_path)
            if thumb_path.exists():
                self.serve_file(thumb_path, "image/jpeg", head_only)
            else:
                self.send_placeholder_thumbnail(item["title"])
            return
        if action == "playback":
            validate_media_access(item)
            if direct_playable(item):
                self.send_json(
                    {
                        "mode": "direct",
                        "url": f"/api/media/{media_id}/direct",
                        "title": item["title"],
                    }
                )
            else:
                ensure_transcode(item)
                self.send_json(
                    {
                        "mode": "hls",
                        "url": f"/api/media/{media_id}/hls/stream.m3u8",
                        "title": item["title"],
                    }
                )
            return
        if action == "hls":
            hls_file = parts[4] if len(parts) >= 5 else "stream.m3u8"
            self.serve_hls(item, hls_file, head_only)
            return

        raise HttpError(HTTPStatus.NOT_FOUND, "Unknown media action")

    def serve_hls(self, item: dict, filename: str, head_only: bool) -> None:
        if not re.match(r"^(stream\.m3u8|segment_\d+\.ts)$", filename):
            raise HttpError(HTTPStatus.FORBIDDEN, "Invalid HLS file")
        session = ensure_transcode(item)
        session["lastAccess"] = time.time()
        file_path = session["dir"] / filename
        if filename.endswith(".ts") and not wait_for_path(file_path, session["process"], timeout=15):
            raise HttpError(HTTPStatus.NOT_FOUND, "Segment not ready")
        self.serve_file(file_path, guess_mime(file_path), head_only, no_cache=True)

    def send_json(self, data: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_placeholder_thumbnail(self, title: str) -> None:
        initials = "".join(word[0] for word in re.findall(r"[A-Za-z0-9]+", title)[:2]).upper() or "SL"
        body = f"""<svg xmlns="http://www.w3.org/2000/svg" width="520" height="292" viewBox="0 0 520 292">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ef4b35"/>
      <stop offset="1" stop-color="#31c2a0"/>
    </linearGradient>
  </defs>
  <rect width="520" height="292" fill="#101219"/>
  <rect width="520" height="292" fill="url(#bg)" opacity="0.32"/>
  <circle cx="260" cy="146" r="54" fill="#050608" opacity="0.6"/>
  <text x="260" y="160" fill="#f4f0e8" font-family="Arial, sans-serif" font-size="40" font-weight="700" text-anchor="middle">{initials}</text>
</svg>""".encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(
        self,
        file_path: Path,
        content_type: str | None,
        head_only: bool,
        no_cache: bool = False,
    ) -> None:
        try:
            if not file_path.exists() or not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            size = file_path.stat().st_size
        except PermissionError:
            self.send_error(HTTPStatus.FORBIDDEN, "Permission denied")
            return
        except OSError as error:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, error.strerror or str(error))
            return

        if size <= 0:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        content_type = content_type or guess_mime(file_path)
        range_value = self.headers.get("Range")
        byte_range = parse_range(range_value, size) if range_value else None

        if range_value and not byte_range:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return

        if byte_range:
            start, end = byte_range
            status = HTTPStatus.PARTIAL_CONTENT
        else:
            start, end = 0, size - 1
            status = HTTPStatus.OK

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if no_cache:
            self.send_header("Cache-Control", "no-store")
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        if head_only:
            return

        try:
            with file_path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError as error:
            print(f"Read failed for {file_path}: {error.strerror or error}")


def guess_mime(file_path: Path) -> str:
    return MIME_TYPES.get(file_path.suffix.lower()) or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"


def main() -> None:
    global CONFIG
    CONFIG = load_config()
    CACHE_ROOT.mkdir(exist_ok=True)
    THUMB_ROOT.mkdir(parents=True, exist_ok=True)
    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    scan_library()

    cleanup_thread = threading.Thread(target=cleanup_transcodes, daemon=True)
    cleanup_thread.start()

    host = str(CONFIG["server"].get("host", "0.0.0.0"))
    port = int(os.environ.get("PORT", CONFIG["server"].get("port", 8080)))
    server = ThreadingHTTPServer((host, port), StreamLiteHandler)
    print(f"Stream Lite is running at http://127.0.0.1:{port}/")
    print("Use media.config.json to point libraryPaths at your external drive.")
    print(f"Loaded {len(LIBRARY)} videos.")
    for tool_name in ("ffmpeg", "ffprobe"):
        if not resolve_tool(tool_name):
            print(f"Tool warning: {tool_name} was not found. Transcoding may not work.")
    for error in public_scan_errors():
        print(f"Scan warning: {error}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for media_id in list(TRANSCODES):
            stop_transcode(media_id)
        server.server_close()


if __name__ == "__main__":
    main()
