import sqlite3, os, hashlib, json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "campusnexus.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        department TEXT NOT NULL,
        year INTEGER NOT NULL,
        bio TEXT DEFAULT '',
        github TEXT DEFAULT '',
        portfolio TEXT DEFAULT '',
        available INTEGER DEFAULT 1,
        looking_for TEXT DEFAULT '',
        consent_given INTEGER DEFAULT 0,
        consent_timestamp DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        skill TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS interests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        interest TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        required_skills TEXT DEFAULT '',
        status TEXT DEFAULT 'recruiting',
        is_anonymous INTEGER DEFAULT 0,
        anon_alias TEXT DEFAULT '',
        idea_hash TEXT DEFAULT '',
        idea_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE SET NULL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS project_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        user_id INTEGER,
        field_changed TEXT NOT NULL,
        old_value TEXT DEFAULT '',
        new_value TEXT DEFAULT '',
        changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        message TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        consent_agreed INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(project_id) REFERENCES projects(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        organizer TEXT DEFAULT '',
        event_date TEXT NOT NULL,
        tags TEXT DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER NOT NULL,
        to_user INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(from_user) REFERENCES users(id),
        FOREIGN KEY(to_user) REFERENCES users(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reported_by INTEGER NOT NULL,
        target_type TEXT NOT NULL,
        target_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(reported_by) REFERENCES users(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        detail TEXT DEFAULT '',
        ip_address TEXT DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.commit()
    conn.close()
    print("Database initialized.")


def generate_idea_hash(title, description, owner_id):
    content = f"{title}|{description}|{owner_id}|{datetime.utcnow().isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()


def compute_safety_score(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return 0

    score = 0
    breakdown = {}

    # Profile completeness (25 pts)
    profile_pts = 0
    if user["bio"] and len(user["bio"]) > 20:
        profile_pts += 10
    if user["github"]:
        profile_pts += 8
    if user["portfolio"]:
        profile_pts += 7
    score += profile_pts
    breakdown["Profile Completeness"] = profile_pts

    # Skills (20 pts)
    skill_count = conn.execute("SELECT COUNT(*) FROM skills WHERE user_id=?", (user_id,)).fetchone()[0]
    skill_pts = min(skill_count * 4, 20)
    score += skill_pts
    breakdown["Skills Listed"] = skill_pts

    # Consent given (20 pts)
    consent_pts = 20 if user["consent_given"] else 0
    score += consent_pts
    breakdown["Code of Conduct Signed"] = consent_pts

    # Activity: audit log (20 pts)
    activity = conn.execute("SELECT COUNT(*) FROM audit_log WHERE user_id=?", (user_id,)).fetchone()[0]
    activity_pts = min(activity * 2, 20)
    score += activity_pts
    breakdown["Platform Activity"] = activity_pts

    # Connections (15 pts)
    conn_count = conn.execute(
        "SELECT COUNT(*) FROM connections WHERE (from_user=? OR to_user=?) AND status='accepted'",
        (user_id, user_id)
    ).fetchone()[0]
    conn_pts = min(conn_count * 3, 15)
    score += conn_pts
    breakdown["Verified Connections"] = conn_pts

    conn.close()
    return {"score": min(score, 100), "breakdown": breakdown}


def seed_demo_data():
    import bcrypt
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing > 0:
        conn.close()
        return

    demo_users = [
        ("Aditi Sharma", "aditi@university.edu", "AI & Data Science", 3,
         "Kaggle enthusiast and ML researcher. Love building things that matter.",
         ["Machine Learning", "Python", "TensorFlow", "React"],
         ["AI/ML", "Research", "Hackathons"]),
        ("Rahul Menon", "rahul@university.edu", "CSE", 4,
         "Full-stack dev | open source contributor | hackathon veteran",
         ["Node.js", "React", "Flask", "UI/UX Design"],
         ["Web Dev", "Open Source", "Startups"]),
        ("Priya Nair", "priya@university.edu", "ECE", 2,
         "IoT and embedded systems lover. Building smart campus solutions.",
         ["IoT", "Arduino", "Python", "C++"],
         ["Robotics", "IoT", "Hardware"]),
        ("Kiran Raj", "kiran@university.edu", "IT", 3,
         "Blockchain and Web3 builder. Interested in DeFi and NFT platforms.",
         ["Solidity", "Blockchain", "Figma", "JavaScript"],
         ["Web3", "Design", "Crypto"]),
        ("Meera Pillai", "meera@university.edu", "CSE", 4,
         "NLP researcher | LLM tinkerer. Working on multilingual models.",
         ["NLP", "LangChain", "Gemini API", "Python"],
         ["AI/ML", "Research", "LLMs"]),
    ]

    hashed_pw = bcrypt.hashpw(b"Demo@1234", bcrypt.gensalt()).decode()

    for name, email, dept, year, bio, skills, interests in demo_users:
        c = conn.execute(
            "INSERT INTO users(name,email,password,department,year,bio,available,consent_given,consent_timestamp) VALUES(?,?,?,?,?,?,1,1,CURRENT_TIMESTAMP)",
            (name, email, hashed_pw, dept, year, bio)
        )
        uid = c.lastrowid
        for s in skills:
            conn.execute("INSERT INTO skills(user_id,skill) VALUES(?,?)", (uid, s))
        for i in interests:
            conn.execute("INSERT INTO interests(user_id,interest) VALUES(?,?)", (uid, i))

    # Demo projects (using - instead of em dash)
    demo_projects = [
        (1, "CampusFood - Smart Canteen AI",
         "AI-powered canteen ordering with demand prediction and waste reduction.",
         "Python, ML, React, Flask", False),
        (2, "StudySync - Peer Learning Platform",
         "Real-time collaborative whiteboard with AI-powered doubt resolution.",
         "React, Node.js, WebSockets, AI", False),
        (3, "EcoTrack - Campus Carbon Monitor",
         "IoT sensor network tracking campus energy usage and carbon footprint.",
         "IoT, Python, Arduino, Data Viz", False),
        (None, "Safe Campus Reporting Tool",
         "Anonymous platform for reporting campus harassment with verified follow-up.",
         "Flask, React, Security", True),
    ]

    for owner, title, desc, skills, anon in demo_projects:
        idea_hash = generate_idea_hash(title, desc, owner or 0)
        alias = "Anonymous Innovator" if anon else ""
        conn.execute(
            "INSERT INTO projects(owner_id,title,description,required_skills,is_anonymous,anon_alias,idea_hash) VALUES(?,?,?,?,?,?,?)",
            (owner, title, desc, skills, 1 if anon else 0, alias, idea_hash)
        )

    demo_events = [
        ("VibHackathon 2026", "24hr AI hackathon", "ACM Chapter", "2026-03-08", "Hackathon,AI/ML"),
        ("Intro to LangChain and RAG", "Workshop on LLMs", "IEEE CS", "2026-03-12", "Workshop,LLMs"),
        ("Open Source Sprint", "GitHub contribution day", "GitHub Campus", "2026-03-22", "Open Source"),
        ("Startup Weekend Campus", "Build a startup in 54hr", "E-Cell", "2026-04-02", "Startup,Entrepreneurship"),
    ]

    for title, desc, org, date, tags in demo_events:
        conn.execute(
            "INSERT INTO events(title,description,organizer,event_date,tags) VALUES(?,?,?,?,?)",
            (title, desc, org, date, tags)
        )

    conn.commit()
    conn.close()
    print("Demo data seeded.")
