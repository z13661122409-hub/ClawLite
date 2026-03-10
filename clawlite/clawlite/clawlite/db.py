import os, json, sqlite3, time
from typing import Any, Dict, Optional

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  status TEXT NOT NULL,
  goal TEXT NOT NULL,
  state_json TEXT NOT NULL,
  result_json TEXT
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""

class DB:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with sqlite3.connect(self.path) as cx:
            cx.executescript(SCHEMA)

    def _now(self) -> int:
        return int(time.time())

    def create_job(self, job_id: str, goal: str, state: Dict[str, Any]) -> None:
        now = self._now()
        with sqlite3.connect(self.path) as cx:
            cx.execute(
                "INSERT INTO jobs(id, created_at, updated_at, status, goal, state_json) VALUES(?,?,?,?,?,?)",
                (job_id, now, now, "queued", goal, json.dumps(state, ensure_ascii=False)),
            )

    def update_job(self, job_id: str, *, status: Optional[str]=None,
                   state: Optional[Dict[str, Any]]=None,
                   result: Optional[Dict[str, Any]]=None) -> None:
        now = self._now()
        with sqlite3.connect(self.path) as cx:
            if status is not None:
                cx.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (status, now, job_id))
            if state is not None:
                cx.execute("UPDATE jobs SET state_json=?, updated_at=? WHERE id=?", (json.dumps(state, ensure_ascii=False), now, job_id))
            if result is not None:
                cx.execute("UPDATE jobs SET result_json=?, updated_at=? WHERE id=?", (json.dumps(result, ensure_ascii=False), now, job_id))

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.path) as cx:
            row = cx.execute("SELECT id, created_at, updated_at, status, goal, state_json, result_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "created_at": row[1],
            "updated_at": row[2],
            "status": row[3],
            "goal": row[4],
            "state": json.loads(row[5]),
            "result": json.loads(row[6]) if row[6] else None,
        }

    def list_jobs(self, limit: int = 20):
        with sqlite3.connect(self.path) as cx:
            rows = cx.execute("SELECT id, created_at, updated_at, status, goal FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "created_at": r[1], "updated_at": r[2], "status": r[3], "goal": r[4]} for r in rows]

    def add_audit(self, job_id: str, kind: str, payload: Dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as cx:
            cx.execute(
                "INSERT INTO audit(job_id, ts, kind, payload_json) VALUES(?,?,?,?)",
                (job_id, self._now(), kind, json.dumps(payload, ensure_ascii=False)),
            )

    def get_audit(self, job_id: str, limit: int = 200):
        with sqlite3.connect(self.path) as cx:
            rows = cx.execute(
                "SELECT ts, kind, payload_json FROM audit WHERE job_id=? ORDER BY id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [{"ts": r[0], "kind": r[1], "payload": json.loads(r[2])} for r in rows][::-1]