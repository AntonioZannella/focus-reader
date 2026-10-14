import os
import sys

# The backend modules (server, tts_script, pdf_cleanup...) import each other
# by plain name, as when server.py runs from backend/.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "backend"))

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
