"""beanprice-bazg: official Swiss BAZG/EZV daily FX rates for beanprice.

Re-exports :class:`Source` so the beancount price-source string can be the short
``CHF:beanprice_bazg/USD`` (beanprice imports the module and looks for ``Source``).
"""

from importlib.metadata import version

from beanprice_bazg.bazg import BAZGError, Source, SourcePrice

__all__ = ["BAZGError", "Source", "SourcePrice"]
# Derived from the installed distribution (pyproject.toml is the single source).
__version__ = version("beanprice-bazg")
