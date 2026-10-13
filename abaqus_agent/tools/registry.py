# -*- coding: utf-8 -*-
"""Tool registry and OpenAI schema exporter for Abaqus AI Agent."""

import json
from typing import Any, Callable, Dict, List
from abaqus_agent.tools.abaqus_tools import (
    tool_abaqus_diff,
    tool_abaqus_get_workspace,
    tool_abaqus_ping,
    tool_abaqus_read_gui_log,
    tool_abaqus_run_script,
    tool_abaqus_search_docs,
    tool_abaqus_snapshot,
    tool_abaqus_status,
)

OPENAI_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "abaqus_get_workspace",
            "description": "Get the current working directory of the live Abaqus instance, list available .cae / .inp / .odb files, and locate abaqus.rpy.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abaqus_read_gui_log",
            "description": "Read the most recent Python commands recorded in the active Abaqus instance's abaqus.rpy file. Call this to check what operations the human engineer recently performed in the CAE GUI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tail_lines": {
                        "type": "integer",
                        "description": "Number of recent command lines to read. Default is 30."
                    },
                    "filter_views": {
                        "type": "boolean",
                        "description": "Whether to filter out repetitive viewport camera rotation and pan lines. Default is true."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abaqus_run_script",
            "description": "Execute a finite Python 3.10 script in the Abaqus 2025 kernel. Known API/context errors are rejected before execution as API_PRECHECK. Other failures may leave partial model changes; inspect before repair.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The complete Python script to execute in Abaqus kernel. Supports multi-line commands."
                    }
                },
                "required": ["script"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abaqus_search_docs",
            "description": "Search the curated Abaqus 2025 knowledge base for API ownership, usage examples, pitfalls, and official source links. Supports English API names and Chinese keywords. Missing entries do not prove an API is invalid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "The class, module, or keyword to search (e.g. 'Material', 'seedPart', 'HomogeneousSolidSection', 'StaticStep', 'Job', 'openOdb')."
                    },
                    "detail": {
                        "type": "boolean",
                        "description": "Whether to return usage examples, cautions and official sources. Default is true."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matching commands to return. Default is 5."
                    }
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abaqus_status",
            "description": "Query current model summary statistics (counts of parts, materials, sections, steps, loads, boundary conditions, mesh elements and nodes) from the active Abaqus session.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abaqus_snapshot",
            "description": "Capture a full model state snapshot (parts, materials, sections, steps, loads, boundary conditions, mesh, jobs) to detect modifications.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "Optional tag name for the snapshot (e.g. 'before_mesh', 'after_loads')."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abaqus_diff",
            "description": "Compare two model snapshots to detect changes made by the user in the Abaqus GUI or by previous scripts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "snap_a": {
                        "type": "string",
                        "description": "Filename or tag of first snapshot (baseline). If omitted, uses second-to-last snapshot."
                    },
                    "snap_b": {
                        "type": "string",
                        "description": "Filename or tag of second snapshot (current). If omitted, uses latest snapshot."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abaqus_ping",
            "description": "Check whether Abaqus bridge server is online and responding.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]

# Function mapping
TOOL_HANDLERS: Dict[str, Callable[..., str]] = {
    "abaqus_get_workspace": tool_abaqus_get_workspace,
    "abaqus_read_gui_log": tool_abaqus_read_gui_log,
    "abaqus_run_script": tool_abaqus_run_script,
    "abaqus_search_docs": tool_abaqus_search_docs,
    "abaqus_status": tool_abaqus_status,
    "abaqus_snapshot": tool_abaqus_snapshot,
    "abaqus_diff": tool_abaqus_diff,
    "abaqus_ping": tool_abaqus_ping,
}


def dispatch_tool_call(tool_name: str, arguments_json: str) -> str:
    """Parse tool arguments and dispatch to corresponding handler."""
    if tool_name not in TOOL_HANDLERS:
        return f"Error: Tool '{tool_name}' is not registered."

    handler = TOOL_HANDLERS[tool_name]
    try:
        kwargs = json.loads(arguments_json) if arguments_json.strip() else {}
        return handler(**kwargs)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON arguments: {e}"
    except TypeError as e:
        return f"Error invoking {tool_name}: {e}"
    except Exception as e:
        return f"Tool execution error ({tool_name}): {e}"
