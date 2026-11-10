"""FrameCue (package `promolens`): find and rank promo clips in microdrama screenplays."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("promolens")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
