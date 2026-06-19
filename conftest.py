"""Root pytest config.

Adding the repo root to sys.path lets backend tests import the path-based
`backend.*` packages (the SDK is installed as `argos`, so it doesn't need this).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
