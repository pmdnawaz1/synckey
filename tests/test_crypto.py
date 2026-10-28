import pytest

from synckey.crypto import SecretBox


def test_seal_open_roundtrip(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    token = box.seal("sk-live-abc123")
    assert token != b"sk-live-abc123"
    assert box.open(token) == "sk-live-abc123"


def test_key_file_created_with_content(tmp_path):
    path = tmp_path / "secret.key"
    SecretBox(path)
    assert path.exists()
    assert len(path.read_bytes()) > 0


def test_existing_key_reused(tmp_path):
    path = tmp_path / "secret.key"
    box1 = SecretBox(path)
    box2 = SecretBox(path)
    assert box2.open(box1.seal("hello")) == "hello"


def test_wrong_key_raises_runtime_error(tmp_path):
    box1 = SecretBox(tmp_path / "a.key")
    token = box1.seal("secret")
    box2 = SecretBox(tmp_path / "b.key")
    with pytest.raises(RuntimeError, match="Could not decrypt"):
        box2.open(token)
