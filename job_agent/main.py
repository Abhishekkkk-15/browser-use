from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

if sys.platform == 'win32':
	if hasattr(sys.stdout, 'reconfigure'):
		sys.stdout.reconfigure(encoding='utf-8', errors='replace')
	if hasattr(sys.stderr, 'reconfigure'):
		sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from job_agent.config import AgentConfig, JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.orchestrator import JobAgentOrchestrator, get_default_llm
from job_agent.prompts.pitch_prompt import build_pitch_prompt
from job_agent.tools.job_tools import create_job_tools

console = Console(legacy_windows=False)


@click.group()
@click.option('--debug/--no-debug', default=False, help='Enable verbose debug logging')
def cli(debug: bool) -> None:
	"""AI Autonomous Job Application Agent powered by browser-use."""
	log_level = logging.DEBUG if debug else logging.INFO
	logging.basicConfig(
		level=log_level,
		format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
		datefmt='%H:%M:%S',
	)


# ==============================================================================
# AUTONOMOUS & AUTHENTICATION COMMANDS
# ==============================================================================


@cli.command(name='auth')
@click.option(
	'--platforms',
	default='linkedin,gmail',
	help='Comma-separated websites to open for initial login (e.g. linkedin,gmail,wellfound,naukri)',
)
def auth_command(platforms: str) -> None:
	"""Open headful Chrome to log into accounts once. Saves session cookies permanently with ZERO credentials in .env."""
	console.print(
		Panel.fit(
			'[bold cyan]🔑 Browser Session Authenticator[/bold cyan]\n\n'
			'1. A visible Chrome browser window will now open.\n'
			'2. Log into your accounts (LinkedIn, Google / Gmail, Wellfound, Naukri).\n'
			'3. Solve any 2FA or security challenges in the browser.\n'
			'4. Once logged in, return here and press [bold green]Enter[/bold green].\n\n'
			'[dim]All session tokens & cookies will be saved locally to job_agent/data/browser_storage_state.json.\n'
			'No passwords or secrets are ever saved in text or .env files![/dim]',
			title='One-Time Browser Auth',
			border_style='green',
		)
	)

	platform_urls = {
		'linkedin': 'https://www.linkedin.com/login',
		'gmail': 'https://mail.google.com/',
		'wellfound': 'https://wellfound.com/login',
		'naukri': 'https://www.naukri.com/nlogin/login',
	}

	targets = [p.strip().lower() for p in platforms.split(',') if p.strip()]
	initial_url = (
		platform_urls.get(targets[0], 'https://www.linkedin.com/login')
		if targets
		else 'https://www.linkedin.com/login'
	)

	from browser_use import BrowserProfile, BrowserSession
	from job_agent.config import AgentConfig

	agent_config = AgentConfig()
	agent_config.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

	profile = BrowserProfile(
		user_data_dir=agent_config.chrome_user_data_dir,
		storage_state=str(agent_config.storage_state_path),
		headless=False,
		keep_alive=True,
		ignore_default_args=[
			'--disable-window-activation',
			'--disable-focus-on-load',
		],
	)
	session = BrowserSession(browser_profile=profile)

	async def _run_auth() -> None:
		await session.start()
		await session.navigate(initial_url)
		for p in targets[1:]:
			url = platform_urls.get(p)
			if url:
				await session.create_new_tab(url)

		from rich.prompt import Prompt

		loop = asyncio.get_running_loop()
		await loop.run_in_executor(
			None,
			lambda: Prompt.ask(
				'\n[bold yellow]👉 When you have logged into all accounts in Chrome, press [Enter] here[/bold yellow]'
			),
		)
		console.print('\n[cyan]Finalizing and saving browser session state...[/cyan]')
		await asyncio.sleep(2)  # Allow StorageStateWatchdog to save cookies
		await session.stop()
		console.print(
			f'[bold green]✅ Success! Browser session saved to: {agent_config.storage_state_path}[/bold green]'
		)
		console.print(
			'[green]You can now run [bold]job-agent auto[/bold] with 100% autonomous operation and zero credentials in .env![/green]'
		)

	asyncio.run(_run_auth())


