from auth.passwords import hash_password, verify_password


def test_correct_password_verifies():
    h = hash_password("correct horse battery", iterations=1000)
    assert verify_password("correct horse battery", h)


def test_wrong_password_rejected():
    h = hash_password("correct horse battery", iterations=1000)
    assert not verify_password("wrong", h)


def test_hashes_are_salted():
    assert hash_password("same", iterations=1000) != hash_password("same", iterations=1000)


def test_malformed_hash_rejected():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "md5$1$a$b")
