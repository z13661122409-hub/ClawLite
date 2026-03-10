# ClawLite

OpenClaw-style agent dashboard MVP: plan -> tools -> audit log -> human approval.

## Features
- Web dashboard to create jobs and inspect audit logs
- Tool execution with policy + approval gates
- SQLite audit ledger
- Sandbox-limited file access

## Setup
```bash
pip install -r requirements.txt
copy .env.example .env
# edit .env
python app.py
