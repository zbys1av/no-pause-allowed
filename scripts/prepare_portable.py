"""Build-time download only: pin and verify upstream FFmpeg, preserve notices."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent


def verified_download(url, destination, expected):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        with destination.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() == expected:
                return destination
    pending = destination.with_suffix(destination.suffix + ".part")
    try:
        # urllib retains the system CA trust and certificate verification.
        with urllib.request.urlopen(url, timeout=120) as source, pending.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024*1024)
        with pending.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                raise RuntimeError("FFmpeg SHA-256 verification failed; nothing was installed")
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    return destination


def prepare(root=ROOT):
    spec = json.loads((root / "scripts" / "ffmpeg.lock.json").read_text())
    archive = verified_download(spec["url"], root / "build" / "downloads" / "ffmpeg.zip", spec["sha256"])
    tools = root / "tools"
    licenses = tools / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as z:
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            with z.open(f"{spec['archive_root']}/bin/{name}") as source, (tools/name).open("wb") as output:
                shutil.copyfileobj(source, output)
        for name in ("LICENSE", "README.txt"):
            (licenses/name).write_bytes(z.read(f"{spec['archive_root']}/{name}"))
    (licenses / "provenance.json").write_text(json.dumps(spec, indent=2)+"\n", encoding="utf-8")
    return tools


if __name__ == "__main__":
    print(f"Verified FFmpeg/FFprobe installed in {prepare()}")
