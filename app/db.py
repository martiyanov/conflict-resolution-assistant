import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY,
  creator_user_id INTEGER NOT NULL,
  participant_a_user_id INTEGER,
  participant_b_user_id INTEGER,
  title TEXT,
  conflict_period TEXT,
  join_code TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  summary_a TEXT,
  summary_b TEXT,
  common_ground TEXT,
  differences TEXT,
  options_text TEXT
);

CREATE TABLE IF NOT EXISTS intake_answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  question_key TEXT NOT NULL,
  answer_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


async def init_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        cursor = await db.execute("PRAGMA table_info(cases)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "conflict_period" not in columns:
            await db.execute("ALTER TABLE cases ADD COLUMN conflict_period TEXT")
        await db.commit()
