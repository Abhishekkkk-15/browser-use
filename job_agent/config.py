from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class UserProfile(BaseModel):
	"""Comprehensive user professional profile for form filling and pitch generation."""

	name: str = Field(default='Candidate Name', description='Full legal name')
	email: str = Field(default='candidate@example.com', description='Primary email address')
	phone: str = Field(default='+1234567890', description='Contact phone number with country code')
	location: str = Field(default='Remote', description='Current location / City, Country')
	linkedin_url: str = Field(default='https://www.linkedin.com/in/candidate', description='LinkedIn profile URL')
	github_url: str | None = Field(default=None, description='GitHub profile URL')
	portfolio_url: str | None = Field(default=None, description='Portfolio or personal website URL')
	resume_path: Path = Field(
		default=Path('job_agent/data/resume.pdf'),
		description='Path to resume PDF file to upload in applications',
	)
	resume_text_path: Path = Field(
		default=Path('job_agent/data/resume.txt'),
		description='Path to plain text resume for LLM context',
	)
	years_of_experience: int = Field(default=3, description='Total years of professional experience')
	current_role: str = Field(default='Software Engineer', description='Current or most recent job title')
	current_company: str | None = Field(default=None, description='Current employer')
	skills: list[str] = Field(
		default_factory=lambda: [
			'Python',
			'FastAPI',
			'TypeScript',
			'React',
			'PostgreSQL',
			'Docker',
			'AWS',
			'System Design',
		],
		description='Key technical and professional skills',
	)
	summary: str = Field(
		default='Experienced software engineer specializing in scalable backend systems, AI agents, and web automation.',
		description='Executive summary / bio',
	)
	education: str = Field(
		default='Bachelor of Science in Computer Science',
		description='Highest education degree and institution',
	)
	work_authorization: str = Field(
		default='Authorized to work without sponsorship',
		description='Work authorization / visa status',
	)
	notice_period_days: int = Field(default=15, description='Notice period in days')
	expected_salary_annual: str = Field(default='Competitive / Market standard', description='Salary expectations')

	@classmethod
	def from_env_or_defaults(cls) -> UserProfile:
		"""Construct user profile from environment variables or sensible defaults."""
		skills_str = os.getenv('USER_SKILLS', '')
		skills = [s.strip() for s in skills_str.split(',') if s.strip()] if skills_str else None

		resume_p = Path(os.getenv('USER_RESUME_PATH', 'job_agent/data/resume.pdf'))
		resume_txt_p = Path(os.getenv('USER_RESUME_TEXT_PATH', 'job_agent/data/resume.txt'))

		kwargs: dict[str, Any] = {}
		if os.getenv('USER_NAME'):
			kwargs['name'] = os.getenv('USER_NAME')
		if os.getenv('USER_EMAIL'):
			kwargs['email'] = os.getenv('USER_EMAIL')
		if os.getenv('USER_PHONE'):
			kwargs['phone'] = os.getenv('USER_PHONE')
		if os.getenv('USER_LOCATION'):
			kwargs['location'] = os.getenv('USER_LOCATION')
		if os.getenv('USER_LINKEDIN'):
			kwargs['linkedin_url'] = os.getenv('USER_LINKEDIN')
		if os.getenv('USER_GITHUB'):
			kwargs['github_url'] = os.getenv('USER_GITHUB')
		if os.getenv('USER_PORTFOLIO'):
			kwargs['portfolio_url'] = os.getenv('USER_PORTFOLIO')
		if os.getenv('USER_EXPERIENCE_YEARS'):
			try:
				kwargs['years_of_experience'] = int(os.getenv('USER_EXPERIENCE_YEARS', '3'))
			except ValueError:
				pass
		if os.getenv('USER_CURRENT_ROLE'):
			kwargs['current_role'] = os.getenv('USER_CURRENT_ROLE')
		if os.getenv('USER_CURRENT_COMPANY'):
			kwargs['current_company'] = os.getenv('USER_CURRENT_COMPANY')
		if os.getenv('USER_EDUCATION'):
			kwargs['education'] = os.getenv('USER_EDUCATION')
		if skills:
			kwargs['skills'] = skills
		if os.getenv('USER_SUMMARY'):
			kwargs['summary'] = os.getenv('USER_SUMMARY')

		kwargs['resume_path'] = resume_p
		kwargs['resume_text_path'] = resume_txt_p

		return cls(**kwargs)

	def get_resume_content(self) -> str:
		"""Read the plain-text resume if available, else summarize profile."""
		if self.resume_text_path.exists():
			try:
				return self.resume_text_path.read_text(encoding='utf-8')
			except Exception:
				pass
		return f"""
Candidate: {self.name}
Role: {self.current_role} ({self.years_of_experience} years exp)
Skills: {', '.join(self.skills)}
Summary: {self.summary}
Education: {self.education}
Contact: {self.email} | {self.phone} | {self.linkedin_url}
"""


