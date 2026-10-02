"""Constants and env loading for the AI features."""

import os

from dotenv import load_dotenv

load_dotenv()

# Optional at import time so the bot still runs (minus AI) without it; keystore checks.
KEY_ENCRYPTION_SECRET = os.getenv("KEY_ENCRYPTION_SECRET", "")
DB_PATH = "bot.db"
TIMEZONE = os.getenv("BOT_TIMEZONE", "UTC")  # IANA name used for natural-language /remind

# Verify against OpenAI's models/pricing page; lineup changes often.
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-6-luna")
LLM_MAX_OUTPUT_TOKENS = 1000  # reasoning tokens count against this; too low = blank replies
LLM_TIMEOUT_SECONDS = 30

MAX_QUESTION_CHARS = 1000
TLDR_MESSAGE_LIMIT = 50
TLDR_MAX_CHARS = 8000
COOLDOWN_SECONDS = 10
DISCORD_MESSAGE_LIMIT = 2000
