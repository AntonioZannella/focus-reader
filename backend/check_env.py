import importlib.metadata

import edge_tts
import pymupdf
import flask

print("edge-tts version:", importlib.metadata.version("edge-tts"))
print("flask version:", importlib.metadata.version("flask"))
print("pymupdf version:", importlib.metadata.version("pymupdf"))
