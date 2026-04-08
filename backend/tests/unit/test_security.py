from app.security import (
    generate_runner_token,
    generate_salt,
    hash_token,
    verify_token,
)


def test_generate_runner_token_has_prefix_and_entropy():
    token_one = generate_runner_token()
    token_two = generate_runner_token()

    assert token_one.startswith("rnr_")
    assert token_two.startswith("rnr_")
    assert token_one != token_two
    assert len(token_one) >= 32


def test_generate_salt_returns_non_empty_bytes():
    salt = generate_salt()

    assert isinstance(salt, bytes)
    assert len(salt) >= 16


def test_hash_token_is_stable_for_same_inputs():
    token = "rnr_example_token"
    salt = b"stable-salt-value"

    digest_one = hash_token(token, salt)
    digest_two = hash_token(token, salt)

    assert isinstance(digest_one, str)
    assert digest_one == digest_two
    assert len(digest_one) > 0


def test_verify_token_accepts_matching_token_and_rejects_other_token():
    token = generate_runner_token()
    salt = generate_salt()
    expected_hash = hash_token(token, salt)

    assert verify_token(token, salt, expected_hash) is True
    assert verify_token(f"{token}_different", salt, expected_hash) is False
