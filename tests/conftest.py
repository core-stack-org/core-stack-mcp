import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./.pytest_mcp.db"
os.environ["MCP_ALLOWED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["CORE_STACK_API_KEY"] = ""
os.environ["CORE_STACK_BASE_URL"] = "https://geoserver.core-stack.org"
os.environ["TRUST_PROXY"] = "false"
