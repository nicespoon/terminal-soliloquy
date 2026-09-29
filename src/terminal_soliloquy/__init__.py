"""Terminal Soliloquy Package"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("terminal-soliloquy")
except PackageNotFoundError:
    __version__ = "unknown"