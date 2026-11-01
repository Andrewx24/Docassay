"""pytest for document ingestion.

Example:
    import docassay

    print(docassay.__version__)
"""

from importlib.metadata import version

from docassay.config import ConfigError, load_config

__version__ = version("docassay")

__all__ = ["ConfigError", "__version__", "load_config"]
