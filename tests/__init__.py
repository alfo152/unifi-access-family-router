import os
import sys

_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "family_router", "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)
