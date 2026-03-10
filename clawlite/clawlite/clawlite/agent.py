import json, uuid
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from .policy import decide
from .tools import TOOLS

ALLOWED_TOOLS = {"web_fetch", "read_file", "write_file", "append_file", "sha256_file"}

SYSTEM_PROMPT = """你是一个严谨的任务规划器。你必须输出严格 JSON（不要 markdown）。
目标：把用户的目标拆成可执行步骤。
规则：
- 只允许使用这些工具：web_fetch, read_file, write_file, append_file, sha256_file
- 工具调用用 step: {"type":"tool","name":"...","args":{...}}
- 说明文字用 {"type":"note","content":"..."}
- 最终总结用 {"type":"final","content":"..."}
- 尽量先用 read_only 工具；写文件要明确写到 sandbox 里，例如 reports/xxx.txt
输出格式：
{"goal":"...","steps":[...]}
"""

@dataclass
class Step:
    type: str
    name: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    content: Optional[str] = None

@dataclass
class Plan:
    goal: str
    steps: List[Step]

def _coerce_step(obj: Dict[str, Any]) -> Optional[Step]:
    if not isinstance(obj, dict):
        return None
    t = obj.get("type")
    if t not in {"tool", "note", "final"}:
        return None
    if t == "tool":
        name = obj.get("name")
        if not isinstance(name, str) or name not in ALLOWED_TOOLS:
            return None
        args = obj.get("args") or {}
        if not isinstance(args, dict):
            return None
        return Step(type="tool", name=name, args=args)
    if t in {"note", "final"}:
        content = obj.get("content")
        if not isinstance(content, str):
            return None
        return Step(type=t, content=content)
    return None

def _try_parse_plan(text: str, goal: str) -> Plan:
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("plan root not dict")
        g = obj.get("goal") or goal
        raw_steps = obj.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("steps empty")
        steps: List[Step] = []
        for s in raw_steps:
            step = _coerce_step(s)
            if step is None:
                raise ValueError("bad step schema")
            steps.append(step)
        return Plan(goal=str(g), steps=steps)
    except Exception:
        # 兜底：给一个最小计划
        return Plan(
            goal=goal,
            steps=[
                Step(type="note", content="LLM 输出无法解析，已采用兜底流程。"),
                Step(type="final", content="请把目标再具体一点（例如给出文件路径/想要的输出格式），或检查 LLM 返回格式。"),
            ],
        )

