import os, re, bcrypt, json, base64
from datetime import timedelta, datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from models import get_db, init_db, seed_demo_data, generate_idea_hash, compute_safety_score
from gemini import (ai_match_students, ai_recommend_projects, ai_team_builder,
                    ai_skill_gap_analyzer, ai_academic_dna, ai_project_success_predictor)

load_dotenv()

app = Flask(__name__)
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "campusnexus-secret-2026")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)

CORS(app, origins=["http://localhost:5500", "http://127.0.0.1:5500",
                   "http://localhost:3000", "http://127.0.0.1:3000", "null"],
     supports_credentials=True)

jwt = JWTManager(app)
limiter = Limiter(key_func=get_remote_address, app=app,
                  default_limits=["10000 per day", "1000 per hour"],
                  storage_uri="memory://")

UNIVERSITY_DOMAINS = ["university.edu", "college.edu", "ac.in", "edu.in", "gmail.com"]

# ── Helpers ──────────────────────────────────────────────────

def valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False
    return any(email.split("@")[1].lower().endswith(d) for d in UNIVERSITY_DOMAINS)

def clean(text, maxlen=500):
    if not text:
        return ""
    return re.sub(r'[<>"\';]', '', str(text))[:maxlen].strip()

def audit(user_id, action, detail=""):
    try:
        ip = request.remote_addr or "unknown"
        conn = get_db()
        conn.execute(
            "INSERT INTO audit_log(user_id,action,detail,ip_address) VALUES(?,?,?,?)",
            (user_id, action, detail[:200], ip)
        )
        conn.commit()
        conn.close()
    except:
        pass

def user_dict(user_id):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        conn.close()
        return None
    skills = [r["skill"] for r in conn.execute("SELECT skill FROM skills WHERE user_id=?", (user_id,)).fetchall()]
    interests = [r["interest"] for r in conn.execute("SELECT interest FROM interests WHERE user_id=?", (user_id,)).fetchall()]
    conn.close()
    return {
        "id": u["id"], "name": u["name"], "email": u["email"],
        "department": u["department"], "year": u["year"],
        "bio": u["bio"], "github": u["github"], "portfolio": u["portfolio"],
        "available": bool(u["available"]), "looking_for": u["looking_for"],
        "consent_given": bool(u["consent_given"]),
        "skills": skills, "interests": interests, "created_at": u["created_at"]
    }

# ── AUTH ─────────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
@limiter.limit("10 per hour")
def register():
    d = request.get_json(silent=True) or {}
    name = clean(d.get("name", ""), 100)
    email = clean(d.get("email", ""), 150).lower()
    password = d.get("password", "")
    department = clean(d.get("department", ""), 100)
    year = d.get("year", 1)
    skills = d.get("skills", [])
    interests = d.get("interests", [])
    consent = d.get("consent_given", False)

    if not all([name, email, password, department]):
        return jsonify({"error": "All fields are required."}), 400
    if not valid_email(email):
        return jsonify({"error": "Please use a valid university email."}), 400
    if len(password) < 8 or not re.search(r'[A-Z]', password) or not re.search(r'\d', password):
        return jsonify({"error": "Password needs 8+ chars, 1 uppercase, 1 number."}), 400
    if not isinstance(year, int) or not 1 <= year <= 5:
        return jsonify({"error": "Year must be 1-5."}), 400
    if not consent:
        return jsonify({"error": "You must agree to the Code of Conduct to join."}), 400

    conn = get_db()
    if conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        conn.close()
        return jsonify({"error": "Email already registered."}), 409

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    c = conn.execute(
        "INSERT INTO users(name,email,password,department,year,consent_given,consent_timestamp) VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP)",
        (name, email, hashed, department, year)
    )
    uid = c.lastrowid
    for s in skills[:20]:
        conn.execute("INSERT INTO skills(user_id,skill) VALUES(?,?)", (uid, clean(s, 100)))
    for i in interests[:20]:
        conn.execute("INSERT INTO interests(user_id,interest) VALUES(?,?)", (uid, clean(i, 100)))
    conn.commit()
    conn.close()

    audit(uid, "REGISTER", f"New user: {name}")
    token = create_access_token(identity=str(uid))
    return jsonify({"message": "Welcome to CampusNexus!", "token": token, "user": user_dict(uid)})


