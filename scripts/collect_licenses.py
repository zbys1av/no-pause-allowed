"""Preserve installed package license files in the portable distribution."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys

destination = Path(sys.argv[1])
for dist in distributions():
    name = dist.metadata.get("Name", "unknown")
    if not name.lower().startswith(("pyside", "shiboken", "numpy", "pyinstaller")):
        continue
    for entry in dist.files or []:
        if any("license" in p.lower() or "copying" in p.lower() for p in entry.parts):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                target = destination / name / str(entry).replace("..", "_")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", destination / "PYTHON-LICENSE.txt")
