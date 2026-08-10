import os
import json
import time
import hashlib
import random
import shutil
from datetime import datetime
from flask import Flask, request, redirect, render_template, session, url_for, abort, Response
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_KEY = os.environ.get("ECLISSE_ADMIN_KEY", "ECL-A26C1PR8")
SECRET_KEY = os.environ.get("ECLISSE_SECRET_KEY", "Nkq-3wR2LmVzTx7A-eclisse-anacapri")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+pg8000://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)
if not DATABASE_URL:
    DATABASE_URL = f"sqlite:///{os.path.join(APP_DIR, 'eclisse.db')}"

engine = create_engine(DATABASE_URL, poolclass=NullPool, future=True)
IS_PG = DATABASE_URL.startswith("postgresql")

CORRECT_ANSWER = "Flippi"
NICKNAME_OPTIONS = ["Supermario", "Flippi", "Batman", "Bomber"]

PAPPO_ROUND_SIZE = 6
PAPPO_MAX_ERRORS = 5
PAPPO_DIR = os.path.join(APP_DIR, "static", "pappo")
PAPPO_IMG_DIR = os.path.join(PAPPO_DIR, "img")
PAPPO_MANIFEST = os.path.join(PAPPO_DIR, "manifest.json")


def build_pappo_manifest():
    src_yes = os.path.join(PAPPO_DIR, "yes")
    src_no = os.path.join(PAPPO_DIR, "no")
    if not (os.path.isdir(src_yes) and os.path.isdir(src_no)):
        return {}
    os.makedirs(PAPPO_IMG_DIR, exist_ok=True)
    manifest = {}
    for kind, src in (("yes", src_yes), ("no", src_no)):
        for fn in sorted(os.listdir(src)):
            if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            src_path = os.path.join(src, fn)
            digest = hashlib.sha256(f"{kind}:{fn}".encode()).hexdigest()[:16]
            ext = os.path.splitext(fn)[1].lower().replace(".jpeg", ".jpg")
            out_name = f"{digest}{ext}"
            out_path = os.path.join(PAPPO_IMG_DIR, out_name)
            if not os.path.exists(out_path) or os.path.getmtime(out_path) < os.path.getmtime(src_path):
                shutil.copy2(src_path, out_path)
            manifest[out_name] = (kind == "yes")
    with open(PAPPO_MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


PAPPO_MAP = build_pappo_manifest()
PAPPO_YES = [k for k, v in PAPPO_MAP.items() if v]
PAPPO_NO = [k for k, v in PAPPO_MAP.items() if not v]

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.context_processor
def inject_url_prefix():
    return {"URL_PREFIX": ""}


def init_db():
    id_col = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with engine.begin() as c:
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS eclisse_rsvps (
                id {id_col},
                name TEXT NOT NULL,
                num_people INTEGER NOT NULL DEFAULT 1,
                allergies TEXT,
                contact TEXT,
                notes TEXT,
                created_at BIGINT NOT NULL,
                ip TEXT,
                user_agent TEXT
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS eclisse_attempts (
                id {id_col},
                name TEXT,
                answer TEXT,
                correct INTEGER,
                created_at BIGINT NOT NULL,
                ip TEXT
            )
        """))


# ─── Game flow: / → /quiz → /pappo → /invito ─────────────────────────────────

@app.route("/")
def home():
    fail = request.args.get("fail") == "1"
    session.clear()
    return render_template("index.html", fail=fail)


@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            return render_template("index.html", error="Scrivi il tuo nome per continuare.")
        session["name"] = name[:80]
        return render_template("quiz.html", name=session["name"], options=NICKNAME_OPTIONS)
    if "name" not in session:
        return redirect(url_for("home"))
    return render_template("quiz.html", name=session["name"], options=NICKNAME_OPTIONS)


@app.route("/verify", methods=["POST"])
def verify():
    name = session.get("name")
    if not name:
        return redirect(url_for("home"))
    answer = (request.form.get("nickname") or "").strip()
    correct = answer == CORRECT_ANSWER
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()
    with engine.begin() as c:
        c.execute(
            text("INSERT INTO eclisse_attempts (name, answer, correct, created_at, ip) VALUES (:n, :a, :ok, :t, :ip)"),
            {"n": name, "a": answer, "ok": 1 if correct else 0, "t": int(time.time()), "ip": ip},
        )
    if not correct:
        return render_template(
            "quiz.html", name=name, options=NICKNAME_OPTIONS,
            error="Risposta sbagliata. Riprova (o chiedi ad Ale)."
        )
    session["verified"] = True
    return redirect(url_for("pappo"))


def _new_pappo_round():
    queue = list(PAPPO_YES) + list(PAPPO_NO)
    random.shuffle(queue)
    return queue


@app.route("/pappo", methods=["GET", "POST"])
def pappo():
    if not session.get("verified"):
        return redirect(url_for("home"))
    if session.get("pappo_passed"):
        return redirect(url_for("invito"))
    if not PAPPO_MAP:
        session["pappo_passed"] = True
        return redirect(url_for("invito"))

    if "pappo_queue" not in session:
        session["pappo_queue"] = _new_pappo_round()
        session["pappo_idx"] = 0
        session["pappo_errors"] = 0

    error_msg = None

    if request.method == "POST":
        answer = request.form.get("answer")
        current_img = request.form.get("img")
        if answer not in ("yes", "no") or current_img not in PAPPO_MAP:
            return redirect(url_for("pappo"))
        expected_yes = PAPPO_MAP[current_img]
        got_yes = answer == "yes"
        if got_yes == expected_yes:
            session["pappo_idx"] = session.get("pappo_idx", 0) + 1
            if session["pappo_idx"] >= len(session["pappo_queue"]):
                session["pappo_passed"] = True
                session.pop("pappo_queue", None)
                session.pop("pappo_idx", None)
                session.pop("pappo_errors", None)
                return redirect(url_for("invito"))
            return redirect(url_for("pappo"))
        else:
            session["pappo_errors"] = session.get("pappo_errors", 0) + 1
            if session["pappo_errors"] >= PAPPO_MAX_ERRORS:
                session.clear()
                return redirect(url_for("home") + "?fail=1")
            remaining = PAPPO_MAX_ERRORS - session["pappo_errors"]
            error_msg = f"Guarda meglio e riprova.. hai ancora {remaining} tentativ{'o' if remaining == 1 else 'i'}."

    idx = session.get("pappo_idx", 0)
    queue = session.get("pappo_queue", [])
    if idx >= len(queue):
        session["pappo_passed"] = True
        return redirect(url_for("invito"))
    current_img = queue[idx]

    return render_template(
        "pappo.html",
        name=session.get("name"),
        img=current_img,
        current=idx + 1,
        total=len(queue),
        errors=session.get("pappo_errors", 0),
        max_errors=PAPPO_MAX_ERRORS,
        error=error_msg,
    )


@app.route("/invito", methods=["GET"])
def invito():
    """Direct invite page — accessible bypassing the game, or landing after game."""
    return render_template("invito.html", sent=request.args.get("sent") == "1")


@app.route("/rsvp", methods=["POST"])
def rsvp():
    name = (request.form.get("name") or "").strip()[:80]
    try:
        num_people = max(1, min(20, int(request.form.get("num_people") or "1")))
    except ValueError:
        num_people = 1
    allergies = (request.form.get("allergies") or "").strip()[:400]
    contact = (request.form.get("contact") or "").strip()[:200]
    notes = (request.form.get("notes") or "").strip()[:600]

    if not name or not contact:
        return render_template(
            "invito.html",
            error="Serve almeno nome + un contatto (telefono o email).",
            form={"name": name, "num_people": num_people, "allergies": allergies, "contact": contact, "notes": notes},
        )

    now = int(time.time())
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO eclisse_rsvps (name, num_people, allergies, contact, notes, created_at, ip, user_agent)
                    VALUES (:n, :np, :al, :co, :nt, :ca, :ip, :ua)"""),
            {"n": name, "np": num_people, "al": allergies or None, "co": contact,
             "nt": notes or None, "ca": now, "ip": ip,
             "ua": request.headers.get("User-Agent", "")[:200]},
        )
    return redirect(url_for("invito") + "?sent=1")