@cli.command(name='auto')
@click.option(
	'--mode',
	type=click.Choice(['free', 'ask'], case_sensitive=False),
	default='ask',
	help="Execution mode: 'free' (100% autonomous autopilot) or 'ask' (human confirmation before submit/send)",
)
@click.option('--free', 'flag_free', is_flag=True, help='Shortcut to enable Free mode (100% autonomous autopilot)')
@click.option('--ask', 'flag_ask', is_flag=True, help='Shortcut to enable Ask mode (prompts human before submit/send)')
@click.option('--dry-run/--live', default=False, help='Run live or dry-run simulation mode')
@click.option('--platforms', default='linkedin,wellfound', help='Comma-separated target job boards')
@click.option(
	'--roles',
	default='Applied AI Engineer,AI Engineer,Software Engineer',
	help='Target roles to search',
)
@click.option('--locations', default='Remote', help='Target locations or Remote')
@click.option(
	'--min-fit',
	default=45.0,
	type=float,
	help='Minimum Best-Fit score percentage required to apply',
)
@click.option('--max-apply', default=5, type=int, help='Maximum number of applications to submit')
@click.option('--login-first', is_flag=True, help='Prompt to authenticate in browser before running')
def auto_command(
	mode: str,
	flag_free: bool,
	flag_ask: bool,
	dry_run: bool,
	platforms: str,
	roles: str,
	locations: str,
	min_fit: float,
	max_apply: int,
	login_first: bool,
) -> None:
	"""Autonomous end-to-end recruitment agent with zero credentials and Free / Ask mode."""
	selected_mode = 'free' if flag_free else ('ask' if flag_ask else mode.lower())

	agent_config = AgentConfig()
	storage_file = agent_config.storage_state_path

	if login_first or not storage_file.exists():
		from rich.prompt import Confirm

		console.print('[yellow]Notice: No saved browser session found.[/yellow]')
		if login_first or Confirm.ask('Would you like to log into your accounts in Chrome now?', default=True):
			ctx = click.get_current_context()
			ctx.invoke(auth_command, platforms=platforms)

	console.print(
		Panel.fit(
			f'[bold cyan]🤖 Autonomous Job Application Agent[/bold cyan]\n\n'
			f'[yellow]Operating Mode:[/yellow] '
			f'{"[bold green]FREE MODE (100% Autonomous Autopilot)[/bold green]" if selected_mode == "free" else "[bold yellow]ASK MODE (Human Confirmation Before Submit/Send)[/bold yellow]"}\n'
			f'[yellow]Submission Mode:[/yellow] {"[dim]DRY RUN (Simulated)[/dim]" if dry_run else "[bold red]LIVE SUBMISSION[/bold red]"}\n'
			f'[yellow]Target Roles:[/yellow] {roles}\n'
			f'[yellow]Locations:[/yellow] {locations}\n'
			f'[yellow]Platforms:[/yellow] {platforms}\n'
			f'[yellow]Min Best-Fit Score:[/yellow] {min_fit}%\n'
			f'[yellow]Max Applications:[/yellow] {max_apply}\n'
			f'[yellow]Email Outreach:[/yellow] Browser-Native (Gmail Web Compose, Zero SMTP)\n'
			f'[yellow]Session Storage:[/yellow] {storage_file}',
			title='Autonomous Mode Activated',
			border_style='green' if selected_mode == 'free' else 'yellow',
		)
	)

	user_profile = UserProfile.from_env_or_defaults()
	platform_list = [p.strip().lower() for p in platforms.split(',') if p.strip()]

	preferences = JobPreferences(
		target_roles=[r.strip() for r in roles.split(',') if r.strip()],
		target_locations=[loc.strip() for loc in locations.split(',') if loc.strip()],
		platforms=platform_list,  # type: ignore
		max_applications_per_run=max_apply,
		dry_run=dry_run,
		auto_cold_email=True,
		mode=selected_mode,  # type: ignore
		min_fit_score=min_fit,
		use_browser_email=True,
	)

	orchestrator = JobAgentOrchestrator(
		user_profile=user_profile,
		preferences=preferences,
		agent_config=agent_config,
	)

	results = asyncio.run(
		orchestrator.run_autonomous_pipeline(
			mode=selected_mode,
			platforms=platform_list,
			max_applications=max_apply,
			min_fit_score=min_fit,
			dry_run=dry_run,
		)
	)

	stats = results.get('stats', {})
	summary_table = Table(title='[Autonomous Campaign Results]', border_style='green', box=box.ROUNDED)
	summary_table.add_column('Metric', style='bold cyan')
	summary_table.add_column('Count', style='bold white')
	summary_table.add_row('Jobs Discovered', str(stats.get('total_found', 0)))
	summary_table.add_row('Applications Submitted', str(stats.get('total_applied', 0)))
	summary_table.add_row('Recruiter Contacts Found', str(stats.get('total_hr_emails', 0)))
	summary_table.add_row('Browser Outreach Emails', str(stats.get('total_emails_sent', 0)))

	console.print()
	console.print(summary_table)
	console.print(
		"\n[bold green]Campaign cycle complete! Run 'job-agent stats' or 'job-agent jobs' to inspect details.[/bold green]"
	)


