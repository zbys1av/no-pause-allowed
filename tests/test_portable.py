import hashlib
import importlib.util
from pathlib import Path
from zipfile import ZipFile
import pytest

spec = importlib.util.spec_from_file_location("prepare_portable", Path(__file__).resolve().parents[1]/"scripts"/"prepare_portable.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_download_rejects_tampered_binary(tmp_path):
    source = tmp_path/"upstream.zip"
    source.write_bytes(b"upstream binary")
    output = tmp_path/"cache"/"download.zip"
    with pytest.raises(RuntimeError, match="SHA-256"):
        prepare.verified_download(source.as_uri(), output, "0"*64)
    assert not output.exists() and not output.with_suffix(".zip.part").exists()
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    prepare.verified_download(source.as_uri(), output, expected)
    assert output.read_bytes() == source.read_bytes()
    source.unlink()
    assert prepare.verified_download(source.as_uri(), output, expected) == output


def test_prepare_preserves_upstream_notices(tmp_path, monkeypatch):
    import json
    (tmp_path/"scripts").mkdir()
    archive = tmp_path/"ffmpeg.zip"
    with ZipFile(archive, "w") as z:
        for name, data in {"bin/ffmpeg.exe": b"encoder", "bin/ffprobe.exe": b"probe",
                           "LICENSE": b"GPL", "README.txt": b"upstream source details"}.items():
            z.writestr("ffmpeg-pinned/"+name, data)
    (tmp_path/"scripts"/"ffmpeg.lock.json").write_text(json.dumps({"url": "https://example.invalid/ffmpeg.zip", "sha256": "unused", "archive_root": "ffmpeg-pinned"}))
    monkeypatch.setattr(prepare, "verified_download", lambda *_: archive)
    tools = prepare.prepare(tmp_path)
    assert (tools/"ffmpeg.exe").read_bytes() == b"encoder"
    assert (tools/"licenses"/"LICENSE").read_bytes() == b"GPL"
    assert (tools/"licenses"/"README.txt").read_bytes() == b"upstream source details"


@pytest.mark.parametrize('name', ['ffmpeg', 'ffprobe'])
def test_frozen_uses_bundled_tools_despite_external_overrides(tmp_path, monkeypatch, name):
    import os
    import smartcut.process as process
    executable = tmp_path/'SmartVideoCutter.exe'
    filename = name + ('.exe' if os.name == 'nt' else '')
    bundled = tmp_path/'tools'/filename
    bundled.parent.mkdir()
    bundled.write_bytes(b'bundled')
    external = tmp_path/('external-'+filename)
    external.write_bytes(b'external')
    monkeypatch.setattr(process.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(process.sys, 'executable', str(executable))
    monkeypatch.setenv('SMARTCUT_'+name.upper(), str(external))
    monkeypatch.setattr(process.shutil, 'which', lambda _: pytest.fail('Portable searched system PATH'))
    assert process.tool(name) == str(bundled)


@pytest.mark.parametrize('name', ['ffmpeg', 'ffprobe'])
def test_frozen_missing_bundle_never_falls_back_to_system(tmp_path, monkeypatch, name):
    import smartcut.process as process
    from smartcut.errors import AppError
    external = tmp_path/'system-tool'
    external.write_bytes(b'external')
    monkeypatch.setattr(process.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(process.sys, 'executable', str(tmp_path/'SmartVideoCutter.exe'))
    monkeypatch.setenv('SMARTCUT_'+name.upper(), str(external))
    monkeypatch.setattr(process.shutil, 'which', lambda _: str(external))
    with pytest.raises(AppError) as error:
        process.tool(name)
    assert error.value.code == 'error.tool'


def test_source_development_keeps_explicit_tool_override(tmp_path, monkeypatch):
    import smartcut.process as process
    external = tmp_path/'development-ffmpeg'
    external.write_bytes(b'development')
    monkeypatch.setattr(process.sys, 'frozen', False, raising=False)
    monkeypatch.setenv('SMARTCUT_FFMPEG', str(external))
    assert process.tool('ffmpeg') == str(external)
