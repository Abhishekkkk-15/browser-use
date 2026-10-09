from __future__ import annotations

from typing import Any

from job_agent.config import UserProfile


def build_pitch_prompt(job: dict[str, Any], user: UserProfile) -> str:
	return f"""
You are an expert technical resume strategist and pitch writer.

Write a compelling, concise 3-4 sentence pitch for the candidate applying for this specific position:

TARGET JOB:
- Job Title: {job.get('job_title', 'Software Engineer')}
- Company: {job.get('company_name', 'Tech Company')}
- Description / Requirements:
{job.get('job_description_summary', '')[:800]}

CANDIDATE:
- Name: {user.name}
- Current Role: {user.current_role} ({user.years_of_experience}+ years exp)
- Core Skills: {', '.join(user.skills[:6])}
- Summary: {user.summary}

REQUIREMENTS:
1. Ground the pitch in the candidate's verified skills and actual experience.
2. Specifically mention 1-2 key technologies requested in the job description that match the candidate's skills.
3. Tone: Confident, professional, and action-oriented.
4. Keep it under 120 words so it easily fits into LinkedIn / Wellfound text boxes.
5. Return ONLY the final pitch text, no meta commentary or pleasantries.
"""