# Add login alias for auth
cli.add_command(auth_command, name='login')


# ==============================================================================
# PIPELINE COMMANDS
# ==============================================================================


@cli.command(name='run')
@click.option(
	'--dry-run/--live',
	default=True,
	help='Run in safe simulation mode (forms filled but not submitted) or live mode',
)
@click.option(
	'--platforms',
	default='linkedin,wellfound,naukri',
	help='Comma-separated list of platforms to target',
)
@click.option(
	'--roles',
	default='Applied AI Engineer,Software Engineer',
	help='Comma-separated target role keywords',
)
@click.option(
	'--locations',
	default='Remote',
	help='Comma-separated target location keywords',
)
@click.option(
	'--max-apply',
	default=10,
	type=int,
	help='Max applications to submit in this run',
)
@click.option(
	'--auto-email/--no-auto-email',
	default=False,
	help='Automatically dispatch cold emails when recruiter email is discovered',
)
def run_command(
	dry_run: bool,
	platforms: str,
	roles: str,
	locations: str,
	max_apply: int,
	auto_email: bool,
) -> None:
	"""Execute the complete end-to-end job hunt pipeline across all phases."""
	console.print(
		Panel.fit(
			f'[bold cyan]AI Job Application Agent Pipeline[/bold cyan]\n'
			f'[yellow]Mode:[/yellow] {"[bold green]DRY RUN (Simulated)[/bold green]" if dry_run else "[bold red]LIVE SUBMISSION[/bold red]"}\n'
			f'[yellow]Platforms:[/yellow] {platforms}\n'
			f'[yellow]Roles:[/yellow] {roles}\n'
			f'[yellow]Locations:[/yellow] {locations}\n'
			f'[yellow]Max Applications:[/yellow] {max_apply}\n'
			f'[yellow]Auto-Email Outreach:[/yellow] {auto_email}',
			title='Pipeline Runner',
			border_style='cyan',
		)
	)

	user_profile = UserProfile.from_env_or_defaults()
	platform_list = [p.strip().lower() for p in platforms.split(',') if p.strip()]

	preferences = JobPreferences(
		target_roles=[r.strip() for r in roles.split(',') if r.strip()],
		target_locations=[loc.strip() for loc in locations.split(',') if loc.strip()],
		platforms=platform_list,  # type: ignore
		max_applications_per_run=max_apply,
		dry_run=dry_run,
		auto_cold_email=auto_email,
	)

	orchestrator = JobAgentOrchestrator(
		user_profile=user_profile,
		preferences=preferences,
	)

	asyncio.run(orchestrator.run_full_pipeline())


@cli.command(name='search')
@click.option('--platforms', default='linkedin,wellfound,naukri', help='Platforms to search (comma-separated)')
@click.option('--roles', default='AI Engineer', help='Role keywords')
@click.option('--location', default='Remote', help='Target location')
def search_command(platforms: str, roles: str, location: str) -> None:
	"""Phase 1: Search designated job boards and catalog vacancies into database."""
	console.print(f'[bold cyan]Searching {platforms.upper()} for "{roles}" in "{location}"...[/bold cyan]')
	user_profile = UserProfile.from_env_or_defaults()
	platform_list = [p.strip().lower() for p in platforms.split(',') if p.strip()]

	preferences = JobPreferences(
		target_roles=[r.strip() for r in roles.split(',') if r.strip()],
		target_locations=[location.strip()],
		platforms=platform_list,  # type: ignore
	)

	orchestrator = JobAgentOrchestrator(
		user_profile=user_profile,
		preferences=preferences,
	)
	results = asyncio.run(orchestrator.run_search_only())
	console.print(f'[bold green]Search completed successfully:[/bold green] {results}')