@app.route("/admin")
def admin():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        abort(403)
    with engine.begin() as c:
        rsvps_rows = list(c.execute(text(
            "SELECT id, name, num_people, allergies, contact, notes, created_at, ip FROM eclisse_rsvps ORDER BY created_at DESC"
        )).mappings())
        attempts_rows = list(c.execute(text(
            "SELECT name, answer, correct, created_at, ip FROM eclisse_attempts ORDER BY created_at DESC LIMIT 200"
        )).mappings())
    total_people = sum(r["num_people"] or 0 for r in rsvps_rows)
    return render_template(
        "admin.html",
        rsvps=[
            {"id": r["id"], "name": r["name"], "num_people": r["num_people"],
             "allergies": r["allergies"] or "", "contact": r["contact"] or "",
             "notes": r["notes"] or "",
             "when": datetime.fromtimestamp(r["created_at"]).strftime("%d/%m %H:%M"),
             "ip": r["ip"] or ""}
            for r in rsvps_rows
        ],
        attempts=[
            {"name": a["name"] or "", "answer": a["answer"] or "",
             "correct": bool(a["correct"]),
             "when": datetime.fromtimestamp(a["created_at"]).strftime("%d/%m %H:%M"),
             "ip": a["ip"] or ""}
            for a in attempts_rows
        ],
        total_rsvps=len(rsvps_rows),
        total_people=total_people,
        admin_key=key,
    )


@app.route("/admin/export.csv")
def admin_export():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        abort(403)
    with engine.begin() as c:
        rows = list(c.execute(text(
            "SELECT id, name, num_people, allergies, contact, notes, created_at, ip FROM eclisse_rsvps ORDER BY created_at DESC"
        )).mappings())
    lines = ["id,name,num_people,allergies,contact,notes,created_at,ip"]
    for r in rows:
        def q(v):
            return (v or "").replace('"', "'")
        when = datetime.fromtimestamp(r["created_at"]).isoformat()
        lines.append(f'{r["id"]},"{q(r["name"])}",{r["num_people"]},"{q(r["allergies"])}","{q(r["contact"])}","{q(r["notes"])}",{when},{r["ip"] or ""}')
    return Response("\n".join(lines), mimetype="text/csv")


@app.route("/admin/delete/<int:rsvp_id>", methods=["POST", "GET"])
def admin_delete(rsvp_id):
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        abort(403)
    with engine.begin() as c:
        c.execute(text("DELETE FROM eclisse_rsvps WHERE id = :id"), {"id": rsvp_id})
    return redirect(url_for("admin", key=key))


@app.route("/healthz")
def healthz():
    return {"ok": True}


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8799)))
