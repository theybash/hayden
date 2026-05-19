import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
NEWS_MODEL = os.getenv("NEWS_MODEL", "gpt-5.2")
SCORING_MODEL = os.getenv("SCORING_MODEL", "gpt-4.1-mini")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-4.1-mini")
NEWSLETTER_MODEL = os.getenv("NEWSLETTER_MODEL", "gpt-5.2")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.2")
NSE_REQUEST_TIMEOUT = int(os.getenv("NSE_REQUEST_TIMEOUT", "25"))

# Agentic RAG models
MAIN_AGENT_MODEL = os.getenv("MAIN_AGENT_MODEL", "gpt-5")
SUB_AGENT_MODEL = os.getenv("SUB_AGENT_MODEL", "gpt-4.1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
CHAT_DATA_DIR = os.path.join(os.path.dirname(__file__), "chat_data")
MAX_MAIN_CYCLES = 12       # main agent gets plenty of room for direct work
MAX_SUB_CYCLES = 3          # sub-agents must be fast
MAX_SUB_AGENTS = 3          # down from 5
MAX_VISION_CALLS = 4        # vision wildcards for agent
TOOL_RESULT_CAP = 2000      # max chars per tool result in message history
