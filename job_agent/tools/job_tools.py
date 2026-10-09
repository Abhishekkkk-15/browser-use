from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from browser_use import ActionResult, Tools
from job_agent.config import JobPreferences, JobRecord, UserProfile
from job_agent.database import JobTracker
from job_agent.services.fit_scorer import JobFitScorer

logger = logging.getLogger(__name__)


class ConfirmationParams(BaseModel):
	action: str = Field(
		description="Action to execute: 'submit_application', 'send_cold_email', or 'send_linkedin_message'",
	)
	company_name: str = Field(description='Hiring company name')
	role: str = Field(description='Job role title')
	details: str = Field(description='Summary of answers filled, attachments, or action details')
	preview_content: str = Field(default='', description='Preview of pitch, cover note, or email body')


class SaveJobParams(BaseModel):
	job_title: str = Field(description="Title of the job posting, e.g. 'Senior Backend Engineer'")
	company_name: str = Field(description='Name of the hiring company')
	job_url: str = Field(description='Direct URL to the job posting')
	platform: str = Field(description="Source platform: 'linkedin', 'wellfound', 'naukri', or 'other'")
	location: str = Field(default='Remote', description="Job location or 'Remote'")
	salary_range: str = Field(default='', description='Salary range if stated on the page, or empty')
	job_description_summary: str = Field(
		default='',
		description='Brief summary of duties, responsibilities, and key stack requirements',
	)
	required_skills: str = Field(
		default='',
		description='Comma-separated list of required technical and soft skills',
	)
	hr_name: str = Field(default='', description='Recruiter or HR contact name if visible')
	hr_email: str = Field(default='', description='Recruiter email address if found')
	hr_linkedin: str = Field(default='', description='Recruiter LinkedIn profile URL if found')
	application_type: str = Field(
		default='easy_apply',
		description="Application mechanism: 'easy_apply', 'external', 'email', or 'direct'",
	)


class MarkAppliedParams(BaseModel):
	job_url: str = Field(description='URL of the job that was just submitted')
	pitch_used: str = Field(default='', description='The custom cover letter or intro pitch text submitted')
	notes: str = Field(default='', description='Any notes about the submission, screening questions, or errors')
	status: str = Field(default='applied', description="New status: 'applied' or 'failed'")


class ProfileFieldParams(BaseModel):
	field: str = Field(
		description='Profile field to fetch: name, email, phone, location, linkedin_url, github_url, portfolio_url, years_of_experience, current_role, education, work_authorization, notice_period_days, expected_salary_annual',
	)


class CheckAppliedParams(BaseModel):
	job_url: str = Field(description='URL of the job to check in tracker database')


class SaveHRContactParams(BaseModel):
	job_url: str = Field(description='Job posting URL this contact is associated with')
	hr_name: str = Field(default='', description='Name of the HR professional, recruiter, or founder')
	hr_email: str = Field(default='', description='Email address discovered')
	hr_linkedin: str = Field(default='', description='LinkedIn profile URL discovered')


class MatchScoreParams(BaseModel):
	job_description: str = Field(description='Job description or requirements snippet')
	required_skills: str = Field(description='Comma-separated required skills')