@cli.command(name='apply')
@click.option('--dry-run/--live', default=True, help='Simulate form filling or submit live')
@click.option('--limit', default=5, type=int, help='Max jobs to apply for')
def apply_command(dry_run: bool, limit: int) -> None:
	"""Phase 3: Autofill application forms for discovered pending jobs."""
	mode_label = '[bold green]DRY RUN (Simulated)[/bold green]' if dry_run else '[bold red]LIVE SUBMISSION[/bold red]'
	console.print(f'[bold cyan]Applying to up to {limit} pending jobs ({mode_label})...[/bold cyan]')
	user_profile = UserProfile.from_env_or_defaults()
	preferences = JobPreferences(dry_run=dry_run, max_applications_per_run=limit)

	orchestrator = JobAgentOrchestrator(
		user_profile=user_profile,
		preferences=preferences,
	)
	count = asyncio.run(orchestrator.run_apply_only(limit=limit))
	console.print(f'[bold green]Successfully processed applications for {count} jobs.[/bold green]')


@cli.command(name='extract')
@click.option('--limit', default=10, type=int, help='Number of postings to research for contacts')
def extract_command(limit: int) -> None:
	"""Phase 2: Hunt for HR/recruiter emails, names, and LinkedIn contacts."""
	console.print(f'[bold cyan]Extracting recruiter contacts for up to {limit} jobs...[/bold cyan]')
	orchestrator = JobAgentOrchestrator()
	count = asyncio.run(orchestrator.run_extract_only(limit=limit))
	console.print(f'[bold green]Processed contacts for {count} postings.[/bold green]')


@cli.command(name='email')
@click.option('--limit', default=5, type=int, help='Max cold emails to send')
def email_command(limit: int) -> None:
	"""Phase 4: Dispatch tailored cold emails to contacts with verified emails."""
	console.print(f'[bold cyan]Dispatching cold emails (Limit: {limit})...[/bold cyan]')
	orchestrator = JobAgentOrchestrator()
	count = asyncio.run(orchestrator.run_email_only(limit=limit))
	console.print(f'[bold green]Dispatched {count} cold emails.[/bold green]')


# ==============================================================================
# DATA INSPECTION & MANAGEMENT
# ==============================================================================


@cli.command(name='stats')
def stats_command() -> None:
	"""Display campaign analytics dashboard (jobs, applications, interviews)."""
	tracker = JobTracker()
	stats = tracker.get_stats()

	table = Table(title='[Job Hunt Campaign Metrics]', border_style='cyan', box=box.ROUNDED)
	table.add_column('Metric', style='bold yellow')
	table.add_column('Value', style='bold green', justify='right')

	table.add_row('Total Jobs Found', str(stats['total_found']))
	table.add_row('Applications Submitted', str(stats['total_applied']))
	table.add_row('Interviews Scheduled', str(stats['total_interviews']))
	table.add_row('Recruiter Emails Found', str(stats['total_hr_emails']))
	table.add_row('Cold Emails Sent', str(stats['total_emails_sent']))

	console.print(table)

	if stats.get('platform_breakdown'):
		plat_table = Table(title='Platform Breakdown', border_style='blue', box=box.ROUNDED)
		plat_table.add_column('Platform', style='bold')
		plat_table.add_column('Discovered', justify='right')
		plat_table.add_column('Applied', justify='right')

		for p in stats['platform_breakdown']:
			plat_table.add_row(
				str(p['platform']).upper(),
				str(p['count']),
				str(p['applied'] or 0),
			)
		console.print(plat_table)


