"""
this file should be executed once to create index for query operation

execute once unless data source is updated
"""

from src.indexing import index_document

if __name__ == "__main__":
    import sys

    file_path = sys.argv[1]
    index_document(file_path)
