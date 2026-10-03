__version__ = '1.0.0'

__all__ = ("make_cell_widget", "make_store_widget", "make_correction_widget")


def __getattr__(name):
    if name == "make_cell_widget":
        from .widget import make_cell_widget
        return make_cell_widget
    if name == "make_store_widget":
        from .store_widget import make_store_widget
        return make_store_widget
    if name == "make_correction_widget":
        from .widget import make_correction_widget
        return make_correction_widget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