@app.route("/api/auth/login", methods=["POST"])
@limiter.limit("20 per hour")
def login():
    d = request.get_json(silent=True) or {}
    email = clean(d.get("email", ""), 150).lower()
    password = d.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password required."}), 400

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()

    if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
        return jsonify({"error": "Invalid email or password."}), 401

    audit(user["id"], "LOGIN")
    token = create_access_token(identity=str(user["id"]))
    return jsonify({"message": "Login successful!", "token": token, "user": user_dict(user["id"])})

# ── PROFILE ──────────────────────────────────────────────────

@app.route("/api/profile/me", methods=["GET"])
@jwt_required()
def get_my_profile():
    uid = int(get_jwt_identity())
    p = user_dict(uid)
    if not p:
        return jsonify({"error": "Not found."}), 404
    return jsonify(p), 200


@app.route("/api/profile/update", methods=["PUT"])
@jwt_required()
def update_profile():
    uid = int(get_jwt_identity())
    d = request.get_json(silent=True) or {}
    bio = clean(d.get("bio", ""), 500)
    github = clean(d.get("github", ""), 200)
    portfolio = clean(d.get("portfolio", ""), 200)
    available = 1 if d.get("available", True) else 0
    looking_for = clean(d.get("looking_for", ""), 200)
    skills = d.get("skills", [])
    interests = d.get("interests", [])

    conn = get_db()
    conn.execute(
        "UPDATE users SET bio=?,github=?,portfolio=?,available=?,looking_for=? WHERE id=?",
        (bio, github, portfolio, available, looking_for, uid)
    )
    if skills:
        conn.execute("DELETE FROM skills WHERE user_id=?", (uid,))
        for s in skills[:20]:
            conn.execute("INSERT INTO skills(user_id,skill) VALUES(?,?)", (uid, clean(s, 100)))
    if interests:
        conn.execute("DELETE FROM interests WHERE user_id=?", (uid,))
        for i in interests[:20]:
            conn.execute("INSERT INTO interests(user_id,interest) VALUES(?,?)", (uid, clean(i, 100)))
    conn.commit()
    conn.close()

    audit(uid, "PROFILE_UPDATE")
    return jsonify({"message": "Profile updated!", "user": user_dict(uid)}), 200


@app.route("/api/users", methods=["GET"])
@jwt_required()
def get_all_users():
    uid = int(get_jwt_identity())
    conn = get_db()
    users = conn.execute("SELECT id FROM users WHERE id!=?", (uid,)).fetchall()
    conn.close()
    return jsonify([user_dict(u["id"]) for u in users]), 200


@app.route("/api/users/<int:tid>", methods=["GET"])
@jwt_required()
def get_user(tid):
    p = user_dict(tid)
    if not p:
        return jsonify({"error": "Not found."}), 404
    return jsonify(p), 200

# ── SAFETY SCORE ─────────────────────────────────────────────

@app.route("/api/safety-score/<int:uid>", methods=["GET"])
@jwt_required()
def get_safety_score(uid):
    result = compute_safety_score(uid)
    return jsonify(result), 200


@app.route("/api/safety-score/me", methods=["GET"])
@jwt_required()
def get_my_safety_score():
    uid = int(get_jwt_identity())
    result = compute_safety_score(uid)
    return jsonify(result), 200

# ── AUDIT LOG ────────────────────────────────────────────────

@app.route("/api/audit-log", methods=["GET"])
@jwt_required()
def get_audit_log():
    uid = int(get_jwt_identity())
    conn = get_db()
    logs = conn.execute(
        "SELECT * FROM audit_log WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (uid,)
    ).fetchall()
    conn.close()
    return jsonify([dict(l) for l in logs]), 200

# ── AI ROUTES ────────────────────────────────────────────────

@app.route("/api/ai/matches", methods=["GET"])
@jwt_required()
@limiter.limit("15 per hour")
def get_ai_matches():
    uid = int(get_jwt_identity())
    try:
        matches = ai_match_students(uid)
        audit(uid, "AI_MATCH", f"Found {len(matches)} matches")
        return jsonify({"matches": matches}), 200
    except ValueError as e:
        return jsonify({"error": str(e), "hint": "Set GEMINI_API_KEY in .env"}), 503
    except Exception as e:
        return jsonify({"error": "AI error", "detail": str(e)}), 500


