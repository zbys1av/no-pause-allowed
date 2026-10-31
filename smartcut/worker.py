from PySide6.QtCore import QThread,Signal
from .process import Job,Cancelled


class Worker(QThread):
    result = Signal(object)
    failure = Signal(object)
    progress = Signal(float, object)
    cancelled = Signal()

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.job = Job()
        self.operation = operation

    def run(self):
        try:
            result = self.operation(self.job, self.progress.emit)
            self.result.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failure.emit(exc)


