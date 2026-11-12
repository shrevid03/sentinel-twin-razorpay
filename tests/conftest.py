"""Tests always run offline: real keys in .env must never reach Razorpay or an LLM from the test suite.

Setting the variables to "" before anything is imported wins over load_dotenv(), which never overrides
variables that already exist.
"""
import os

for _k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "GEMINI_API_KEY", "GROQ_API_KEY",
           "ANTHROPIC_API_KEY", "LLM_PROVIDER", "ADMIN_TOKEN", "ELASTIC_URL", "ZAPIER_WEBHOOK_URL", "CHANNEL3_API_KEY"):
    os.environ[_k] = ""