def create_job_tools(
	tracker: JobTracker,
	user_profile: UserProfile,
	preferences: JobPreferences,
) -> Tools:
	"""Instantiate browser-use Tools populated with custom job hunting and application actions."""

	tools = Tools()

	@tools.action(
		description='Save a discovered job posting to the tracker database. Call this for each relevant posting.',
		param_model=SaveJobParams,
	)
	async def save_job(params: SaveJobParams) -> ActionResult:
		"""Save a job posting to SQLite."""
		# Check blacklist
		company_lower = params.company_name.lower()
		for blacklisted in preferences.blacklisted_companies:
			if blacklisted.lower() in company_lower:
				return ActionResult(
					extracted_content=f'Skipped {params.company_name} because it is in your blacklisted companies list.',
					include_extracted_content_only_once=True,
				)

		skills_list = [s.strip() for s in params.required_skills.split(',') if s.strip()]

		# Calculate comprehensive best-fit score and reasoning
		fit_result = JobFitScorer.score_fit(
			{
				'job_title': params.job_title,
				'job_description_summary': params.job_description_summary,
				'required_skills': skills_list,
				'location': params.location,
			},
			user_profile,
			target_roles=preferences.target_roles,
			target_locations=preferences.target_locations,
		)
		match_score = fit_result.score

		# STRICT RELEVANCE GATE: Never save non-relevant jobs!
		if match_score < preferences.min_fit_score:
			logger.info(
				f"Rejected non-relevant job '{params.job_title}' at '{params.company_name}' "
				f'(Score: {match_score:.1f}% < min threshold {preferences.min_fit_score:.1f}%). Reason: {fit_result.reasoning}'
			)
			return ActionResult(
				extracted_content=(
					f"REJECTED AS NOT RELEVANT: '{params.job_title}' at '{params.company_name}' scored only {match_score:.0f}% match "
					f'({fit_result.reasoning}), which is below your minimum relevance threshold ({preferences.min_fit_score:.0f}%). '
					f'This posting was NOT saved to the database. Continue searching and only save postings that genuinely match candidate skills, role, and location.'
				),
				include_extracted_content_only_once=True,
			)

		# Normalize and canonicalize job URL to prevent duplicates and tracking query collisions
		import re
		import urllib.parse

		cleaned_url = params.job_url.strip()
		if 'linkedin.com' in cleaned_url or params.platform == 'linkedin':
			match_id = re.search(r'currentJobId=(\d+)', cleaned_url)
			if match_id:
				cleaned_url = f'https://www.linkedin.com/jobs/view/{match_id.group(1)}/'
			elif '/jobs/view/' in cleaned_url:
				view_match = re.search(r'/jobs/view/(\d+)', cleaned_url)
				if view_match:
					cleaned_url = f'https://www.linkedin.com/jobs/view/{view_match.group(1)}/'
			elif '/jobs/search' in cleaned_url:
				import hashlib

				slug = hashlib.md5(f'{params.company_name}_{params.job_title}'.encode('utf-8')).hexdigest()[:10]
				cleaned_url = f'https://www.linkedin.com/jobs/view/li-{slug}/'
		else:
			parsed = urllib.parse.urlparse(cleaned_url)
			if parsed.query:
				q_pairs = urllib.parse.parse_qsl(parsed.query)
				filtered = [
					(k, v) for k, v in q_pairs if not k.lower().startswith(('utm_', 'trk', 'tracking', 'ref', 'source', 'origin'))
				]
				new_query = urllib.parse.urlencode(filtered)
				cleaned_url = urllib.parse.urlunparse(parsed._replace(query=new_query))

		cleaned_url = cleaned_url.rstrip('/')

		# Normalize application type
		app_type = params.application_type.lower()
		if 'easy' in app_type:
			norm_app_type = 'easy_apply'
		elif app_type in ('external', 'apply', 'direct'):
			norm_app_type = 'external'
		elif app_type in ('email',):
			norm_app_type = 'email'
		else:
			norm_app_type = 'easy_apply'

		job_rec = JobRecord(
			job_title=params.job_title,
			company_name=params.company_name,
			job_url=cleaned_url,
			platform=params.platform if params.platform in ('linkedin', 'wellfound', 'naukri') else 'other',
			location=params.location or 'Remote',
			salary_range=params.salary_range or None,
			job_description_summary=params.job_description_summary,
			required_skills=skills_list,
			hr_name=params.hr_name or None,
			hr_email=params.hr_email or None,
			hr_linkedin=params.hr_linkedin or None,
			application_type=norm_app_type,
			status='found',
			match_score=round(match_score, 1),
			notes=f'[Fit: {fit_result.reasoning}]' if fit_result.reasoning else None,
		)

		job_id = tracker.add_job(job_rec)
		stats = tracker.get_stats()
		return ActionResult(
			extracted_content=(
				f"Saved job #{job_id}: '{params.job_title}' at '{params.company_name}' (Fit: {match_score:.0f}%, {fit_result.reasoning}). "
				f'Total matching jobs in tracker: {stats["total_found"]}. Continue inspecting and cataloging high-relevance positions.'
			),
			include_extracted_content_only_once=True,
		)

	@tools.action(
		description='Mark a job application as completed and record it in the tracker database.',
		param_model=MarkAppliedParams,
	)
	async def mark_applied(params: MarkAppliedParams) -> ActionResult:
		"""Record successful or attempted application."""
		if preferences.dry_run and params.status == 'applied':
			tracker.update_status(
				job_url=params.job_url,
				status='found',
				notes=f'[DRY RUN - Form filled but not submitted] {params.notes}',
				pitch_used=params.pitch_used,
			)
			return ActionResult(
				extracted_content=f'[DRY RUN] Verified form completion for {params.job_url}. Application not submitted as dry_run=True.',
				include_extracted_content_only_once=True,
			)

		tracker.update_status(
			job_url=params.job_url,
			status=params.status,
			notes=params.notes,
			pitch_used=params.pitch_used,
		)
		return ActionResult(
			extracted_content=f"Successfully updated status of {params.job_url} to '{params.status}'.",
			include_extracted_content_only_once=True,
		)

	@tools.action(
		description='Retrieve candidate profile information to populate form inputs accurately.',
		param_model=ProfileFieldParams,
	)
	async def get_user_profile(params: ProfileFieldParams) -> ActionResult:
		"""Fetch candidate information field."""
		field_map = {
			'name': user_profile.name,
			'email': user_profile.email,
			'phone': user_profile.phone,
			'location': user_profile.location,
			'linkedin_url': user_profile.linkedin_url,
			'github_url': user_profile.github_url or '',
			'portfolio_url': user_profile.portfolio_url or '',
			'years_of_experience': str(user_profile.years_of_experience),
			'current_role': user_profile.current_role,
			'current_company': user_profile.current_company or '',
			'education': user_profile.education,
			'work_authorization': user_profile.work_authorization,
			'notice_period_days': str(user_profile.notice_period_days),
			'expected_salary_annual': user_profile.expected_salary_annual,
			'skills': ', '.join(user_profile.skills),
			'summary': user_profile.summary,
		}

		val = field_map.get(params.field.lower())
		if val is None:
			return ActionResult(
				error=f"Field '{params.field}' not found. Available fields: {', '.join(field_map.keys())}",
			)
		return ActionResult(extracted_content=str(val))

	@tools.action(
		description='Check if a job URL has already been processed or applied to avoid duplicate work.',
		param_model=CheckAppliedParams,
	)
	async def check_already_applied(params: CheckAppliedParams) -> ActionResult:
		"""Verify if job is already in the database."""
		is_applied = tracker.is_already_applied(params.job_url)
		is_saved = tracker.is_job_saved(params.job_url)
		if is_applied:
			return ActionResult(
				extracted_content=f'ALREADY_APPLIED: This posting ({params.job_url}) was previously submitted. Skip it.',
			)
		if is_saved:
			return ActionResult(
				extracted_content=f'ALREADY_SAVED: This posting ({params.job_url}) is already in tracker as pending.',
			)
		return ActionResult(extracted_content='NEW_JOB: This posting has not been processed yet.')

	@tools.action(
		description='Save or update HR, recruiter, or hiring manager contact details for a job.',
		param_model=SaveHRContactParams,
	)
	async def save_hr_contact(params: SaveHRContactParams) -> ActionResult:
		"""Update recruiter contact information in database."""
		tracker.update_hr_contact(
			job_url=params.job_url,
			hr_name=params.hr_name or None,
			hr_email=params.hr_email or None,
			hr_linkedin=params.hr_linkedin or None,
		)
		return ActionResult(
			extracted_content=f"Recorded HR contact for {params.job_url}: Name='{params.hr_name}', Email='{params.hr_email}', LinkedIn='{params.hr_linkedin}'",
			include_extracted_content_only_once=True,
		)

	@tools.action(
		description="Request human confirmation before performing sensitive actions like final application submission or email sending. In 'ask' mode, pauses and asks the user in CLI. In 'free' mode, approves automatically.",
		param_model=ConfirmationParams,
	)
	async def request_human_confirmation(params: ConfirmationParams) -> ActionResult:
		"""Check with user before final submit or send."""
		if preferences.mode == 'free':
			return ActionResult(
				extracted_content=f'APPROVED: Autonomous Free Mode is active. Proceeding immediately to {params.action}.',
			)

		from rich.console import Console
		from rich.panel import Panel
		from rich.prompt import Prompt

		cli_console = Console(legacy_windows=False)
		panel_content = (
			f'[bold white]Action:[/bold white] [bold yellow]{params.action}[/bold yellow]\n'
			f'[bold white]Company:[/bold white] [bold cyan]{params.company_name}[/bold cyan]\n'
			f'[bold white]Role:[/bold white] [bold]{params.role}[/bold]\n'
			f'[bold white]Details:[/bold white] {params.details}\n'
		)
		if params.preview_content:
			panel_content += f'\n[bold green]Preview Content:[/bold green]\n{params.preview_content[:400]}'

		cli_console.print()
		cli_console.print(
			Panel(
				panel_content,
				title='[bold yellow]⚠️ Human Confirmation Required (Ask Mode)[/bold yellow]',
				border_style='yellow',
			)
		)

		loop = asyncio.get_running_loop()
		try:
			user_choice = await loop.run_in_executor(
				None,
				lambda: Prompt.ask('Approve this action?', choices=['y', 'n', 's', 'edit'], default='y'),
			)
		except Exception:
			user_choice = 'y'

		choice_clean = str(user_choice).strip().lower()
		if choice_clean in ('y', 'yes'):
			return ActionResult(extracted_content=f'APPROVED: User approved {params.action}. Proceed to execute now.')
		elif choice_clean in ('n', 'no', 's', 'skip'):
			return ActionResult(
				extracted_content=f'REJECTED: User chose to skip {params.action}. Cancel this action and move on.'
			)
		else:
			new_text = await loop.run_in_executor(None, lambda: Prompt.ask('Enter modified message/pitch text to use'))
			return ActionResult(extracted_content=f"EDITED: User updated content to: '{new_text}'. Use this text and proceed.")

	return tools
