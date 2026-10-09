from __future__ import annotations

from typing import Any

from job_agent.config import UserProfile


def build_application_prompt(
	job: dict[str, Any],
	user: UserProfile,
	custom_pitch: str,
	dry_run: bool = True,
) -> str:
	job_url = job.get('job_url', '')
	job_title = job.get('job_title', 'Software Engineer')
	company_name = job.get('company_name', 'Company')
	platform = job.get('platform', 'linkedin')

	submit_instruction = (
		'DRY RUN MODE ENABLED: Fill all form fields completely and advance to the final review step. '
		"DO NOT CLICK the final 'Submit application' button! Instead, take a screenshot of the filled form, "
		"and call mark_applied(job_url, status='applied', notes='[DRY RUN] Form filled and verified successfully')."
		if dry_run
		else "LIVE SUBMISSION: After reviewing all answers and ensuring fields are accurate, click the final 'Submit application' button. "
		"Verify the confirmation modal appears, then call mark_applied(job_url, status='applied')."
	)

	return f"""
You are an autonomous AI job application agent applying to a position on behalf of the candidate.

TARGET JOB:
- Title: {job_title}
- Company: {company_name}
- URL: {job_url}
- Platform: {platform}

CANDIDATE INFORMATION TO FILL:
- Name: {user.name}
- Email: {user.email}
- Phone: {user.phone}
- Location: {user.location}
- LinkedIn: {user.linkedin_url}
- Experience: {user.years_of_experience} years
- Current Role: {user.current_role}
- Skills: {', '.join(user.skills)}
- Work Authorization: {user.work_authorization}
- Notice Period: {user.notice_period_days} days
- Expected Salary: {user.expected_salary_annual}

PITCH / COVER NOTE TO PASTE IF REQUESTED:
\"\"\"
{custom_pitch}
\"\"\"

STEP-BY-STEP EXECUTION INSTRUCTIONS:
1. Navigate directly to {job_url}
2. Find and click the 'Easy Apply' (or 'Apply Now') button.
3. If an application modal or multi-step form appears:
   a. Step through each page carefully.
   b. Fill in contact info (Phone, Email, Location) using candidate info.
   c. If resume upload is requested: select the uploaded resume file from the available file paths.
   d. If screening questions appear:
      - Years of experience: enter {user.years_of_experience} or select closest option.
      - Are you legally authorized to work?: Select Yes.
      - Will you now or in the future require sponsorship?: Select No (or per candidate authorization: {user.work_authorization}).
      - Notice period: enter {user.notice_period_days}.
      - Expected compensation / CTC: enter {user.expected_salary_annual}.
      - Why do you want to work here / Cover letter: paste the provided pitch note.
   e. Click 'Next' or 'Continue' to advance through each step.
4. When you reach the final review page:
   {submit_instruction}
5. If at any point the application cannot proceed (e.g. requires external website login or third-party redirect),
   call mark_applied(job_url='{job_url}', status='failed', notes='External redirect required') and finish.
6. Once completed, finish with the done action.
"""
