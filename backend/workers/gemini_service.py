import os
import json
import re
import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

async def _call_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        return ""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]}
        )
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            return ""

async def generate_cover_letter(job_title: str, company: str, job_description: str, cv_text: str) -> str:
    prompt = f"""
You are a professional career coach. Write a concise, compelling cover letter (3 short paragraphs) for this job application.

Job Title: {job_title}
Company: {company}
Job Description (summary): {job_description[:800]}

Candidate Background (from CV):
{cv_text[:1200]}

Requirements:
- Professional but natural tone
- 3 paragraphs: why this role, relevant experience, closing
- Max 200 words total
- English language
- Do NOT use placeholders like [Your Name] — just write the content body
"""
    return await _call_gemini(prompt)

def render_cover_letter_template(template: str, job_title: str, company: str) -> str:
    if not template:
        return ""
    rendered = template
    job_value = (job_title or "posisi").strip()
    company_value = (company or "").strip()

    position_patterns = (
        r"\{\s*posisi\s*\}",
        r"\{\s*nama\s*posisi\s*\}",
        r"\{\s*posisi\s*kerja\s*\}",
        r"\{\s*job\s*title\s*\}",
        r"\{\s*position(?:\s*name)?\s*\}",
        r"\[\s*position[_\s-]*name\s*\]",
        r"\[\s*posisi\s*\]",
    )
    company_patterns = (
        r"\{\s*perusahaan\s*\}",
        r"\{\s*nama\s*perusahaan\s*\}",
        r"\{\s*company(?:\s*name)?\s*\}",
        r"\[\s*company[_\s-]*name\s*\]",
        r"\[\s*perusahaan\s*\]",
    )

    for pattern in position_patterns:
        rendered = re.sub(pattern, job_value, rendered, flags=re.I)
    for pattern in company_patterns:
        rendered = re.sub(pattern, company_value, rendered, flags=re.I)

    if company_value:
        return rendered.strip()

    rendered = re.sub(r"\b(di|pada)\s+lowongan\s+ini\b", "", rendered, flags=re.I)
    rendered = re.sub(r"\bat\s+this\s+(role|job|opening)\b", "", rendered, flags=re.I)
    rendered = re.sub(r"\b(di|at)\s*([,.;:\n])", r"\2", rendered, flags=re.I)
    rendered = re.sub(r"\b(di|at)\s*$", "", rendered, flags=re.I | re.M)
    rendered = re.sub(r"\s+di\s+([,.])", r"\1", rendered, flags=re.I)
    rendered = re.sub(r"\s+at\s+([,.])", r"\1", rendered, flags=re.I)
    rendered = re.sub(r"\s+([,.;:])", r"\1", rendered)
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = re.sub(r" {2,}", " ", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()

async def validate_and_fix_email(subject: str, body: str, company: str, job_title: str, candidate_name: str) -> dict:
    """
    Use Gemini to validate and fix an email before sending.
    Returns dict with keys: subject, body, is_valid, issues (list of str).
    """
    prompt = f"""
You are an email quality checker for job application emails. Review and fix this email.

Subject: {subject}
Body:
{body}

Company name: {company}
Job title: {job_title}
Candidate name: {candidate_name}

Check for these issues and FIX them:
1. Company name appears MORE THAN ONCE in the body — remove duplicates, keep only the first natural mention
2. Company name appears in subject AND body redundantly — it's OK to have it in both, but body should only mention it once naturally
3. Email content is appropriate and professional
4. No placeholder text like [Company Name] or {{perusahaan}} left unfilled
5. Candidate name is mentioned at least once

Return JSON ONLY with these keys:
- "subject": the corrected subject line
- "body": the corrected email body (fix duplicate company names, keep it professional)
- "is_valid": boolean (true if email is good to send)
- "issues": list of strings describing what was fixed

If no issues found, return the original subject and body unchanged with empty issues list.
"""
    raw = await _call_gemini(prompt)
    if not raw:
        return {"subject": subject, "body": body, "is_valid": True, "issues": []}

    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {"subject": subject, "body": body, "is_valid": True, "issues": []}

    try:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return {
                "subject": data.get("subject") or subject,
                "body": data.get("body") or body,
                "is_valid": bool(data.get("is_valid", True)),
                "issues": data.get("issues") or [],
            }
    except json.JSONDecodeError:
        pass

    return {"subject": subject, "body": body, "is_valid": True, "issues": []}


async def answer_question(question: str, field_type: str, cv_text: str, job_title: str) -> str:
    prompt = f"""
You are filling out a job application form. Answer the following question concisely and professionally.

Question: {question}
Field type: {field_type}  (text / number / yes_no / dropdown)
Job being applied for: {job_title}

Candidate background:
{cv_text[:800]}

Rules:
- For yes/no: reply only "Yes" or "No"
- For number: reply only the number
- For text: keep it under 100 words, professional
- Be specific, not generic
- Reply with the answer only, no explanation
"""
    return await _call_gemini(prompt)

async def analyze_linkedin_post_opportunity(post_text: str, position: str, location: str, cv_text: str) -> dict:
    prompt = f"""
You are screening LinkedIn posts for real job opportunities.

Target position: {position}
Target location: {location}

LinkedIn post:
{post_text[:2500]}

Candidate background:
{cv_text[:1200]}

Return JSON only with these keys:
- is_job_post: boolean
- confidence: number from 0 to 1
- job_title: string
- company: string
- location: string
- contact_email: string
- application_url: string
- reason: string
- draft_email_subject: string
- draft_email_body: string

Rules:
- Mark is_job_post true only if the post appears to invite applications, referrals, hiring, recruitment, or email/DM submission.
- Prefer posts that match the target position and location.
- If there is no email or application URL, leave those fields empty but still assess the post.
- Draft a concise email body in English, max 180 words, tailored to the post and CV.
"""
    raw = await _call_gemini(prompt)
    if not raw:
        return {}

    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}