@cli.command(name='jobs')
@click.option('--limit', default=25, type=int, help='Maximum jobs to display')
@click.option('--platform', default=None, help='Filter by platform: linkedin, wellfound, naukri')
def jobs_command(limit: int, platform: str | None) -> None:
	"""List cataloged job postings from the database in a table."""
	tracker = JobTracker()
	jobs = tracker.get_all_jobs(platform=platform, limit=limit)

	if not jobs:
		console.print('[yellow]No jobs found in database.[/yellow]')
		return

	table = Table(title=f'[Cataloged Jobs in Database (Showing {len(jobs)})]', border_style='cyan', box=box.ROUNDED)
	table.add_column('ID', style='dim', width=4)
	table.add_column('Job Title', style='bold white')
	table.add_column('Company', style='bold cyan')
	table.add_column('Platform', style='magenta')
	table.add_column('Location', style='green')
	table.add_column('Match', justify='right')
	table.add_column('HR / Recruiter', style='yellow')
	table.add_column('Status', style='bold')

	for j in jobs:
		hr_info = j.get('hr_name') or ''
		if j.get('hr_email'):
			hr_info += f" ({j['hr_email']})"
		table.add_row(
			str(j['id']),
			(j.get('job_title') or 'Unknown')[:30],
			(j.get('company_name') or 'Unknown')[:20],
			str(j.get('platform') or 'other').upper(),
			(j.get('location') or 'Remote')[:18],
			f"{j.get('match_score', 0):.0f}%",
			hr_info[:22] if hr_info else '—',
			str(j.get('status') or 'found'),
		)

	console.print(table)


# ==============================================================================
# INDIVIDUAL JOB DETAILS & MANAGEMENT (job group)
# ==============================================================================


@cli.group(name='job')
def job_group() -> None:
	"""Manage or inspect individual job records by ID."""
	pass


@job_group.command(name='show')
@click.argument('job_id', type=int)
def job_show_command(job_id: int) -> None:
	"""Show complete details, requirements, stack, and contact info for a job."""
	tracker = JobTracker()
	j = tracker.get_job_by_id(job_id)

	if not j:
		console.print(f'[red]Job #{job_id} not found in database.[/red]')
		return

	try:
		skills = json.loads(j.get('required_skills') or '[]')
		skills_str = ', '.join(skills) if isinstance(skills, list) else str(skills)
	except Exception:
		skills_str = str(j.get('required_skills') or 'None')

	details = (
		f"[bold white]Title:[/bold white] {j.get('job_title')}\n"
		f"[bold white]Company:[/bold white] [bold cyan]{j.get('company_name')}[/bold cyan]\n"
		f"[bold white]Platform:[/bold white] {str(j.get('platform')).upper()}\n"
		f"[bold white]Location:[/bold white] {j.get('location') or 'Remote'}\n"
		f"[bold white]Salary / Equity:[/bold white] {j.get('salary_range') or 'Not disclosed'}\n"
		f"[bold white]URL:[/bold white] [underline blue]{j.get('job_url')}[/underline blue]\n"
		f"[bold white]Application Type:[/bold white] {j.get('application_type')}\n"
		f"[bold white]Status:[/bold white] {j.get('status')}\n"
		f"[bold white]Match Score:[/bold white] [bold green]{j.get('match_score', 0):.1f}%[/bold green]\n"
		f"[bold white]Recruiter / HR:[/bold white] {j.get('hr_name') or 'None'}\n"
		f"[bold white]HR Email:[/bold white] {j.get('hr_email') or 'None'}\n"
		f"[bold white]HR LinkedIn:[/bold white] {j.get('hr_linkedin') or 'None'}\n\n"
		f"[bold yellow]Required Skills & Stack:[/bold yellow]\n{skills_str}\n\n"
		f"[bold yellow]Job Description Summary:[/bold yellow]\n{j.get('job_description_summary') or 'None'}\n\n"
		f"[bold yellow]Notes / Feedback:[/bold yellow]\n{j.get('notes') or 'None'}"
	)

	console.print(Panel(details, title=f"Job Record #{j['id']}", border_style='cyan'))