class JobPreferences(BaseModel):
	"""User preferences for job filtering and application volume."""

	target_roles: list[str] = Field(
		default_factory=lambda: ['Software Engineer', 'Backend Developer', 'Full Stack Engineer'],
		description='Target job titles to search for',
	)
	target_locations: list[str] = Field(
		default_factory=lambda: ['Remote', 'Bangalore', 'San Francisco'],
		description='Target locations / cities or Remote',
	)
	experience_level: Literal['internship', 'entry_level', 'associate', 'mid_senior', 'director', 'executive'] = Field(
		default='mid_senior',
		description='Target experience level filter',
	)
	platforms: list[Literal['linkedin', 'wellfound', 'naukri']] = Field(
		default_factory=lambda: ['linkedin', 'wellfound', 'naukri'],
		description='Job platforms to search and apply on',
	)
	max_applications_per_run: int = Field(
		default=10,
		description='Maximum number of applications to submit in a single execution',
	)
	max_searches_per_platform: int = Field(
		default=15,
		description='Maximum job postings to extract per platform during search',
	)
	dry_run: bool = Field(
		default=True,
		description='If True, fills forms and prepares applications without clicking final Submit',
	)
	preferred_companies: list[str] = Field(
		default_factory=list,
		description='List of companies to prioritize',
	)
	blacklisted_companies: list[str] = Field(
		default_factory=list,
		description='List of companies to never apply to',
	)
	min_salary_lpa: float | None = Field(default=None, description='Minimum expected salary in LPA or local currency')
	auto_cold_email: bool = Field(
		default=True,
		description='Whether to automatically send cold emails after extracting recruiter contacts',
	)
	mode: Literal['free', 'ask'] = Field(
		default='ask',
		description="Operating mode: 'free' (autonomous autopilot) or 'ask' (prompts human before submit/send)",
	)
	min_fit_score: float = Field(
		default=40.0,
		description='Minimum fit percentage required to proceed with automatic application',
	)
	use_browser_email: bool = Field(
		default=True,
		description='Send cold outreach directly through browser webmail (Gmail) without requiring SMTP credentials',
	)


class JobRecord(BaseModel):
	"""Structured representation of a discovered job posting."""

	job_title: str
	company_name: str
	job_url: str
	platform: Literal['linkedin', 'wellfound', 'naukri', 'other']
	location: str = 'Unknown'
	salary_range: str | None = None
	job_description_summary: str = ''
	required_skills: list[str] = Field(default_factory=list)
	hr_name: str | None = None
	hr_email: str | None = None
	hr_linkedin: str | None = None
	recruiter_name: str | None = None
	application_type: Literal['easy_apply', 'external', 'email', 'direct'] = 'easy_apply'
	status: Literal['found', 'applied', 'failed', 'interview', 'rejected', 'offer'] = 'found'
	match_score: float = 0.0
	notes: str | None = None
	pitch_used: str | None = None


class AgentConfig(BaseModel):
	"""Operational configuration for browser-use and agents."""

	chrome_user_data_dir: str | None = Field(
		default=os.getenv('CHROME_USER_DATA_DIR', '~/.config/browseruse/profiles/job_agent'),
		description='Path to persistent Chrome user-data-dir preserving logins',
	)
	storage_state_path: Path = Field(
		default=Path('job_agent/data/browser_storage_state.json'),
		description='Path to persistent cookies & session storage JSON file',
	)
	cdp_url: str | None = Field(
		default=os.getenv('CDP_URL', None),
		description='Optional CDP URL to attach to an existing Chrome instance (e.g. http://localhost:9222)',
	)
	mode: Literal['free', 'ask'] = Field(
		default='ask',
		description="Operating mode: 'free' (full autopilot) or 'ask' (human confirmation before submit/send)",
	)
	headless: bool = Field(default=False, description='Run browser in headful mode for visibility and stealth')
	demo_mode: bool = Field(default=True, description='Display interactive in-browser panel and keep browser visible')
	model_name: str = Field(
		default=os.getenv('OPENAI_MODEL', 'gpt-5.6-luna'),
		description='LLM provider model to use',
	)
	action_delay_seconds_min: float = Field(default=8.0, description='Min randomized delay between applications')
	action_delay_seconds_max: float = Field(default=18.0, description='Max randomized delay between applications')
	save_screenshots: bool = Field(default=True, description='Save visual verification screenshots')
	database_path: Path = Field(default=Path('job_agent/data/jobs_tracker.db'), description='SQLite database path')
