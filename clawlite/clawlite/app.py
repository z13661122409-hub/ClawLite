import os, threading
from flask import Flask, render_template, request, jsonify, abort
from dotenv import load_dotenv

from clawlite.db import DB
from clawlite.llm_client import LLMClient
from clawlite.agent import AgentRunner

load_dotenv()

app = Flask(__name__)

DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "change_me")
SANDBOX_DIR = os.getenv("SANDBOX_DIR", "./sandbox")
FETCH_ALLOWLIST = os.getenv("FETCH_ALLOWLIST", "")
DB_PATH = os.getenv("DB_PATH", "./clawlite.db")

os.makedirs(SANDBOX_DIR, exist_ok=True)

db = DB(DB_PATH)
llm = LLMClient(
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    api_key=os.getenv("LLM_API_KEY", ""),
    model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
)
agent = AgentRunner(llm=llm, db=db, sandbox_dir=SANDBOX_DIR, fetch_allowlist=FETCH_ALLOWLIST)

def auth():
    token = request.headers.get("X-Token") or request.args.get("token")
    if token != DASHBOARD_TOKEN:
        abort(401)

def run_bg(job_id: str):
    try:
        agent.run_until_pause_or_done(job_id)
    except Exception as e:
        db.add_audit(job_id, "crash", {"error": str(e)})
        db.update_job(job_id, status="failed", result={"error": f"crash: {e}"})

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/jobs")
def api_jobs():
    auth()
    return jsonify({"jobs": db.list_jobs(50)})

@app.post("/api/jobs")
def api_create_job():
    auth()
    data = request.get_json(force=True)
    goal = (data.get("goal") or "").strip()
    if not goal:
        return jsonify({"error": "goal required"}), 400
    job_id = agent.start(goal)
    db.update_job(job_id, status="running")
    threading.Thread(target=run_bg, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id})

@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    auth()
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)

@app.get("/api/jobs/<job_id>/audit")
def api_audit(job_id):
    auth()
    return jsonify({"audit": db.get_audit(job_id, 300)})

@app.post("/api/jobs/<job_id>/approve")
def api_approve(job_id):
    auth()
    data = request.get_json(force=True)
    approved = bool(data.get("approved"))
    # 先执行审批动作，再继续跑
    agent.approve_and_continue(job_id, approved=approved)
    if approved:
        threading.Thread(target=run_bg, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)