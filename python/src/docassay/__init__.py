"""pytest for document ingestion.

Example:
    import docassay

    print(docassay.__version__)
"""

from importlib.metadata import version

from docassay.config import ConfigError, load_config
from docassay.golden import load_golden

__version__ = version("docassay")

__all__ = ["ConfigError", "__version__", "load_config", "load_golden"]
