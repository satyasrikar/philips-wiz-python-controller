__all__ = ["__version__"]

try:
    from importlib.resources import files as _files
    __version__ = (_files("wiz_controller") / "VERSION").read_text().strip()
except Exception:
    __version__ = "0.1.0"