@app.route("/api/ai/projects", methods=["GET"])
@jwt_required()
@limiter.limit("15 per hour")
def get_ai_projects():
    uid = int(get_jwt_identity())
    try:
        recs = ai_recommend_projects(uid)
        audit(uid, "AI_PROJECT_REC")
        return jsonify({"recommendations": recs}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "AI error", "detail": str(e)}), 500


@app.route("/api/ai/team-builder", methods=["POST"])
@jwt_required()
@limiter.limit("10 per hour")
def team_builder():
    uid = int(get_jwt_identity())
    d = request.get_json(silent=True) or {}
    query = clean(d.get("query", ""), 300)
    if not query:
        return jsonify({"error": "Query required."}), 400
    try:
        results = ai_team_builder(query, uid)
        audit(uid, "TEAM_BUILDER", query[:50])
        return jsonify({"results": results}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "AI error", "detail": str(e)}), 500


@app.route("/api/ai/skill-gap", methods=["POST"])
@jwt_required()
@limiter.limit("10 per hour")
def skill_gap():
    uid = int(get_jwt_identity())
    d = request.get_json(silent=True) or {}
    resume_text = clean(d.get("resume_text", ""), 5000)
    if not resume_text:
        return jsonify({"error": "Resume text required."}), 400
    try:
        result = ai_skill_gap_analyzer(resume_text, uid)
        audit(uid, "SKILL_GAP_ANALYSIS")
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "AI error", "detail": str(e)}), 500


@app.route("/api/ai/dna", methods=["GET"])
@jwt_required()
@limiter.limit("10 per hour")
def academic_dna():
    uid = int(get_jwt_identity())
    try:
        result = ai_academic_dna(uid)
        audit(uid, "ACADEMIC_DNA")
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "AI error", "detail": str(e)}), 500


@app.route("/api/ai/predict-team", methods=["POST"])
@jwt_required()
@limiter.limit("10 per hour")
def predict_team():
    uid = int(get_jwt_identity())
    d = request.get_json(silent=True) or {}
    project_id = d.get("project_id")
    applicant_ids = d.get("applicant_ids", [uid])
    if not project_id:
        return jsonify({"error": "project_id required."}), 400
    try:
        result = ai_project_success_predictor(project_id, applicant_ids)
        audit(uid, "TEAM_PREDICT", f"Project {project_id}")
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": "AI error", "detail": str(e)}), 500

# ── PROJECTS ─────────────────────────────────────────────────

@app.route("/api/projects", methods=["GET"])
@jwt_required()
def get_projects():
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*,
        CASE WHEN p.is_anonymous=1 THEN p.anon_alias ELSE u.name END as display_name
        FROM projects p LEFT JOIN users u ON p.owner_id=u.id
        ORDER BY p.created_at DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows]), 200


@app.route("/api/projects", methods=["POST"])
@jwt_required()
@limiter.limit("20 per hour")
def create_project():
    uid = int(get_jwt_identity())
    d = request.get_json(silent=True) or {}
    title = clean(d.get("title", ""), 150)
    desc = clean(d.get("description", ""), 1000)
    req_skills = clean(d.get("required_skills", ""), 300)
    is_anon = 1 if d.get("is_anonymous", False) else 0
    anon_alias = clean(d.get("anon_alias", "Anonymous Innovator"), 50) if is_anon else ""

    if not title or not desc:
        return jsonify({"error": "Title and description required."}), 400

    idea_hash = generate_idea_hash(title, desc, uid)
    conn = get_db()
    c = conn.execute(
        "INSERT INTO projects(owner_id,title,description,required_skills,is_anonymous,anon_alias,idea_hash) VALUES(?,?,?,?,?,?,?)",
        (None if is_anon else uid, title, desc, req_skills, is_anon, anon_alias, idea_hash)
    )
    pid = c.lastrowid
    conn.execute(
        "INSERT INTO project_versions(project_id,user_id,field_changed,new_value) VALUES(?,?,?,?)",
        (pid, uid if not is_anon else None, "created", title)
    )
    conn.commit()
    conn.close()

    audit(uid, "PROJECT_CREATED", f"{'[ANON] ' if is_anon else ''}{title[:40]}")
    return jsonify({
        "message": "Project posted!", "project_id": pid,
        "idea_hash": idea_hash, "idea_fingerprint": idea_hash[:16] + "..."
    }), 201


