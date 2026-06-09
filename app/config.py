import os
from dotenv import load_dotenv

load_dotenv()

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")

# LLM / Azure OpenAI
LLM_API_KEY = os.getenv("LLM_API_KEY", "dummy")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_SUBSCRIPTION_KEY = os.getenv("LLM_SUBSCRIPTION_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_API_VERSION = os.getenv("LLM_API_VERSION", "2024-08-01-preview")

# App
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))
