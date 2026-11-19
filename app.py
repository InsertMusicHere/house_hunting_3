"""Housing Market Intelligence Platform - entry point (Phase 0 shell)."""
import streamlit as st

from auth.gate import require_login
from database.connection import check_connection

st.set_page_config(page_title="Housing Market Intelligence", page_icon="🏠", layout="wide")
require_login()

st.title("🏠 Housing Market Intelligence Platform")
st.caption("Use the sidebar: Add Property to save listings, Property Master to browse, edit and export them.")

st.subheader("Database connection")
if st.button("Test Neon connection"):
    with st.spinner("Connecting (the first call after idle can take a few seconds)..."):
        try:
            info = check_connection()
            st.success(f"Connected to database '{info['database']}'.")
            st.code(info["version"])
        except Exception as exc:  # noqa: BLE001
            st.error(f"Connection failed: {type(exc).__name__}: {exc}")
