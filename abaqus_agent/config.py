# -*- coding: utf-8 -*-
"""Configuration management for Abaqus AI Agent."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory for the abaqus_agent package and project root
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

# Load .env file from project root, package directory, or current working directory
load_dotenv(ROOT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")
load_dotenv()

# Override ROOT_DIR if explicitly defined in environment
_env_project_root = os.environ.get("PROJECT_ROOT", "").strip()
if _env_project_root and Path(_env_project_root).is_dir():
    ROOT_DIR = Path(_env_project_root).resolve()


def get_env_str(key: str, default: str) -> str:
    """Retrieve string value from environment variable."""
    return os.environ.get(key, default).strip()


def get_env_int(key: str, default: int) -> int:
    """Retrieve integer value from environment variable."""
    val = os.environ.get(key, "").strip()
    if val.isdigit():
        return int(val)
    return default


def get_env_float(key: str, default: float) -> float:
    """Retrieve float value from environment variable."""
    val = os.environ.get(key, "").strip()
    try:
        return float(val)
    except ValueError:
        return default


def get_env_bool(key: str, default: bool) -> bool:
    """Retrieve boolean value from environment variable."""
    val = os.environ.get(key, "").strip().lower()
    if val in ["1", "true", "yes", "on"]:
        return True
    elif val in ["0", "false", "no", "off"]:
        return False
    return default


# API Configuration (Supports any OpenAI-compatible provider)
API_KEY = (
    os.environ.get("OPENAI_API_KEY", "").strip()
    or os.environ.get("API_KEY", "").strip()
    or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or os.environ.get("QWEN_API_KEY", "").strip()
)
BASE_URL = get_env_str("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = get_env_str("MODEL_NAME", "deepseek-flash")

# Abaqus local GUI event bridge configuration
ABAQUS_HOST = get_env_str("ABAQUS_BRIDGE_HOST", "127.0.0.1")
ABAQUS_PORT = get_env_int("ABAQUS_BRIDGE_PORT", 23457)
ABAQUS_TIMEOUT = get_env_float("ABAQUS_TIMEOUT", 30.0)
CONFIRM_BEFORE_EXEC = get_env_bool("CONFIRM_BEFORE_EXEC", False)

# Abaqus Launcher Configuration
ABAQUS_COMMAND = get_env_str("ABAQUS_COMMAND", "abaqus")
AUTO_LAUNCH_ABAQUS = get_env_bool("AUTO_LAUNCH_ABAQUS", True)
WORKSPACE_DIR_STR = get_env_str("WORKSPACE_DIR", "./workspace")
WORKSPACE_DIR = (ROOT_DIR / WORKSPACE_DIR_STR).resolve() if not Path(WORKSPACE_DIR_STR).is_absolute() else Path(WORKSPACE_DIR_STR)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

# File-based IPC Directory
_ipc_override = get_env_str("ABAQUS_IPC_DIR", "")
IPC_DIR = ((ROOT_DIR / _ipc_override).resolve() if _ipc_override else WORKSPACE_DIR / ".ipc")
IPC_DIR.mkdir(parents=True, exist_ok=True)

# Agent Execution Limits
MAX_AGENT_STEPS = get_env_int("MAX_AGENT_STEPS", 15)

# Paths
DOCS_DIR = BASE_DIR / "docs"
COMMAND_REF_PATH = DOCS_DIR / "abaqus_api_reference.md"
COMMAND_INDEX_PATH = DOCS_DIR / "command_index.txt"
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
