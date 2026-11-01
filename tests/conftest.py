import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_AUDIO_BACKEND", "pulseaudio")

# Keep one application alive across tests; recreating Qt's multimedia backend
# between tests can leave native Windows handles pointing at a deleted instance.
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope='session', autouse=True)
def qt_application():
    app=QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
