import asyncio
import os
import sys

from cryptography.fernet import Fernet

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

config.KEY_ENCRYPTION_SECRET = Fernet.generate_key().decode()
import gpt as keystore


def test_roundtrip(tmp_path):
    config.DB_PATH = str(tmp_path / "t.db")

    async def run():
        await keystore.init_db()
        assert await keystore.get_key(1) is None
        await keystore.set_key(1, "sk-secret-1234567890abcd")
        assert await keystore.get_key(1) == "sk-secret-1234567890abcd"
        await keystore.set_key(1, "sk-other-1234567890abcdef")
        assert (await keystore.get_key(1)).endswith("cdef")
        assert await keystore.delete_key(1) is True
        assert await keystore.delete_key(1) is False

    asyncio.run(run())
    assert b"sk-secret" not in open(config.DB_PATH, "rb").read()


def test_parse_reminder_reply():
    now = 1_000_000
    ok = '{"task": "call mom", "when_iso": "1970-01-12T14:00:00+00:00"}'
    r = keystore.parse_reminder_reply(ok, now=now)
    assert r and r.task == "call mom"
    assert keystore.parse_reminder_reply("nonsense", now=now) is None
    past = '{"task": "x", "when_iso": "1970-01-01T00:00:00+00:00"}'
    assert keystore.parse_reminder_reply(past, now=now) is None


def test_chunk_and_transcript():
    assert max(map(len, keystore.chunk("a b\n" * 1500))) <= 2000
    assert keystore.build_transcript([("a", "hi"), ("b", "")]) == "a: hi"
