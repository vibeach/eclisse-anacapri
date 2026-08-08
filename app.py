import os
import time
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

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


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


@app.route("/")
def home():
    return render_template("index.html", sent=request.args.get("sent") == "1")


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
            "index.html",
            error="Serve almeno nome + un contatto (telefono o email).",
            form={"name": name, "num_people": num_people, "allergies": allergies, "contact": contact, "notes": notes},
        )

    now = int(time.time())
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO eclisse_rsvps (name, num_people, allergies, contact, notes, created_at, ip, user_agent)
                    VALUES (:n, :np, :al, :co, :nt, :ca, :ip, :ua)"""),
            {
                "n": name,
                "np": num_people,
                "al": allergies or None,
                "co": contact,
                "nt": notes or None,
                "ca": now,
                "ip": ip,
                "ua": request.headers.get("User-Agent", "")[:200],
            },
        )
    return redirect(url_for("home") + "?sent=1")


@app.route("/admin")
def admin():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        abort(403)
    with engine.begin() as c:
        eclisse_rsvps = list(c.execute(text(
            "SELECT id, name, num_people, allergies, contact, notes, created_at, ip FROM eclisse_rsvps ORDER BY created_at DESC"
        )).mappings())
    total_people = sum(r["num_people"] or 0 for r in eclisse_rsvps)
    return render_template(
        "admin.html",
        eclisse_rsvps=[
            {
                "id": r["id"],
                "name": r["name"],
                "num_people": r["num_people"],
                "allergies": r["allergies"] or "",
                "contact": r["contact"] or "",
                "notes": r["notes"] or "",
                "when": datetime.fromtimestamp(r["created_at"]).strftime("%d/%m %H:%M"),
                "ip": r["ip"] or "",
            }
            for r in eclisse_rsvps
        ],
        total_eclisse_rsvps=len(eclisse_rsvps),
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


@app.route("/healthz")
def healthz():
    return {"ok": True}


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8799)))
