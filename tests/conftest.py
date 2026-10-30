import os

# Tests never send traces to LangSmith. Set before screener.config loads .env;
# load_dotenv does not override variables that are already set.
os.environ["LANGSMITH_TRACING"] = "false"
