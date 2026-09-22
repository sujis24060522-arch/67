"""
코딩무대 백엔드 (Flask + Supabase Postgres)

DB 연결 문자열은 환경변수 DATABASE_URL 하나로 관리해.
로컬 실행:
    pip install -r requirements.txt
    export DATABASE_URL="postgresql://postgres:[비밀번호]@[project-ref].supabase.co:5432/postgres"
    export FLASK_SECRET_KEY="원하는-랜덤-문자열"
    python app.py

Render 배포:
    - 이 저장소를 Web Service로 연결
    - Build Command: pip install -r requirements.txt
    - Start Command: gunicorn app:app --bind 0.0.0.0:$PORT   (Procfile에 이미 있음)
    - Environment 탭에서 DATABASE_URL, FLASK_SECRET_KEY 를 등록 (코드에 절대 하드코딩하지 말 것)

Supabase 커넥션 문자열 주의:
    - Supabase 대시보드의 "Connection string" > URI 를 그대로 DATABASE_URL에 넣으면 돼.
    - Render 같은 서버리스/컨테이너 환경에서는 IPv4 전용인 경우가 많아서,
      Supabase가 주는 "Connection pooling"(포트 6543, pgbouncer) 문자열을 쓰는 걸 권장해.
      (직접 연결용 5432 포트가 IPv6 전용으로 나오면 Render에서 연결이 안 될 수 있음)

주의: 이 코드는 학습/데모용 스타터야. 운영 전에 아래를 점검해줘.
    - FLASK_SECRET_KEY를 반드시 강한 랜덤 값으로 지정 (기본값 금지)
    - 프런트엔드를 다른 도메인(Claude 아티팩트 등)에서 호출한다면 세션 쿠키가
      크로스 사이트 쿠키로 취급돼서 브라우저에 따라 막힐 수 있음(특히 iOS Safari).
      같은 도메인에서 프런트를 서빙하거나, 쿠키 대신 토큰 기반 인증으로 바꾸는 걸 고려해줘.
    - 채점(문제를 실제로 풀었는지)은 여전히 브라우저 쪽 시뮬레이션 결과를 그대로 신뢰함.
"""

import os
import time
import uuid
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL 환경변수가 없어. Supabase 커넥션 문자열을 "
        "DATABASE_URL 환경변수에 넣고 다시 실행해줘."
    )

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

CATS = {"game", "math", "move"}
LEVELS = {1, 2, 3, 4, 5, 9}
GOAL_TYPES = {"position", "say", "free"}
GAME_IDS = {"jump", "catch", "lane"}


# =========================================================
# DB 연결
# =========================================================
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def dbexec(sql, params=()):
    cur = get_db().cursor()
    cur.execute(sql, params)
    return cur


