from __future__ import annotations

from typing import Any


def build_extractor_prompt(job: dict[str, Any]) -> str:
	company = job.get('company_name', '')
	job_url = job.get('job_url', '')
	job_title = job.get('job_title', '')

	return f"""
You are an OSINT and technical recruiter intelligence agent.

YOUR GOAL:
Find the hiring manager, technical recruiter, or talent acquisition lead for this role:
- Role: {job_title}
- Company: {company}
- Job Posting: {job_url}

EXECUTION STRATEGY:
1. First, navigate to the job posting URL: {job_url}
2. Carefully inspect:
   - "Meet the hiring team" or "Job poster" section
   - Any recruiter or HR person linked directly to the posting
   - Any listed email address (e.g. careers@{company.lower().replace(' ', '')}.com or specific recruiter email)
3. If not found on the job posting:
   - Search LinkedIn for "{company} Technical Recruiter" or "{company} Talent Acquisition" or "{company} Engineering Manager"
   - Find the top 1-2 most relevant people responsible for hiring software engineers.
4. If you discover contact details:
   - Call save_hr_contact(
       job_url="{job_url}",
       hr_name="<Recruiter Name>",
       hr_email="<Email if visible>",
       hr_linkedin="<LinkedIn Profile URL>"
     )
5. Call done when you have either saved the contact or thoroughly searched without result.
"""