@job_group.command(name='update')
@click.argument('job_id', type=int)
@click.option(
	'--status',
	type=click.Choice(['found', 'applied', 'interview', 'rejected', 'offer']),
	required=True,
	help='New job status',
)
@click.option('--notes', default=None, help='Additional notes or recruiter feedback')
def job_update_command(job_id: int, status: str, notes: str | None) -> None:
	"""Update the tracking status and notes for a specific job."""
	tracker = JobTracker()
	j = tracker.get_job_by_id(job_id)
	if not j:
		console.print(f'[red]Job #{job_id} not found.[/red]')
		return

	tracker.update_status(job_url=j['job_url'], status=status, notes=notes)
	console.print(f"[bold green]Updated Job #{job_id} to status '{status}'.[/bold green]")


@job_group.command(name='delete')
@click.argument('job_id', type=int)
def job_delete_command(job_id: int) -> None:
	"""Delete an unwanted or spam job record from the database."""
	tracker = JobTracker()
	success = tracker.delete_job(job_id)
	if success:
		console.print(f'[bold green]Deleted Job #{job_id} from database.[/bold green]')
	else:
		console.print(f'[red]Job #{job_id} not found.[/red]')


# ==============================================================================
# PITCH & COVER LETTER GENERATOR
# ==============================================================================


@cli.command(name='pitch')
@click.argument('job_id', type=int)
def pitch_command(job_id: int) -> None:
	"""Generate a customized cover letter / intro pitch for a specific job."""
	tracker = JobTracker()
	job = tracker.get_job_by_id(job_id)
	if not job:
		console.print(f'[red]Job #{job_id} not found in database.[/red]')
		return

	user_profile = UserProfile.from_env_or_defaults()
	console.print(
		f"[bold cyan]Generating tailored pitch for {job.get('job_title')} at {job.get('company_name')}...[/bold cyan]"
	)

	llm = get_default_llm()
	from browser_use.llm.messages import UserMessage

	prompt = build_pitch_prompt(job, user_profile)

	async def _generate() -> str:
		response = await llm.ainvoke([UserMessage(content=prompt)])
		return str(
			response.completion if hasattr(response, 'completion') else getattr(response, 'output', str(response))
		).strip()

	pitch = asyncio.run(_generate())
	console.print(
		Panel(
			pitch,
			title=f"Custom Pitch: {job.get('job_title')} @ {job.get('company_name')}",
			border_style='green',
		)
	)


# ==============================================================================
# INTERVIEWS & COLD OUTREACH
# ==============================================================================


@cli.group(name='interview')
def interview_group() -> None:
	"""Manage and log interview invitations and meetings."""
	pass


@interview_group.command(name='list')
def interview_list_command() -> None:
	"""List all scheduled interviews and meeting links."""
	tracker = JobTracker()
	interviews = tracker.get_interviews()

	if not interviews:
		console.print('[yellow]No interviews scheduled yet.[/yellow]')
		return

	table = Table(title='[Scheduled Interviews]', border_style='green', box=box.ROUNDED)
	table.add_column('ID', style='dim')
	table.add_column('Company', style='bold cyan')
	table.add_column('Role', style='bold white')
	table.add_column('Date/Time', style='yellow')
	table.add_column('Type', style='magenta')
	table.add_column('Meeting Link', style='blue')
	table.add_column('Status', style='bold')

	for i in interviews:
		table.add_row(
			str(i['id']),
			i['company_name'],
			i['role'],
			i.get('scheduled_at') or 'TBD',
			i.get('interview_type') or 'video',
			i.get('meeting_link') or '—',
			i.get('status', 'scheduled'),
		)

	console.print(table)


@interview_group.command(name='log')
@click.option('--job-id', type=int, required=True, help='ID of the job record')
@click.option('--company', required=True, help='Company name')
@click.option('--role', required=True, help='Interview role title')
@click.option('--date', default=None, help='Scheduled date/time (e.g. 2026-10-15 15:00)')
@click.option('--meeting-link', default=None, help='Zoom/Google Meet link')
@click.option('--notes', default=None, help='Interviewer notes or prep points')
def interview_log_command(
	job_id: int,
	company: str,
	role: str,
	date: str | None,
	meeting_link: str | None,
	notes: str | None,
) -> None:
	"""Record an interview invitation for a job."""
	tracker = JobTracker()
	int_id = tracker.record_interview(
		job_id=job_id,
		company_name=company,
		role=role,
		scheduled_at=date,
		meeting_link=meeting_link,
		notes=notes,
	)
	console.print(f'[bold green]Successfully recorded Interview #{int_id} for {company}![/bold green]')


