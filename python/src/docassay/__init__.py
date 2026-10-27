"""pytest for document ingestion.

Example:
    import docassay

    print(docassay.__version__)
"""

from importlib.metadata import version

__version__ = version("docassay")

__all__ = ["__version__"]
