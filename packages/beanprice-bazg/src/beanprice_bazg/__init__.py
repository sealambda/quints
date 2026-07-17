"""beanprice-bazg: official Swiss BAZG/EZV daily FX rates for beanprice.

Re-exports :class:`Source` so the beancount price-source string can be the short
``CHF:beanprice_bazg/USD`` (beanprice imports the module and looks for ``Source``).
"""

from beanprice_bazg.bazg import BAZGError, Source, SourcePrice

__all__ = ["BAZGError", "Source", "SourcePrice"]
__version__ = "0.2.0"
