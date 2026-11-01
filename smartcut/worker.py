from PySide6.QtCore import QThread,Signal
from .process import Job,Cancelled
import time


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
        last_time=float('-inf');last_stage=None
        def progress(fraction,stage):
            nonlocal last_time,last_stage
            now=time.monotonic()
            if now-last_time>=.05 or stage!=last_stage or fraction>=1:
                self.progress.emit(fraction,stage);last_time=now;last_stage=stage
        try:
            result = self.operation(self.job, progress)
            self.result.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failure.emit(exc)