# ==============================================================================
# PROFILE & CONFIGURATION CHECKS
# ==============================================================================


@cli.group(name='profile')
def profile_group() -> None:
	"""View and inspect candidate profile and resume settings."""
	pass


@profile_group.command(name='show')
def profile_show_command() -> None:
	"""Display candidate profile information, skills, and resume paths."""
	user = UserProfile.from_env_or_defaults()

	profile_text = (
		f"[bold white]Name:[/bold white] {user.name}\n"
		f"[bold white]Current Role:[/bold white] {user.current_role}\n"
		f"[bold white]Current Employer:[/bold white] {user.current_company or 'Not specified'}\n"
		f"[bold white]Experience:[/bold white] {user.years_of_experience} years\n"
		f"[bold white]Location:[/bold white] {user.location}\n"
		f"[bold white]Email:[/bold white] {user.email}\n"
		f"[bold white]Phone:[/bold white] {user.phone}\n"
		f"[bold white]LinkedIn:[/bold white] {user.linkedin_url}\n"
		f"[bold white]GitHub:[/bold white] {user.github_url or 'None'}\n"
		f"[bold white]Portfolio:[/bold white] {user.portfolio_url or 'None'}\n"
		f"[bold white]Education:[/bold white] {user.education}\n"
		f"[bold white]Resume PDF:[/bold white] {user.resume_path} ({'[green]Found[/green]' if user.resume_path.exists() else '[red]Missing[/red]'})\n"
		f"[bold white]Resume Text:[/bold white] {user.resume_text_path} ({'[green]Found[/green]' if user.resume_text_path.exists() else '[red]Missing[/red]'})\n\n"
		f"[bold yellow]Core Skills ({len(user.skills)}):[/bold yellow]\n{', '.join(user.skills)}\n\n"
		f"[bold yellow]Executive Summary:[/bold yellow]\n{user.summary}"
	)

	console.print(Panel(profile_text, title='Candidate Active Profile', border_style='cyan'))


@cli.group(name='config')
def config_group() -> None:
	"""Verify system configuration, model endpoints, and browser setup."""
	pass


@config_group.command(name='check')
def config_check_command() -> None:
	"""Run health checks on environment variables, LLM model, and database."""
	console.print('[bold cyan]Running System Health Checks...[/bold cyan]\n')

	# Check 1: Environment & Keys
	user = UserProfile.from_env_or_defaults()
	model = os.getenv('OPENAI_MODEL', 'gpt-5.6-luna')
	base_url = os.getenv('OPENAI_BASE_URL', 'default')

	console.print(f"  [bold]AI Model:[/bold] {model}")
	console.print(f"  [bold]API Base URL:[/bold] {base_url}")
	console.print(
		f"  [bold]Candidate Name:[/bold] {user.name} ({'[green]OK[/green]' if user.name != 'Candidate Name' else '[yellow]Default[/yellow]'})"
	)
	console.print(
		f"  [bold]Resume PDF:[/bold] {user.resume_path} ({'[green]OK[/green]' if user.resume_path.exists() else '[red]Missing[/red]'})"
	)
	console.print(
		f"  [bold]Resume Text:[/bold] {user.resume_text_path} ({'[green]OK[/green]' if user.resume_text_path.exists() else '[red]Missing[/red]'})"
	)

	# Check 2: Database
	tracker = JobTracker()
	stats = tracker.get_stats()
	console.print(f"  [bold]Database Path:[/bold] {tracker.db_path} ([green]Connected[/green])")
	console.print(f"  [bold]Total Jobs in Database:[/bold] {stats['total_found']}")

	# Check 3: LLM Connectivity
	console.print('\n[bold cyan]Testing LLM Connectivity...[/bold cyan]')
	has_any_key = any(
		bool(os.getenv(k))
		for k in [
			'BROWSER_USE_API_KEY',
			'OPENAI_API_KEY',
			'ANTHROPIC_API_KEY',
			'GOOGLE_API_KEY',
			'GEMINI_API_KEY',
		]
	)
	if not has_any_key and not os.getenv('OPENAI_BASE_URL'):
		console.print(
			'  [bold yellow]No active LLM API key detected in environment. Please set OPENAI_API_KEY or BROWSER_USE_API_KEY in your .env file.[/bold yellow]'
		)
	else:
		try:
			llm = get_default_llm()
			from browser_use.llm.messages import UserMessage

			_ = asyncio.run(llm.ainvoke([UserMessage(content="Respond with 'OK'")]))
			console.print('  [bold green]LLM Connection Test: SUCCESS[/bold green]')
		except Exception as e:
			console.print(f'  [bold red]LLM Connection Test Failed: {e}[/bold red]')


