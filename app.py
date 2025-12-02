from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
import random
from datetime import timedelta
import os

app = Flask(__name__)
# Use environment variables in production; fall back to development defaults
app.secret_key = os.environ.get("SECRET_KEY", "secret123")
app.permanent_session_lifetime = timedelta(days=30)  # Sessions last 30 days if "Remember Me" is checked


# ----------------------------
# Helper function: Database
# ----------------------------
DB_PATH = os.environ.get("DATABASE_PATH", "database.db")


def ensure_schema(db_path: str):
    """Create minimal tables if they don't exist so the app can boot on first deploy."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # Users table (very simple; passwords are plaintext in this project)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                password TEXT
            )
            """
        )
        # Vocabulary table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vocabulary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hanzi TEXT NOT NULL,
                pinyin TEXT,
                meaning TEXT,
                hsk_level TEXT
            )
            """
        )
        # Progress table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                vocab_id INTEGER,
                learned INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(vocab_id) REFERENCES vocabulary(id)
            )
            """
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Ensure schema at startup (idempotent). If using a mounted disk, ensure the mount path exists.
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
ensure_schema(DB_PATH)


def get_db():
    # Allow overriding the database path via env for persistent disks in deployment
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# -------------------------------------------------
# Optional auto-seed (first boot) for public deploy
# -------------------------------------------------
def seed_if_empty(csv_path: str = "hsk_sample_data.csv"):
    """Populate vocabulary table from a CSV if it's empty.
    Safe to call every startup: only runs when COUNT(*) == 0 and file exists.
    Expected headers: hanzi, pinyin, meaning, hsk_level (flexible: hsk_level optional)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        count = cur.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0]
        if count > 0:
            return  # already seeded
        if not os.path.isfile(csv_path):
            return  # no seed file available
        import csv
        rows_added = 0
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                hanzi = (row.get("hanzi") or "").strip()
                if not hanzi:
                    continue
                pinyin = (row.get("pinyin") or "").strip()
                meaning = (row.get("meaning") or "").strip()
                level = (row.get("hsk_level") or row.get("level") or "").strip()
                cur.execute(
                    "INSERT INTO vocabulary (hanzi, pinyin, meaning, hsk_level) VALUES (?, ?, ?, ?)",
                    (hanzi, pinyin, meaning, level)
                )
                rows_added += 1
        conn.commit()
    except Exception as e:
        print("Seed failed:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass

# Enable auto-seed if environment variable AUTO_SEED is set to '1'
if os.environ.get("AUTO_SEED", "1") == "1":  # default on for first public deploy
    seed_if_empty()


# ----------------------------
# Welcome Page
# ----------------------------
@app.route("/")
def welcome():
    return render_template("welcome.html")


# ----------------------------
# Sign Up
# ----------------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        conn.execute("INSERT INTO users (email, password) VALUES (?, ?)", (email, password))
        conn.commit()
        conn.close()

        return redirect("/login")

    return render_template("signup.html")


# ----------------------------
# Login
# ----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        remember = request.form.get("remember")  # Get "Remember Me" checkbox value

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=? AND password=?", (email, password)).fetchone()

        if user:
            # If "Remember Me" is checked, make session permanent (lasts 30 days)
            if remember == "yes":
                session.permanent = True
            else:
                session.permanent = False
            
            session["user_id"] = user["id"]
            conn.close()
            return redirect("/home")

        conn.close()
        return render_template("login.html", error="Invalid email or password. Please try again.")

    return render_template("login.html")


# ----------------------------
# Home Page
# ----------------------------
@app.route("/home")
def home():
    if "user_id" not in session:
        return redirect("/login")
    return render_template("home.html")


# ----------------------------
# HSK Selection Page
# ----------------------------
@app.route("/hsk")
def hsk_select():
    conn = get_db()
    # Get vocab count for each HSK level
    levels = []
    for i in range(1, 7):
        count = conn.execute(
            "SELECT COUNT(*) as count FROM vocabulary WHERE hsk_level=?",
            (i,)
        ).fetchone()[0]
        levels.append({'level': i, 'count': count})
    conn.close()
    return render_template("hsk_selection.html", levels=levels)


# ----------------------------
# Vocabulary List for Each HSK Level
# ----------------------------
@app.route("/hsk/<int:level>")
def vocab_list(level):
    user_id = session.get("user_id")
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    per_page = 10

    conn = get_db()
    if search:
        q = f"%{search.lower()}%"
        if user_id:
            # Join with progress to show learned status
            vocab = conn.execute("""
                SELECT v.*, 
                       CASE WHEN p.vocab_id IS NOT NULL THEN 1 ELSE 0 END as learned
                FROM vocabulary v
                LEFT JOIN progress p ON v.id = p.vocab_id AND p.user_id = ?
                WHERE v.hsk_level = ? 
                AND (lower(v.hanzi) LIKE ? OR lower(v.pinyin) LIKE ? OR lower(v.meaning) LIKE ?)
            """, (user_id, level, q, q, q)).fetchall()
        else:
            vocab = conn.execute(
                "SELECT *, 0 as learned FROM vocabulary WHERE hsk_level=? AND (lower(hanzi) LIKE ? OR lower(pinyin) LIKE ? OR lower(meaning) LIKE ?)",
                (level, q, q, q)
            ).fetchall()
    else:
        if user_id:
            # Join with progress to show learned status
            vocab = conn.execute("""
                SELECT v.*, 
                       CASE WHEN p.vocab_id IS NOT NULL THEN 1 ELSE 0 END as learned
                FROM vocabulary v
                LEFT JOIN progress p ON v.id = p.vocab_id AND p.user_id = ?
                WHERE v.hsk_level = ?
            """, (user_id, level)).fetchall()
        else:
            vocab = conn.execute(
                "SELECT *, 0 as learned FROM vocabulary WHERE hsk_level=?",
                (level,)
            ).fetchall()

    conn.close()
    
    # Pagination
    total = len(vocab)
    total_pages = (total + per_page - 1) // per_page  # Ceiling division
    start = (page - 1) * per_page
    end = start + per_page
    vocab_page = vocab[start:end]
    
    return render_template("vocab_list.html", 
                         vocab_list=vocab_page, 
                         level=level, 
                         search=search,
                         page=page,
                         total_pages=total_pages)


# ----------------------------
# Flashcards
# ----------------------------
@app.route("/hsk/<int:level>/flashcards", methods=["GET", "POST"])
def flashcards(level):
    user_id = session.get("user_id")

    conn = get_db()

    # Mark vocab as learned
    if request.method == "POST":
        vocab_id = request.form.get("learned")
        # Only record progress if user is logged in and vocab_id is valid
        if user_id and vocab_id:
            # avoid duplicate progress records
            exists = conn.execute(
                "SELECT id FROM progress WHERE user_id=? AND vocab_id=?",
                (user_id, vocab_id)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO progress (user_id, vocab_id, learned) VALUES (?, ?, ?)",
                    (user_id, vocab_id, 1)
                )
                conn.commit()
            # after marking learned, redirect to the next card
            conn.close()
            return redirect(url_for('flashcards', level=level, next=vocab_id))
    # Get navigation args
    next_anchor = request.args.get("next")
    prev_anchor = request.args.get("prev")

    if next_anchor:
        # show the first vocab with id > next_anchor
        vocab = conn.execute(
            "SELECT * FROM vocabulary WHERE hsk_level=? AND id > ? ORDER BY id ASC",
            (level, next_anchor)
        ).fetchone()
    elif prev_anchor:
        # show the first vocab with id < prev_anchor (reverse order)
        vocab = conn.execute(
            "SELECT * FROM vocabulary WHERE hsk_level=? AND id < ? ORDER BY id DESC",
            (level, prev_anchor)
        ).fetchone()
    else:
        # initial card (smallest id)
        vocab = conn.execute(
            "SELECT * FROM vocabulary WHERE hsk_level=? ORDER BY id ASC",
            (level,)
        ).fetchone()

    # compute prev/next ids for current vocab
    prev_id = None
    next_id = None
    if vocab:
        cur_id = vocab['id']
        prev_row = conn.execute(
            "SELECT id FROM vocabulary WHERE hsk_level=? AND id < ? ORDER BY id DESC LIMIT 1",
            (level, cur_id)
        ).fetchone()
        next_row = conn.execute(
            "SELECT id FROM vocabulary WHERE hsk_level=? AND id > ? ORDER BY id ASC LIMIT 1",
            (level, cur_id)
        ).fetchone()
        if prev_row:
            prev_id = prev_row['id']
        if next_row:
            next_id = next_row['id']

    conn.close()
    return render_template("flashcards.html", vocab=vocab, level=level, prev_id=prev_id, next_id=next_id)


# ----------------------------
# Logout
# ----------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('welcome'))

# ----------------------------
# Start Quiz (choose number of questions)
# ----------------------------
@app.route("/hsk/<int:level>/quiz/start", methods=["GET", "POST"])
def quiz_start(level):
    if request.method == "POST":
        num = int(request.form["num"])
        if num % 5 != 0:
            return "Please enter a number that is a multiple of 5."

        conn = get_db()
        vocab_list = conn.execute("SELECT id FROM vocabulary WHERE hsk_level=?", (level,)).fetchall()
        conn.close()

        if num > len(vocab_list):
            return f"Not enough words! Max allowed: {len(vocab_list)}"

        vocab_ids = random.sample([v["id"] for v in vocab_list], num)

        session["quiz_words"] = vocab_ids
        session["quiz_current"] = 0
        session["quiz_correct"] = 0
        session["quiz_level"] = level
        session["quiz_total"] = num

        return redirect(url_for("quiz_question", level=level))

    # GET request — show start quiz page
    return render_template("quiz_start.html", level=level)


@app.route("/hsk/<int:level>/quiz")
def quiz(level):
    # Backwards-compatible route used by templates that link to /hsk/<level>/quiz
    # Redirect users to the quiz start page which initializes session state.
    return redirect(url_for("quiz_start", level=level))


# ----------------------------
# Show Question
# ----------------------------
@app.route("/hsk/<int:level>/quiz/question")
def quiz_question(level):
    words = session.get("quiz_words", [])
    current = session.get("quiz_current", 0)

    if not words:
        return redirect(url_for("quiz", level=level))  # ← redirect to quiz start

    if current >= len(words):
        return redirect(url_for("quiz_result", level=level))

    question_id = words[current]

    conn = get_db()
    question = conn.execute("SELECT * FROM vocabulary WHERE id=?", (question_id,)).fetchone()
    vocab_list = conn.execute("SELECT * FROM vocabulary WHERE hsk_level=?", (level,)).fetchall()
    conn.close()

    options = random.sample([v for v in vocab_list if v["id"] != question_id], 3)
    options.append(question)
    random.shuffle(options)

    return render_template(
        "quiz_question.html",
        question=question,
        options=options,
        level=level,
        total=len(words),
        current=current,
        feedback=None
    )

# ----------------------------
# Submit Answer
# ----------------------------
@app.route("/hsk/<int:level>/quiz/submit", methods=["POST"])
def quiz_submit(level):
    answer_id = int(request.form["answer"])
    correct_id = int(request.form["correct_id"])

    words = session.get("quiz_words", [])
    current = session.get("quiz_current", 0)

    if answer_id == correct_id:
        feedback = "Correct!"
        session["quiz_correct"] += 1
    else:
        conn = get_db()
        correct_word = conn.execute("SELECT * FROM vocabulary WHERE id=?", (correct_id,)).fetchone()
        conn.close()
        feedback = f"Wrong! Correct answer: {correct_word['hanzi']}"

    # Store feedback in session
    session["quiz_feedback"] = feedback

    return redirect(url_for("quiz_feedback", level=level))


# ----------------------------
# Feedback Page
# ----------------------------
@app.route("/hsk/<int:level>/quiz/feedback")
def quiz_feedback(level):
    feedback = session.get("quiz_feedback", "")
    current = session.get("quiz_current", 0)
    total = len(session.get("quiz_words", []))
    return render_template(
        "quiz_question.html",
        question=None,
        options=None,
        level=level,
        feedback=feedback,
        total=total,
        current=current
    )


# ----------------------------
# Next Question
# ----------------------------
@app.route("/hsk/<int:level>/quiz/next", methods=["POST"])
def quiz_next(level):
    session["quiz_current"] += 1
    current = session.get("quiz_current", 0)
    words = session.get("quiz_words", [])

    if current >= len(words):
        return redirect(url_for("quiz_result", level=level))

    question_id = words[current]

    conn = get_db()
    question = conn.execute("SELECT * FROM vocabulary WHERE id=?", (question_id,)).fetchone()
    vocab_list = conn.execute("SELECT * FROM vocabulary WHERE hsk_level=?", (level,)).fetchall()
    conn.close()

    options = random.sample([v for v in vocab_list if v["id"] != question_id], 3)
    options.append(question)
    random.shuffle(options)

    return render_template(
        "quiz_question.html",
        question=question,
        options=options,
        level=level,
        total=len(words),
        current=current,
        feedback=None
    )


# ----------------------------
# Quiz Result
# ----------------------------
@app.route("/hsk/<int:level>/quiz/result")
def quiz_result(level):
    total = session.get("quiz_total", 0)
    correct = session.get("quiz_correct", 0)

    # Clear quiz session
    session.pop("quiz_words", None)
    session.pop("quiz_current", None)
    session.pop("quiz_correct", None)
    session.pop("quiz_feedback", None)
    session.pop("quiz_total", None)
    session.pop("quiz_level", None)

    return render_template("quiz_result.html", total=total, correct=correct, level=level)


# ----------------------------
# Progress Page
# ----------------------------
@app.route("/progress")
def progress():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    conn = get_db()

    # Show all HSK levels 1-6
    progress = {}
    for lvl in range(1, 7):
        total = conn.execute("SELECT COUNT(*) FROM vocabulary WHERE hsk_level=?", (lvl,)).fetchone()[0]
        learned = conn.execute(
            "SELECT COUNT(DISTINCT p.vocab_id) FROM progress p JOIN vocabulary v ON p.vocab_id=v.id WHERE p.user_id=? AND v.hsk_level=? AND p.learned=1",
            (user_id, lvl)
        ).fetchone()[0]

        percent = int((learned / total) * 100) if total > 0 else 0
        progress[lvl] = percent

    conn.close()
    return render_template("progress.html", progress=progress)


if __name__ == "__main__":
    # Enable host/port overrides for local/docker runs; debug only for local script run
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