def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username      TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            created_at    BIGINT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS problems (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            description   TEXT NOT NULL,
            category      TEXT NOT NULL,
            level         INTEGER NOT NULL,
            goal_type     TEXT NOT NULL,
            goal_x        DOUBLE PRECISION,
            goal_y        DOUBLE PRECISION,
            goal_tol      DOUBLE PRECISION,
            goal_text     TEXT,
            created_by    TEXT NOT NULL,
            created_at    BIGINT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS solved (
            username      TEXT NOT NULL,
            problem_id    TEXT NOT NULL,
            solved_at     BIGINT NOT NULL,
            PRIMARY KEY (username, problem_id)
        );

        CREATE TABLE IF NOT EXISTS highscores (
            username      TEXT NOT NULL,
            game_id       TEXT NOT NULL,
            score         INTEGER NOT NULL,
            updated_at    BIGINT NOT NULL,
            PRIMARY KEY (username, game_id)
        );
        """
    )

    cur.execute("SELECT 1 FROM users WHERE username = %s", ("admin",))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (%s, %s, 'admin', %s)",
            ("admin", generate_password_hash("admin1234"), int(time.time())),
        )

    cur.execute("SELECT COUNT(*) FROM problems")
    if cur.fetchone()[0] == 0:
        now = int(time.time())
        demo = [
            ("p1", "앞으로 100 만큼!", "움직임 블록으로 캐릭터를 오른쪽으로 이동시켜서 깃발 지점에 세워봐.",
             "game", 1, "position", 120, 0, 25, None),
            ("p2", "좌표 위에 정확히 서보기", "x = -100, y = 80 위치로 정확히 이동해봐. 좌표 격자를 참고해.",
             "math", 1, "position", -100, 80, 20, None),
            ("p3", "출발 신호 외치기", '말하기 블록을 사용해서 "출발!" 이라고 말해봐.',
             "move", 2, "say", None, None, None, "출발!"),
        ]
        cur.executemany(
            """INSERT INTO problems
               (id, title, description, category, level, goal_type, goal_x, goal_y, goal_tol, goal_text, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'admin', %s)""",
            [row + (now,) for row in demo],
        )

    conn.commit()
    cur.close()
    conn.close()


init_db()  # gunicorn이 app을 import할 때도 항상 실행되도록 모듈 최상위에서 호출


# =========================================================
# 인증 헬퍼
# =========================================================
def current_user():
    username = session.get("username")
    if not username:
        return None
    row = dbexec("SELECT username, role FROM users WHERE username = %s", (username,)).fetchone()
    return dict(row) if row else None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "로그인이 필요해."}), 401
        request.user = user
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "로그인이 필요해."}), 401
        if user["role"] != "admin":
            return jsonify({"error": "관리자만 할 수 있어."}), 403
        request.user = user
        return f(*args, **kwargs)
    return wrapper


def problem_to_dict(row):
    goal = {"type": row["goal_type"]}
    if row["goal_type"] == "position":
        goal.update(x=row["goal_x"], y=row["goal_y"], tol=row["goal_tol"])
    elif row["goal_type"] == "say":
        goal["text"] = row["goal_text"]
    return {
        "id": row["id"],
        "title": row["title"],
        "desc": row["description"],
        "category": row["category"],
        "level": row["level"],
        "goal": goal,
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
    }


# =========================================================
# 프런트엔드(coding-stage.html) 서빙
# =========================================================
@app.get("/")
def index():
    # app.py와 같은 폴더에 coding-stage.html을 두면 여기서 같은 오리진으로 서빙돼.
    # 이렇게 하면 프런트/백엔드가 같은 도메인이라 CORS나 크로스사이트 쿠키 문제가 아예 없음.
    return send_from_directory(app.root_path, "coding-stage.html")


# =========================================================
# 인증 API
# =========================================================
@app.post("/api/signup")
def signup():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not (3 <= len(username) <= 20) or not username.replace("_", "").isalnum():
        return jsonify({"error": "아이디는 영문/숫자/밑줄 3~20자로 입력해줘."}), 400
    if len(password) < 4:
        return jsonify({"error": "비밀번호는 4자 이상이어야 해."}), 400

    db = get_db()
    exists = dbexec("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone()
    if exists:
        return jsonify({"error": "이미 사용 중인 아이디야."}), 409

    dbexec(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (%s, %s, 'user', %s)",
        (username, generate_password_hash(password), int(time.time())),
    )
    db.commit()

    session["username"] = username
    return jsonify({"username": username, "role": "user"})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    row = dbexec(
        "SELECT username, password_hash, role FROM users WHERE username = %s", (username,)
    ).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "아이디 또는 비밀번호가 올바르지 않아."}), 401

    session["username"] = row["username"]
    return jsonify({"username": row["username"], "role": row["role"]})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"error": "로그인이 필요해."}), 401
    return jsonify(user)


# =========================================================
# 문제 API
# =========================================================
@app.get("/api/problems")
@login_required
def list_problems():
    rows = dbexec("SELECT * FROM problems ORDER BY created_at DESC").fetchall()
    return jsonify([problem_to_dict(r) for r in rows])


@app.post("/api/problems")
@admin_required
def create_problem():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    desc = (data.get("desc") or "").strip()
    category = data.get("category")
    level = data.get("level")
    goal = data.get("goal") or {}
    goal_type = goal.get("type")

    if not title or not desc:
        return jsonify({"error": "제목과 설명을 모두 입력해줘."}), 400
    if category not in CATS:
        return jsonify({"error": "카테고리 값이 올바르지 않아."}), 400
    if level not in LEVELS:
        return jsonify({"error": "난이도 값이 올바르지 않아."}), 400
    if goal_type not in GOAL_TYPES:
        return jsonify({"error": "채점 방식 값이 올바르지 않아."}), 400
    if goal_type == "say" and not (goal.get("text") or "").strip():
        return jsonify({"error": "목표 문장을 입력해줘."}), 400

    pid = "p_" + uuid.uuid4().hex[:10]
    now = int(time.time())
    db = get_db()
    dbexec(
        """INSERT INTO problems
           (id, title, description, category, level, goal_type, goal_x, goal_y, goal_tol, goal_text, created_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            pid, title, desc, category, level, goal_type,
            goal.get("x"), goal.get("y"), goal.get("tol"), goal.get("text"),
            request.user["username"], now,
        ),
    )
    db.commit()
    row = dbexec("SELECT * FROM problems WHERE id = %s", (pid,)).fetchone()
    return jsonify(problem_to_dict(row)), 201


