from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class ToolDecision:
    allow: bool
    needs_approval: bool
    reason: str

# 你可以把风险分级做得更细：read_only / write / external_side_effect / admin
TOOL_RISK = {
    "web_fetch": "read_only",
    "read_file": "read_only",
    "write_file": "write",
    "append_file": "write",
    "sha256_file": "read_only",
}

def decide(tool_name: str, args: Dict[str, Any]) -> ToolDecision:
    risk = TOOL_RISK.get(tool_name, "unknown")

    if risk == "unknown":
        return ToolDecision(False, True, "Unknown tool; blocked by default")

    if risk == "read_only":
        return ToolDecision(True, False, "Read-only tool")

    if risk == "write":
        # 写文件默认需要你审批（你以后也可以改为部分路径自动放行）
        return ToolDecision(True, True, "Write action requires approval")

    return ToolDecision(False, True, "Blocked by policy")