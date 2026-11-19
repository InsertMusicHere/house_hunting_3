"""Generate the password_hash value for .streamlit/secrets.toml.

Run from the project root:  python scripts/make_password_hash.py
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth.passwords import hash_password  # noqa: E402

pw = getpass.getpass("Choose admin password: ")
if len(pw) < 12:
    sys.exit("Use at least 12 characters.")
if getpass.getpass("Repeat password: ") != pw:
    sys.exit("Passwords do not match.")
print("\nPaste this into .streamlit/secrets.toml under [auth]:\n")
print(f'password_hash = "{hash_password(pw)}"')
