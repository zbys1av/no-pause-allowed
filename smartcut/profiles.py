from .model import Settings

PROFILES = {
    'drawing': Settings(sensitivity=95, min_idle=3, padding=.6, sample_fps=8, analysis_width=960, debounce=.3),
    'screen': Settings(sensitivity=80, min_idle=2.5, padding=.35, sample_fps=6, analysis_width=640, debounce=.3),
    'general': Settings(sensitivity=65, min_idle=2, padding=.4, sample_fps=6, analysis_width=640, debounce=.15),
}
