"""Exclusive foreground-operation state; valid analysis is kept separately."""
from enum import Enum


class State(str, Enum):
    NO_VIDEO='no_video'
    READY='ready'
    LOADING='loading'
    LOADING_CANCELLED='loading_cancelled'
    LOADING_FAILED='loading_failed'
    ANALYZING='analyzing'
    ANALYSIS_COMPLETE='analysis_complete'
    ANALYSIS_CANCELLED='analysis_cancelled'
    ANALYSIS_FAILED='analysis_failed'
    EXPORTING='exporting'
    EXPORT_COMPLETE='export_complete'
    EXPORT_CANCELLED='export_cancelled'
    EXPORT_FAILED='export_failed'
    PREVIEW='preview'
    PREVIEW_COMPLETE='preview_complete'
    PREVIEW_CANCELLED='preview_cancelled'
    PREVIEW_FAILED='preview_failed'


class Workflow:
    def __init__(self):
        self.state=State.NO_VIDEO
        self.kind=None
        self.progress=None
        self.generation=0

    @property
    def busy(self):
        return self.kind is not None

    def begin(self, kind):
        if self.busy:
            raise RuntimeError('An operation is already running')
        self.generation+=1
        self.kind=kind
        self.progress=None
        self.state={'analysis':State.ANALYZING,'export':State.EXPORTING,'preview':State.PREVIEW,'loading':State.LOADING}[kind]
        return self.generation

    def update(self, generation, progress):
        if generation != self.generation or not self.busy:
            return False
        self.progress=min(1.,max(0.,progress))
        return True

    def finish(self, outcome):
        kind=self.kind
        if not kind:
            return
        if kind=='loading':
            self.state=State.READY if outcome=='complete' else State(f'loading_{outcome}')
        else:
            self.state=State(f'{"analysis" if kind=="analysis" else kind}_{outcome}')
        self.progress=1. if outcome=='complete' else None
        self.kind=None
