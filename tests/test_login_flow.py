"""The login gate on the home page: wrong password rejected, right password admitted."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from auth.passwords import hash_password

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def make():
    at = AppTest.from_file(APP, default_timeout=30)
    at.secrets["auth"] = {"username": "admin", "password_hash": hash_password("a-long-test-password", iterations=1000)}
    at.secrets["database"] = {"url": "sqlite://"}
    return at


def test_wrong_then_right_password():
    at = make().run()
    assert not at.exception
    assert any("Sign in" in s.value for s in at.subheader)
    at.text_input[0].set_value("admin")
    at.text_input[1].set_value("wrong")
    at.button[0].click().run()
    assert any("Invalid" in e.value for e in at.error)
    at.text_input[0].set_value("admin")
    at.text_input[1].set_value("a-long-test-password")
    at.button[0].click().run()
    assert not at.exception
    assert any("Add Property" in c.value for c in at.caption)
