import os
import sys

# Ensure repository root is on sys.path so tests can import the integration package
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