class AgentRunner:
    def __init__(self, llm, db, sandbox_dir: str, fetch_allowlist: str):
        self.llm = llm
        self.db = db
        self.sandbox_dir = sandbox_dir
        self.fetch_allowlist = fetch_allowlist

    def start(self, goal: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        state = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": goal},
            ],
            "plan": None,
            "step_index": 0,
            "pending": None,
            "final": None,
        }
        self.db.create_job(job_id, goal, state)
        self.db.add_audit(job_id, "job_created", {"goal": goal})
        return job_id

    def run_until_pause_or_done(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        state = job["state"]
        goal = job["goal"]

        if state["plan"] is None:
            self.db.update_job(job_id, status="planning")
            raw = self.llm.chat(state["messages"])
            self.db.add_audit(job_id, "llm_plan_raw", {"text": raw[:20000]})
            plan = _try_parse_plan(raw, goal)
            state["plan"] = {
                "goal": plan.goal,
                "steps": [s.__dict__ for s in plan.steps],
            }
            state["step_index"] = 0
            self.db.add_audit(job_id, "plan_parsed", {"plan": state["plan"]})
            self.db.update_job(job_id, state=state, status="running")

        plan_obj = state["plan"]
        plan = Plan(
            goal=plan_obj["goal"],
            steps=[Step(**s) for s in plan_obj["steps"]],
        )
        i = int(state.get("step_index", 0))

        while i < len(plan.steps):
            step = plan.steps[i]

            if step.type == "note":
                self.db.add_audit(job_id, "note", {"content": step.content})
                i += 1
                state["step_index"] = i
                self.db.update_job(job_id, state=state, status="running")
                continue

            if step.type == "final":
                state["final"] = step.content
                self.db.add_audit(job_id, "final", {"content": step.content})
                self.db.update_job(job_id, state=state, status="done", result={"final": step.content})
                return

            if step.type == "tool":
                tool_name = step.name or ""
                args = dict(step.args or {})

                if tool_name in {"read_file", "write_file", "append_file", "sha256_file"}:
                    args = {"sandbox_dir": self.sandbox_dir, **args}
                if tool_name == "web_fetch":
                    args = {"allowlist_csv": self.fetch_allowlist, **args}

                decision = decide(tool_name, args)
                self.db.add_audit(job_id, "tool_decision", {"tool": tool_name, "args": args, "decision": decision.__dict__})

                if not decision.allow:
                    msg = f"工具 {tool_name} 被策略阻止：{decision.reason}"
                    self.db.add_audit(job_id, "blocked", {"message": msg})
                    self.db.update_job(job_id, status="failed", result={"error": msg})
                    return

                if decision.needs_approval:
                    state["pending"] = {"step": step.__dict__, "tool_args": args, "reason": decision.reason}
                    state["step_index"] = i
                    self.db.add_audit(job_id, "awaiting_approval", state["pending"])
                    self.db.update_job(job_id, state=state, status="awaiting_approval")
                    return

                if tool_name not in TOOLS:
                    msg = f"未知工具：{tool_name}"
                    self.db.add_audit(job_id, "failed", {"message": msg})
                    self.db.update_job(job_id, status="failed", result={"error": msg})
                    return

                try:
                    self.db.add_audit(job_id, "tool_call", {"tool": tool_name, "args": args})
                    out = TOOLS[tool_name](**args)
                    self.db.add_audit(job_id, "tool_result", {"tool": tool_name, "result": out})
                except Exception as e:
                    self.db.add_audit(job_id, "tool_error", {"tool": tool_name, "error": str(e)})
                    self.db.update_job(job_id, status="failed", result={"error": f"{tool_name} failed: {e}"})
                    return

                state["messages"].append({"role": "assistant", "content": f"[tool:{tool_name}] args={json.dumps(args, ensure_ascii=False)}"})
                state["messages"].append({"role": "user", "content": f"[tool_result] {json.dumps(out, ensure_ascii=False)[:20000]}"})

                i += 1
                state["step_index"] = i
                self.db.update_job(job_id, state=state, status="running")
                continue

            self.db.add_audit(job_id, "failed", {"message": f"Unknown step type: {step.type}"})
            self.db.update_job(job_id, status="failed", result={"error": f"Unknown step type: {step.type}"})
            return

        self.db.update_job(job_id, status="done", result={"final": state.get("final") or "任务结束（无 final 步骤）。"})

    def approve_and_continue(self, job_id: str, approved: bool) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        state = job["state"]
        if job["status"] != "awaiting_approval" or not state.get("pending"):
            return

        pending = state["pending"]
        step = Step(**pending["step"])
        tool_name = step.name or ""
        args = pending["tool_args"]

        self.db.add_audit(job_id, "approval_decision", {"approved": approved, "pending": pending})

        if not approved:
            msg = f"用户拒绝执行工具 {tool_name}：{pending.get('reason')}"
            self.db.add_audit(job_id, "rejected", {"message": msg})
            state["pending"] = None
            self.db.update_job(job_id, state=state, status="failed", result={"error": msg})
            return

        try:
            self.db.add_audit(job_id, "tool_call", {"tool": tool_name, "args": args, "approved": True})
            out = TOOLS[tool_name](**args)
            self.db.add_audit(job_id, "tool_result", {"tool": tool_name, "result": out, "approved": True})
        except Exception as e:
            self.db.add_audit(job_id, "tool_error", {"tool": tool_name, "error": str(e), "approved": True})
            self.db.update_job(job_id, status="failed", result={"error": f"{tool_name} failed: {e}"})
            return

        state["messages"].append({"role": "assistant", "content": f"[tool:{tool_name}] args={json.dumps(args, ensure_ascii=False)}"})
        state["messages"].append({"role": "user", "content": f"[tool_result] {json.dumps(out, ensure_ascii=False)[:20000]}"})
        state["pending"] = None
        state["step_index"] = int(state["step_index"]) + 1
        self.db.update_job(job_id, state=state, status="running")