@app.route("/api/projects/<int:pid>/apply", methods=["POST"])
@jwt_required()
def apply_project(pid):
    uid = int(get_jwt_identity())
    d = request.get_json(silent=True) or {}
    msg = clean(d.get("message", ""), 500)
    consent = d.get("consent_agreed", False)

    if not consent:
        return jsonify({"error": "You must agree to the collaboration terms."}), 400

    conn = get_db()
    if conn.execute("SELECT id FROM applications WHERE project_id=? AND user_id=?", (pid, uid)).fetchone():
        conn.close()
        return jsonify({"error": "Already applied."}), 409

    conn.execute(
        "INSERT INTO applications(project_id,user_id,message,consent_agreed) VALUES(?,?,?,1)",
        (pid, uid, msg)
    )
    conn.commit()
    conn.close()

    audit(uid, "APPLIED_PROJECT", f"Project {pid}")
    return jsonify({"message": "Application sent!"}), 201


@app.route("/api/projects/<int:pid>/versions", methods=["GET"])
@jwt_required()
def project_versions(pid):
    conn = get_db()
    versions = conn.execute("""
        SELECT pv.*,
        CASE WHEN u.name IS NULL THEN 'Anonymous' ELSE u.name END as editor_name
        FROM project_versions pv LEFT JOIN users u ON pv.user_id=u.id
        WHERE pv.project_id=? ORDER BY pv.changed_at DESC
    """, (pid,)).fetchall()
    conn.close()
    return jsonify([dict(v) for v in versions]), 200

# ── FLAGS / REPORTING ────────────────────────────────────────

@app.route("/api/flag", methods=["POST"])
@jwt_required()
@limiter.limit("20 per hour")
def flag_content():
    uid = int(get_jwt_identity())
    d = request.get_json(silent=True) or {}
    target_type = clean(d.get("target_type", ""), 50)
    target_id = d.get("target_id")
    reason = clean(d.get("reason", ""), 300)

    if not all([target_type, target_id, reason]):
        return jsonify({"error": "target_type, target_id, reason required."}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO flags(reported_by,target_type,target_id,reason) VALUES(?,?,?,?)",
        (uid, target_type, target_id, reason)
    )
    conn.commit()
    conn.close()

    audit(uid, "FLAG_SUBMITTED", f"{target_type}:{target_id} - {reason[:50]}")
    return jsonify({"message": "Report submitted. Our team will review it."}), 201

# ── EVENTS ───────────────────────────────────────────────────

@app.route("/api/events", methods=["GET"])
@jwt_required()
def get_events():
    conn = get_db()
    events = conn.execute("SELECT * FROM events ORDER BY event_date ASC").fetchall()
    conn.close()
    return jsonify([dict(e) for e in events]), 200

# ── CONNECTIONS ──────────────────────────────────────────────

@app.route("/api/connect/<int:tid>", methods=["POST"])
@jwt_required()
def send_connect(tid):
    uid = int(get_jwt_identity())
    if uid == tid:
        return jsonify({"error": "Cannot connect with yourself."}), 400

    conn = get_db()
    if conn.execute("SELECT id FROM connections WHERE from_user=? AND to_user=?", (uid, tid)).fetchone():
        conn.close()
        return jsonify({"error": "Already sent."}), 409

    conn.execute("INSERT INTO connections(from_user,to_user) VALUES(?,?)", (uid, tid))
    conn.commit()
    conn.close()

    audit(uid, "CONNECT_REQUEST", f"To user {tid}")
    return jsonify({"message": "Connection request sent!"}), 201

# ── HEALTH ───────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "app": "CampusNexus API v2",
        "timestamp": datetime.utcnow().isoformat()
    })

if __name__ == "__main__":
    init_db()
    seed_demo_data()
    print("\n🚀 CampusNexus v2 running → http://localhost:5000")
    print("🤖 AI Features: Match Engine | Project Recommender | Team Builder | Skill Gap | DNA | Predictor")
    app.run(debug=True, port=5000)
