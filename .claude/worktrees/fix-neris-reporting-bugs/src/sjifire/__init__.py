"""SJI Fire District utilities package."""

import sys

__version__ = "0.1.0"

# Load .env once on first import — skip during tests so credentials
# never leak into the test environment.
if "pytest" not in sys.modules:
    from dotenv import load_dotenv

    load_dotenv()
