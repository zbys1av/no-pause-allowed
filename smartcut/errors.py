from .i18n import tr


class AppError(ValueError):
    def __init__(self, code, details='', **params):
        self.code, self.details, self.params = code, str(details), params
        super().__init__(tr('en', code, **params) + ('\n'+self.details if self.details else ''))


def user_error(error, language):
    if isinstance(error, AppError):
        return tr(language, error.code, **error.params), error.details
    return tr(language, 'error.file' if isinstance(error, OSError) else 'error.unexpected'), str(error)
