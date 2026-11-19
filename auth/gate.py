"""Streamlit login gate: single admin user, credentials from st.secrets.

Call require_login() at the top of app.py and of every page.
It calls st.stop() until the user is authenticated.
"""
import hmac
import time

import streamlit as st

from auth.passwords import verify_password

MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
# Placeholder hash so a wrong username still costs one full hash computation.
_DUMMY_HASH = "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@st.cache_resource
def _attempt_tracker() -> dict:
    """Process-wide failure counter (shared across browser sessions)."""
    return {"failures": 0, "locked_until": 0.0}


def _credentials() -> tuple[str, str] | None:
    try:
        return st.secrets["auth"]["username"], st.secrets["auth"]["password_hash"]
    except (KeyError, FileNotFoundError):
        return None


def logout() -> None:
    st.session_state["authenticated"] = False
    st.rerun()


def require_login() -> None:
    if st.session_state.get("authenticated"):
        with st.sidebar:
            if st.button("Log out"):
                logout()
        return

    creds = _credentials()
    if creds is None:
        st.error(
            "Login is not configured. Add an [auth] section with username and "
            "password_hash to .streamlit/secrets.toml (see secrets.toml.example)."
        )
        st.stop()

    expected_user, stored_hash = creds
    tracker = _attempt_tracker()

    st.title("Housing Market Intelligence Platform")
    st.subheader("Sign in")

    remaining = tracker["locked_until"] - time.time()
    if remaining > 0:
        st.error(f"Too many failed attempts. Try again in {int(remaining) + 1} seconds.")
        st.stop()

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        user_ok = hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
        # Always run the hash check so timing does not reveal whether the username was right.
        pass_ok = verify_password(password, stored_hash if user_ok else _DUMMY_HASH)
        if user_ok and pass_ok:
            tracker["failures"] = 0
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            tracker["failures"] += 1
            if tracker["failures"] >= MAX_FAILURES:
                tracker["locked_until"] = time.time() + LOCKOUT_SECONDS
                tracker["failures"] = 0
            st.error("Invalid username or password.")
    st.stop()
