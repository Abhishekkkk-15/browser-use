from __future__ import annotations

from typing import Any

from job_agent.config import UserProfile


def build_application_prompt(
	job: dict[str, Any],
	user: UserProfile,
	custom_pitch: str,
	dry_run: bool = True,
	mode: str = 'ask',
) -> str:
	job_url = job.get('job_url', '')
	job_title = job.get('job_title', 'Software Engineer')
	company_name = job.get('company_name', 'Company')
	platform = job.get('platform', 'linkedin')

	if dry_run:
		submit_instruction = (
			'DRY RUN MODE ENABLED: Fill all form fields completely and advance to the final review step. '
			"DO NOT CLICK the final 'Submit application' button! Instead, take a screenshot of the filled form, "
			"and call mark_applied(job_url, status='applied', notes='[DRY RUN] Form filled and verified successfully')."
		)
	elif mode == 'ask':
		submit_instruction = (
			'ASK MODE ENABLED (HUMAN CONFIRMATION REQUIRED): When you reach the final review page, you MUST call '
			f"request_human_confirmation(action='submit_application', company_name='{company_name}', role='{job_title}', "
			"details='Form filled with contact info and screening answers', preview_content='Ready to submit').\n"
			"   - If the tool result is APPROVED: click the final 'Submit application' button, verify confirmation modal, and call mark_applied(job_url, status='applied').\n"
			"   - If the tool result is REJECTED: close or cancel the application modal, and call mark_applied(job_url, status='found', notes='Skipped by user').\n"
			'   - If the tool result is EDITED: update fields as requested and then click Submit.'
		)
	else:
		submit_instruction = (
			"AUTONOMOUS FREE MODE: After reviewing all answers and ensuring fields are accurate, click the final 'Submit application' button. "
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
3. If an application modal or multi-step form appears, execute the SMART FORM SOLVER:
   a. PRE-FLIGHT STEP SCAN:
      - Fill all text inputs: Name, Email, Phone, Location from candidate profile.
      - If a resume upload field appears: attach the available resume file.
   b. SCREENING QUESTIONS RESOLUTION:
      - Years of experience: enter {user.years_of_experience} (or 2-3 if specific skill is mentioned).
      - Are you legally authorized to work?: Select Yes.
      - Will you require visa sponsorship?: Select No (or per profile: {user.work_authorization}).
      - Notice period / Availability: enter {user.notice_period_days} days.
      - Desired salary / compensation: enter {user.expected_salary_annual}.
      - Radio buttons / dropdowns: select the affirmative or best matching option (e.g. "Yes", "Fluent", "Comfortable", or degree matching {user.education}).
      - Why do you want to work here / Cover note: paste the provided pitch note.
   c. STUCK-STEP / DISABLED BUTTON RECOVERY:
      - If 'Next', 'Continue', or 'Review' is disabled or unclickable:
        * DO NOT keep clicking a disabled button!
        * Scan the current screen for red error messages, unfilled required fields (*), or unselected radio buttons.
        * Complete the missing input, then click 'Next'.
   d. Advance through each step until reaching the final review page.
4. When you reach the final review page:
   {submit_instruction}
5. SUBMISSION VERIFICATION:
   - Verify that the confirmation modal ("Application submitted", "Your application was sent") appears.
   - If successfully submitted or dry-run confirmed, call mark_applied(job_url='{job_url}', status='applied').
6. If at any point the application cannot proceed (e.g. requires external ATS redirect like Workday/Greenhouse without Easy Apply, or blocked captcha):
   call mark_applied(job_url='{job_url}', status='failed', notes='External redirect or manual verification required') and finish.
7. Once completed, finish with the done action.
"""
