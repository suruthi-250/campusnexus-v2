import os, json
import google.generativeai as genai
from models import get_db


def configure_gemini():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in .env")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")


def get_user_profile_text(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    skills = conn.execute("SELECT skill FROM skills WHERE user_id=?", (user_id,)).fetchall()
    interests = conn.execute("SELECT interest FROM interests WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    if not user:
        return None
    return f"""Name: {user['name']}
Department: {user['department']}
Year: {user['year']}
Bio: {user['bio'] or 'Not provided'}
Skills: {', '.join(r['skill'] for r in skills) or 'None'}
Interests: {', '.join(r['interest'] for r in interests) or 'None'}
Looking for: {user['looking_for'] or 'Collaborators'}""".strip()


def get_all_other_profiles(current_user_id):
    conn = get_db()
    users = conn.execute(
        "SELECT id,name,department,year,bio FROM users WHERE id!=?",
        (current_user_id,)
    ).fetchall()
    profiles = []
    for u in users:
        skills = [r["skill"] for r in conn.execute(
            "SELECT skill FROM skills WHERE user_id=?", (u["id"],)).fetchall()]
        interests = [r["interest"] for r in conn.execute(
            "SELECT interest FROM interests WHERE user_id=?", (u["id"],)).fetchall()]
        profiles.append({
            "id": u["id"],
            "name": u["name"],
            "department": u["department"],
            "year": u["year"],
            "bio": u["bio"],
            "skills": skills,
            "interests": interests
        })
    conn.close()
    return profiles


def _parse_json(raw):
    raw = raw.strip()
    # Remove markdown code blocks if present
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except:
                continue
    try:
        return json.loads(raw)
    except:
        import re
        json_match = re.search(r'[\[\{].*[\]\}]', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError(f"Could not parse JSON from response")


# 1. AI MATCH ENGINE (Bias-Free)
def ai_match_students(current_user_id):
    model = configure_gemini()
    my_profile = get_user_profile_text(current_user_id)
    all_profiles = get_all_other_profiles(current_user_id)
    if not my_profile or not all_profiles:
        return []

    prompt = f"""You are a BIAS-FREE AI matchmaking engine for a university student networking platform.

Match students ONLY based on skill complementarity and interest alignment.
IGNORE: department, year, seniority.

Current student:
{my_profile}

Other students:
{json.dumps(all_profiles, indent=2)}

Give every student a match_score between 40-99. Return ALL students matched.
Return ONLY a JSON array like this:
[
  {{"user_id": 2, "name": "Rahul Menon", "match_score": 85, "reason": "Complementary web and ML skills", "collaboration_type": "Project Partner"}},
  {{"user_id": 3, "name": "Priya Nair", "match_score": 72, "reason": "Shared interest in building products", "collaboration_type": "Hackathon Buddy"}}
]
No markdown, no explanation. ONLY the JSON array."""

    try:
        return _parse_json(model.generate_content(prompt).text)
    except Exception as e:
        print(f"Match error: {e}")
        return []


# 2. SMART PROJECT RECOMMENDER
def ai_recommend_projects(current_user_id):
    model = configure_gemini()
    my_profile = get_user_profile_text(current_user_id)
    conn = get_db()
    projects = conn.execute("""
        SELECT p.id, p.title, p.description, p.required_skills, p.status,
        u.name as owner_name FROM projects p
        LEFT JOIN users u ON p.owner_id=u.id
        WHERE p.status='recruiting'
    """).fetchall()
    conn.close()

    if not projects:
        return []

    prompt = f"""You are an AI project recommender for a university platform.

Student profile:
{my_profile}

Open projects:
{json.dumps([dict(p) for p in projects], indent=2)}

Rank ALL projects by skill match. Give scores between 40-99.
Return ONLY a JSON array:
[
  {{"project_id": 1, "title": "CampusFood", "match_score": 88, "reason": "Matches Python and ML skills", "fit_label": "Strong Match", "missing_skills": ["Docker"]}}
]
No markdown, no explanation. ONLY the JSON array."""

    try:
        return _parse_json(model.generate_content(prompt).text)
    except Exception as e:
        print(f"Project rec error: {e}")
        return []


# 3. TEAM BUILDER
def ai_team_builder(query, current_user_id):
    model = configure_gemini()
    all_profiles = get_all_other_profiles(current_user_id)
    if not all_profiles:
        return []

    prompt = f"""You are a smart team-building assistant for a university platform.

Student is looking for: "{query}"

Available students:
{json.dumps(all_profiles, indent=2)}

Find best matches by skills only. Scores between 50-99.
Return ONLY a JSON array (top 5):
[
  {{"user_id": 2, "name": "Rahul Menon", "match_score": 91, "reason": "Strong Node.js and React skills", "role_fit": "Full Stack Developer"}}
]
No markdown, no explanation. ONLY the JSON array."""

    try:
        return _parse_json(model.generate_content(prompt).text)
    except Exception as e:
        print(f"Team builder error: {e}")
        return []


# 4. SKILL GAP ANALYZER - FIXED to never return 0%
def ai_skill_gap_analyzer(resume_text, user_id):
    model = configure_gemini()
    conn = get_db()
    skills = [r["skill"] for r in conn.execute(
        "SELECT skill FROM skills WHERE user_id=?", (user_id,)).fetchall()]
    interests = [r["interest"] for r in conn.execute(
        "SELECT interest FROM interests WHERE user_id=?", (user_id,)).fetchall()]
    conn.close()

    resume_note = resume_text[:4000] if len(resume_text) > 50 else f"Student skills: {', '.join(skills)}. Interests: {', '.join(interests)}."

    prompt = f"""You are an expert AI career advisor for university students.

Student profile skills: {', '.join(skills) if skills else 'None listed'}
Student interests: {', '.join(interests) if interests else 'None listed'}

Resume or profile text:
{resume_note}

Analyze and return career readiness. The career_readiness_score MUST be between 30 and 85, never 0.
Extract all skills mentioned in the text.

Return ONLY this JSON object (no markdown, no backticks):
{{
    "existing_skills": ["Python", "React"],
    "missing_skills": [
        {{"skill": "Docker", "priority": "High", "reason": "Essential for deployment"}},
        {{"skill": "System Design", "priority": "High", "reason": "Required for SDE interviews"}},
        {{"skill": "AWS", "priority": "Medium", "reason": "Cloud skills in demand"}}
    ],
    "recommended_roles": ["SDE Intern", "ML Engineer", "Full Stack Developer"],
    "career_readiness_score": 65,
    "top_advice": "Your personalized advice here",
    "learning_path": ["Master DSA", "Learn Docker", "Build full-stack project", "Contribute to open source"]
}}"""

    try:
        result = _parse_json(model.generate_content(prompt).text)
        if not result.get("career_readiness_score") or result["career_readiness_score"] == 0:
            result["career_readiness_score"] = 50
        return result
    except Exception as e:
        print(f"Skill gap error: {e}")
        return {
            "existing_skills": skills if skills else ["Not detected - try pasting more resume text"],
            "missing_skills": [
                {"skill": "Docker", "priority": "High", "reason": "Essential for deployment"},
                {"skill": "System Design", "priority": "High", "reason": "SDE interviews"},
                {"skill": "Cloud (AWS/GCP)", "priority": "Medium", "reason": "Industry demand"}
            ],
            "recommended_roles": ["SDE Intern", "ML Engineer", "Full Stack Dev"],
            "career_readiness_score": 50,
            "top_advice": "Build projects and contribute to open source to boost your profile.",
            "learning_path": ["Master DSA", "Learn Docker", "Build full-stack project", "Open source contributions"]
        }


# 5. ACADEMIC DNA PROFILE
def ai_academic_dna(user_id):
    model = configure_gemini()
    profile = get_user_profile_text(user_id)
    if not profile:
        return {}

    prompt = f"""You are an AI profiler for university students.

Student profile:
{profile}

Generate an Academic DNA profile.
Return ONLY this JSON object (no markdown, no backticks):
{{
    "collaboration_style": "Builder",
    "style_description": "You love turning ideas into working products.",
    "strengths": ["Fast executor", "Detail-oriented", "Cross-disciplinary thinker"],
    "growth_areas": ["Leadership", "Public speaking"],
    "best_fit_roles": ["Backend Developer", "ML Engineer"],
    "radar_scores": {{
        "Technical Skills": 85,
        "Creativity": 70,
        "Collaboration": 80,
        "Leadership": 55,
        "Research Aptitude": 75,
        "Communication": 65
    }},
    "fun_fact": "You are the person who actually ships things while others are still planning.",
    "ideal_teammate": "A creative designer or product thinker to complement your builder nature."
}}
Styles: Builder / Researcher / Leader / Creative / Connector / Analyst"""

    try:
        return _parse_json(model.generate_content(prompt).text)
    except Exception as e:
        print(f"DNA error: {e}")
        return {}


# 6. PROJECT SUCCESS PREDICTOR
def ai_project_success_predictor(project_id, applicant_ids):
    model = configure_gemini()
    conn = get_db()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return {}

    team_profiles = []
    for uid in applicant_ids:
        skills = [r["skill"] for r in conn.execute(
            "SELECT skill FROM skills WHERE user_id=?", (uid,)).fetchall()]
        interests = [r["interest"] for r in conn.execute(
            "SELECT interest FROM interests WHERE user_id=?", (uid,)).fetchall()]
        user = conn.execute(
            "SELECT name,department,year FROM users WHERE id=?", (uid,)).fetchone()
        if user:
            team_profiles.append({
                "name": user["name"],
                "skills": skills,
                "interests": interests
            })
    conn.close()

    prompt = f"""You are an AI team analyzer for a university project platform.

Project: {project['title']}
Description: {project['description']}
Required Skills: {project['required_skills']}

Team: {json.dumps(team_profiles, indent=2)}

Return ONLY this JSON object (no markdown):
{{
    "success_probability": 78,
    "verdict": "Strong Team",
    "skill_coverage": 85,
    "diversity_score": 70,
    "missing_roles": ["UI/UX Designer", "DevOps"],
    "strengths": ["Strong ML coverage", "Complementary skills"],
    "risks": ["No frontend specialist"],
    "recommendation": "Add a frontend developer to balance the team."
}}"""

    try:
        return _parse_json(model.generate_content(prompt).text)
    except Exception as e:
        print(f"Predictor error: {e}")
        return {}
