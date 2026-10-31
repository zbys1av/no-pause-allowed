from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import Qt,QByteArray
from PySide6.QtGui import QIcon,QPixmap,QPainter
from PySide6.QtSvg import QSvgRenderer


@lru_cache(maxsize=64)
def icon(name, color='#c7d2e3', size=18):
    source=(Path(__file__).with_name('assets')/(name+'.svg')).read_text()
    renderer=QSvgRenderer(QByteArray(source.replace('currentColor',color).encode()))
    image=QPixmap(size*2,size*2)
    image.fill(Qt.transparent)
    painter=QPainter(image)
    renderer.render(painter)
    painter.end()
    image.setDevicePixelRatio(2)
    return QIcon(image)
