"""quints — Swiss VAT & accounting for plain-text (beancount) books."""

from importlib.metadata import version

# Derived from the installed distribution (pyproject.toml is the single
# source) — a hardcoded string here drifted from the released version once.
__version__ = version("quints")
