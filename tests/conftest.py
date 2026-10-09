import os
import sys
from pathlib import Path

# Make the test suite hermetic. The repo ships a .env with INSTAMART_MODE=mcp
# and a live .swiggy_token, which would otherwise let "local" tests dial real
# Swiggy. Tests that genuinely need live mode opt in with monkeypatch.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["INSTAMART_MODE"] = "local"
os.environ["SWIGGY_MCP_TOKEN"] = ""
os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")

# Point the on-disk token store somewhere that cannot supply a token, so no
# test can accidentally go live through the real .swiggy_token file.
import backend.mcp_transport as _transport  # noqa: E402
_transport._token_path = lambda: Path(__file__).parent / "no-such-token"