@app.put("/api/problems/<problem_id>")
@admin_required
def update_problem(problem_id):
    existing = dbexec("SELECT * FROM problems WHERE id = %s", (problem_id,)).fetchone()
    if not existing:
        return jsonify({"error": "존재하지 않는 문제야."}), 404

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or existing["title"]).strip()
    desc = (data.get("desc") or existing["description"]).strip()
    category = data.get("category") or existing["category"]
    level = data.get("level") if data.get("level") is not None else existing["level"]
    goal = data.get("goal") or {
        "type": existing["goal_type"], "x": existing["goal_x"], "y": existing["goal_y"],
        "tol": existing["goal_tol"], "text": existing["goal_text"],
    }
    goal_type = goal.get("type")

    if category not in CATS or level not in LEVELS or goal_type not in GOAL_TYPES:
        return jsonify({"error": "값이 올바르지 않아."}), 400

    db = get_db()
    dbexec(
        """UPDATE problems SET title=%s, description=%s, category=%s, level=%s, goal_type=%s,
           goal_x=%s, goal_y=%s, goal_tol=%s, goal_text=%s WHERE id=%s""",
        (title, desc, category, level, goal_type,
         goal.get("x"), goal.get("y"), goal.get("tol"), goal.get("text"), problem_id),
    )
    db.commit()
    row = dbexec("SELECT * FROM problems WHERE id = %s", (problem_id,)).fetchone()
    return jsonify(problem_to_dict(row))


@app.delete("/api/problems/<problem_id>")
@admin_required
def delete_problem(problem_id):
    db = get_db()
    dbexec("DELETE FROM problems WHERE id = %s", (problem_id,))
    dbexec("DELETE FROM solved WHERE problem_id = %s", (problem_id,))
    db.commit()
    return jsonify({"ok": True})


# =========================================================
# 풀이 기록 API
# =========================================================
@app.get("/api/solved")
@login_required
def get_solved():
    rows = dbexec(
        "SELECT problem_id FROM solved WHERE username = %s", (request.user["username"],)
    ).fetchall()
    return jsonify([r["problem_id"] for r in rows])


@app.post("/api/solved/<problem_id>")
@login_required
def mark_solved(problem_id):
    exists = dbexec("SELECT 1 FROM problems WHERE id = %s", (problem_id,)).fetchone()
    if not exists:
        return jsonify({"error": "존재하지 않는 문제야."}), 404
    db = get_db()
    dbexec(
        """INSERT INTO solved (username, problem_id, solved_at) VALUES (%s, %s, %s)
           ON CONFLICT (username, problem_id) DO NOTHING""",
        (request.user["username"], problem_id, int(time.time())),
    )
    db.commit()
    return jsonify({"ok": True})


# =========================================================
# 최고 기록 API
# =========================================================
@app.get("/api/highscores")
@login_required
def get_highscores():
    rows = dbexec(
        "SELECT game_id, score FROM highscores WHERE username = %s", (request.user["username"],)
    ).fetchall()
    result = {g: 0 for g in GAME_IDS}
    result.update({r["game_id"]: r["score"] for r in rows})
    return jsonify(result)


@app.post("/api/highscores/<game_id>")
@login_required
def post_highscore(game_id):
    if game_id not in GAME_IDS:
        return jsonify({"error": "존재하지 않는 게임이야."}), 404
    data = request.get_json(silent=True) or {}
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "점수 값이 올바르지 않아."}), 400

    db = get_db()
    row = dbexec(
        "SELECT score FROM highscores WHERE username = %s AND game_id = %s",
        (request.user["username"], game_id),
    ).fetchone()

    if row is None or score > row["score"]:
        dbexec(
            """INSERT INTO highscores (username, game_id, score, updated_at) VALUES (%s, %s, %s, %s)
               ON CONFLICT (username, game_id) DO UPDATE SET score = EXCLUDED.score, updated_at = EXCLUDED.updated_at""",
            (request.user["username"], game_id, score, int(time.time())),
        )
        db.commit()
        return jsonify({"best": score, "isNew": True})

    return jsonify({"best": row["score"], "isNew": False})


@app.get("/api/health")
def health():
    dbexec("SELECT 1")
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