# ==============================================================================
# EXPORT & INTERACTIVE REPL
# ==============================================================================


@cli.command(name='export')
@click.option(
	'--output',
	default='job_agent/data/jobs_export.csv',
	help='CSV export file destination',
)
def export_command(output: str) -> None:
	"""Export all jobs and tracking history to a CSV file."""
	tracker = JobTracker()
	out_path = Path(output)
	saved_file = tracker.export_to_csv(out_path)
	console.print(f'[bold green]Exported database to:[/bold green] {saved_file}')


@cli.command(name='interactive')
@click.option(
	'--url',
	default='https://wellfound.com/jobs',
	help='Starting URL for interactive browser session',
)
@click.option('--task', default=None, help='Initial task to run in the interactive browser')
def interactive_command(url: str, task: str | None) -> None:
	"""Launch a visible Chrome window with live in-browser demo panel and continuous REPL."""
	console.print(
		Panel.fit(
			'[bold cyan]Interactive Browser Session[/bold cyan]\n'
			f'[yellow]Initial URL:[/yellow] {url}\n'
			'[green]Features:[/green] Visible headful Chrome, In-Browser Demo Panel, Job Tools enabled.\n'
			"[cyan]Commands:[/cyan] Type any natural-language instruction, or 'q' to quit.",
			title='Job Agent Interactive',
			border_style='cyan',
		)
	)

	user_profile = UserProfile.from_env_or_defaults()
	preferences = JobPreferences(dry_run=True)
	tracker = JobTracker()
	tools = create_job_tools(tracker, user_profile, preferences)

	from browser_use import Agent, BrowserProfile, BrowserSession

	llm = get_default_llm()
	profile = BrowserProfile(
		headless=False,
		demo_mode=True,
		keep_alive=True,
		user_data_dir=os.getenv('CHROME_USER_DATA_DIR', '~/.config/browseruse/profiles/job_agent'),
		ignore_default_args=[
			'--disable-window-activation',
			'--disable-focus-on-load',
		],
	)

	first_task = task or f'Navigate to {url} and display the page.'

	async def run_interactive_session() -> None:
		session = BrowserSession(browser_profile=profile)
		try:
			agent = Agent(
				task=first_task,
				llm=llm,
				browser=session,
				tools=tools,
				demo_mode=True,
				use_vision=True,
				directly_open_url=False,
				extend_system_message="""
INTERACTIVE AGENT GUIDELINES:
- When inspecting job boards (LinkedIn, Wellfound, Naukri): click job cards one by one to load details.
- Extract title, company, URL, location, salary, and required skills, then call save_job.
- Check for recruiter/founder information and call save_hr_contact.
- Do not repeat element queries in a loop; click directly into listings to make progress.
""",
			)

			console.print(f'[bold green]Launching browser and executing:[/bold green] {first_task}')
			await agent.run()

			loop = asyncio.get_running_loop()
			while True:
				try:
					user_input = await loop.run_in_executor(
						None, lambda: input("\n👤 Enter command for browser agent (or 'q' to quit): ")
					)
					cmd = user_input.strip()
					if cmd.lower() in ('q', 'quit', 'exit'):
						console.print('[yellow]Exiting interactive session...[/yellow]')
						break
					if not cmd:
						continue

					console.print(f'[bold cyan]Executing:[/bold cyan] {cmd}')
					agent.add_new_task(cmd)
					await agent.run()
				except (KeyboardInterrupt, EOFError):
					break
		finally:
			await session.stop()
			console.print('[bold green]Browser session closed.[/bold green]')

	asyncio.run(run_interactive_session())


if __name__ == '__main__':
	cli()
