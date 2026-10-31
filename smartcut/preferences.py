"""Portable language preference; no registry writes and no video paths stored."""
import json
from pathlib import Path
import sys


def path():
    root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent
    return root/'.smartcut-preferences.json'


def read_language():
    try:
        data=json.loads(path().read_text())
        language=data.get('language','uk') if isinstance(data,dict) else 'uk'
        return language if language in ('uk','en') else 'uk'
    except (OSError,ValueError):return 'uk'


def save_language(language):
    target=path()
    try:
        existing=json.loads(target.read_text()) if target.exists() else {}
        if not isinstance(existing,dict):existing={}
        existing['language']=language
        pending=target.with_suffix('.tmp');pending.write_text(json.dumps(existing),encoding='utf-8');pending.replace(target)
    except (OSError,ValueError):pass
