"""sessionmail - mail for coding agents: message, pager, memory over one maildir."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sessionmail")
except PackageNotFoundError:  # running from a bare checkout, not installed
    __version__ = "0+unknown"
