from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

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

from job_agent.config import (
	DEFAULT_DATA_DIR,
	DEFAULT_PROFILE_JSON,
	AgentConfig,
	JobPreferences,
	UserProfile,
)
from job_agent.database import JobTracker
from job_agent.orchestrator import JobAgentOrchestrator, get_default_llm
from job_agent.prompts.pitch_prompt import build_pitch_prompt
from job_agent.tools.job_tools import create_job_tools

console = Console(legacy_windows=False)
logger = logging.getLogger(__name__)


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
	'--platform',
	default='wellfound',
	help='Platform to authenticate (wellfound, linkedin, gmail, naukri, all)',
)
@click.option('--platforms', default=None, help='Comma-separated platforms to open')
@click.option('--email', default=None, help='Account email to auto-fill')
@click.option('--password', default=None, help='Account password to auto-fill')
@click.option('--manual', is_flag=True, help='Log in manually in the Chrome browser window')
def auth_command(
	platform: str,
	platforms: str | None,
	email: str | None,
	password: str | None,
	manual: bool,
) -> None:
	"""Log into job platforms and webmail. Supports auto-login or manual browser login, saving session state permanently."""
	platform_urls = {
		'wellfound': 'https://wellfound.com/login',
		'linkedin': 'https://www.linkedin.com/login',
		'gmail': 'https://mail.google.com/',
		'naukri': 'https://www.naukri.com/nlogin/login',
	}

	raw_target = platforms or platform
	if raw_target.lower() == 'all':
		targets = ['wellfound', 'linkedin', 'gmail']
	else:
		targets = [p.strip().lower() for p in raw_target.split(',') if p.strip()]

	primary_platform = targets[0] if targets else 'wellfound'
	initial_url = platform_urls.get(primary_platform, 'https://wellfound.com/login')

	from rich.prompt import Prompt

	if not manual and not email:
		console.print(
			Panel.fit(
				f'[bold cyan]🔑 Session Authenticator for {primary_platform.title()}[/bold cyan]\n\n'
				'Choose how you want to log in:\n'
				'  [bold yellow]1.[/bold yellow] [bold white]Auto-Fill:[/bold white] Enter credentials here in terminal, agent types them into Chrome for you\n'
				'  [bold yellow]2.[/bold yellow] [bold white]Manual Browser:[/bold white] Open Chrome and click/type into the web page yourself',
				title='Login Method',
				border_style='cyan',
			)
		)
		choice = Prompt.ask('Choose option', choices=['1', '2'], default='1')
		if choice == '1':
			email = Prompt.ask(f'Enter {primary_platform.title()} email')
			password = Prompt.ask(f'Enter {primary_platform.title()} password', password=True)
		else:
			manual = True

	from browser_use import BrowserProfile, BrowserSession
	from job_agent.config import AgentConfig

	agent_config = AgentConfig()
	agent_config.clean_profile_locks()
	agent_config.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

	storage_arg = str(agent_config.storage_state_path) if agent_config.storage_state_path.exists() else None
	profile = BrowserProfile(
		user_data_dir=agent_config.chrome_user_data_dir,
		storage_state=storage_arg,
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
		console.print(f'\n[cyan]🌐 Opening Chrome at {initial_url}...[/cyan]')
		await session.navigate(initial_url)

		for p in targets[1:]:
			url = platform_urls.get(p)
			if url:
				await session.create_new_tab(url)

		if email and password and not manual:
			console.print(f'[cyan]⚡ Auto-injecting credentials for {primary_platform.title()}...[/cyan]')
			await asyncio.sleep(1.5)
			safe_email = json.dumps(email)
			safe_pass = json.dumps(password)
			inject_script = f"""
			(function() {{
				const emailSelectors = [
					'input[type="email"]', 'input[name="email"]', 'input[name="session_key"]',
					'input#user_email', 'input#email', 'input[autocomplete="username"]', 'input[autocomplete="email"]'
				];
				const passSelectors = [
					'input[type="password"]', 'input[name="password"]', 'input[name="session_password"]',
					'input#user_password', 'input#password', 'input[autocomplete="current-password"]'
				];
				let eField = null, pField = null;
				for (const s of emailSelectors) {{
					const el = document.querySelector(s);
					if (el && el.offsetParent !== null) {{ eField = el; break; }}
				}}
				for (const s of passSelectors) {{
					const el = document.querySelector(s);
					if (el && el.offsetParent !== null) {{ pField = el; break; }}
				}}
				if (eField) {{
					eField.focus();
					eField.value = {safe_email};
					eField.dispatchEvent(new Event('input', {{ bubbles: true }}));
					eField.dispatchEvent(new Event('change', {{ bubbles: true }}));
				}}
				if (pField) {{
					pField.focus();
					pField.value = {safe_pass};
					pField.dispatchEvent(new Event('input', {{ bubbles: true }}));
					pField.dispatchEvent(new Event('change', {{ bubbles: true }}));
				}}
				return {{ emailFilled: !!eField, passFilled: !!pField }};
			}})();
			"""
			try:
				res = await session.execute_javascript(inject_script)
				if res and res.get('emailFilled') and res.get('passFilled'):
					console.print('[bold green]✓ Credentials entered into login fields![/bold green]')
					console.print('[dim]Click "Log In" or submit if needed.[/dim]')
				else:
					console.print(
						'[yellow]Note: Login fields did not accept script autofill. Please enter credentials in Chrome.[/yellow]'
					)
			except Exception as fill_err:
				logger.debug(f'Auto-fill notice: {fill_err}')

		console.print(
			Panel.fit(
				f'[bold green]Chrome is active at {initial_url}![/bold green]\n\n'
				'1. In the Chrome window, complete your login (or Google SSO / 2FA / Captcha if required).\n'
				'2. [bold cyan]Auto-Detection Active:[/bold cyan] As soon as the page redirects to your feed or dashboard, the session will auto-save!\n'
				'3. Or press [bold yellow][Enter][/bold yellow] here anytime once you are logged in.',
				title='Session Login Assistant',
				border_style='green',
			)
		)

		login_detected = False
		loop = asyncio.get_running_loop()

		async def _watchdog() -> None:
			nonlocal login_detected
			for _ in range(120):
				await asyncio.sleep(1.5)
				try:
					cur_url = await session.get_current_url()
					if not cur_url:
						continue
					cur_lower = cur_url.lower()
					if (
						('wellfound.com' in cur_lower and '/login' not in cur_lower and '/auth' not in cur_lower)
						or (
							'linkedin.com' in cur_lower
							and '/login' not in cur_lower
							and '/checkpoint' not in cur_lower
							and '/uas/' not in cur_lower
						)
						or ('google.com' in cur_lower and '/signin' not in cur_lower and '/auth' not in cur_lower)
					):
						console.print(f'\n[bold green]🎉 Login detected! Redirected to: {cur_url}[/bold green]')
						login_detected = True
						return
				except Exception:
					pass

		watchdog_task = asyncio.create_task(_watchdog())

		async def _wait_manual() -> None:
			nonlocal login_detected
			await loop.run_in_executor(
				None,
				lambda: Prompt.ask('\n[bold yellow]👉 Press [Enter] once logged in inside Chrome[/bold yellow]', default=''),
			)
			login_detected = True

		manual_task = asyncio.create_task(_wait_manual())

		done, pending = await asyncio.wait(
			[watchdog_task, manual_task],
			return_when=asyncio.FIRST_COMPLETED,
		)
		for t in pending:
			t.cancel()

		console.print('\n[cyan]Finalizing and saving browser session state...[/cyan]')
		await asyncio.sleep(2)
		try:
			await session.export_storage_state(agent_config.storage_state_path)
		except Exception as ex:
			logger.warning(f'Could not export storage state: {ex}')

		await session.stop()
		console.print(f'[bold green]✅ Success! Browser session saved to: {agent_config.storage_state_path}[/bold green]')
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
@click.option('--platforms', default='wellfound,linkedin', help='Comma-separated target job boards')
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
@click.option('--years-exp', default=None, type=float, help='Candidate years of experience (e.g. 0.5, 1, 2)')
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
	years_exp: float | None,
	login_first: bool,
) -> None:
	"""Autonomous end-to-end recruitment agent with zero credentials and Free / Ask mode."""
	selected_mode = 'free' if flag_free else ('ask' if flag_ask else mode.lower())

	agent_config = AgentConfig()
	storage_file = agent_config.storage_state_path

	user_profile = UserProfile.from_env_or_defaults()
	if years_exp is not None:
		user_profile.years_of_experience = years_exp

	if login_first:
		ctx = click.get_current_context()
		ctx.invoke(auth_command, platform=platforms.split(',')[0].strip())
	elif not storage_file.exists():
		console.print(
			'[yellow]Notice: No saved browser session found in storage state. '
			'Using persistent Chrome profile directory. (Run "job-agent auth" anytime to save session state)[/yellow]'
		)

	console.print(
		Panel.fit(
			f'[bold cyan]🤖 Autonomous Job Application Agent[/bold cyan]\n\n'
			f'[yellow]Operating Mode:[/yellow] '
			f'{"[bold green]FREE MODE (100% Autonomous Autopilot)[/bold green]" if selected_mode == "free" else "[bold yellow]ASK MODE (Human Confirmation Before Submit/Send)[/bold yellow]"}\n'
			f'[yellow]Submission Mode:[/yellow] {"[dim]DRY RUN (Simulated)[/dim]" if dry_run else "[bold red]LIVE SUBMISSION[/bold red]"}\n'
			f'[yellow]Candidate Experience:[/yellow] [bold cyan]{user_profile.years_of_experience:.1f} years[/bold cyan]\n'
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
			hr_info += f' ({j["hr_email"]})'
		table.add_row(
			str(j['id']),
			(j.get('job_title') or 'Unknown')[:30],
			(j.get('company_name') or 'Unknown')[:20],
			str(j.get('platform') or 'other').upper(),
			(j.get('location') or 'Remote')[:18],
			f'{j.get("match_score", 0):.0f}%',
			hr_info[:22] if hr_info else '—',
			str(j.get('status') or 'found'),
		)

	console.print(table)


@cli.command(name='clean')
@click.option(
	'--min-fit',
	default=45.0,
	type=float,
	help='Purge unapplied jobs with match score below this percentage',
)
@click.option(
	'--yes',
	'-y',
	is_flag=True,
	help='Skip confirmation prompt and immediately clean',
)
def clean_command(min_fit: float, yes: bool) -> None:
	"""Clean and purge non-relevant jobs below minimum fit score from database."""
	tracker = JobTracker()
	with tracker._get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(
			"SELECT COUNT(*) FROM jobs WHERE match_score < ? AND status NOT IN ('applied', 'interview', 'offer')",
			(min_fit,),
		)
		count = cursor.fetchone()[0]

	if count == 0:
		console.print(f'[green]Database is clean! No unapplied jobs found with match score < {min_fit:.0f}%.[/green]')
		return

	if not yes:
		from rich.prompt import Confirm

		confirmed = Confirm.ask(
			f'Found {count} non-relevant job(s) with match score < {min_fit:.0f}%. Delete them from database?',
			default=True,
		)
		if not confirmed:
			console.print('[yellow]Aborted clean.[/yellow]')
			return

	deleted = tracker.delete_jobs_below_fit_score(min_fit)
	console.print(f'[bold green]Successfully deleted {deleted} non-relevant job(s) from database.[/bold green]')


@cli.command(name='reset-db')
@click.option(
	'--yes',
	'-y',
	is_flag=True,
	help='Skip confirmation prompt and immediately reset the database',
)
def reset_db_command(yes: bool) -> None:
	"""Completely reset the database, erasing all job records, emails, and tracking history."""
	if not yes:
		from rich.prompt import Confirm

		confirmed = Confirm.ask(
			'[bold red]WARNING: This will permanently erase ALL jobs, applications, and interview records from the database. Are you sure?[/bold red]',
			default=False,
		)
		if not confirmed:
			console.print('[yellow]Aborted database reset.[/yellow]')
			return

	tracker = JobTracker()
	tracker.reset_database()
	console.print('[bold green]Database has been completely reset and initialized to a clean state.[/bold green]')


@cli.command(name='doctor')
def doctor_command() -> None:
	"""Diagnose system readiness, Chrome installation, LLM connectivity, resume, and authentication status."""
	console.print(
		Panel.fit(
			'[bold cyan]🩺 Job Agent Environment & Authentication Diagnostics[/bold cyan]\n'
			'Checking browser environment, LLM connectivity, profiles, and saved sessions...',
			title='System Doctor',
			border_style='cyan',
		)
	)

	table = Table(title='Diagnostic Results', border_style='cyan', box=box.ROUNDED)
	table.add_column('Component', style='bold white', width=22)
	table.add_column('Status', width=12)
	table.add_column('Details', style='dim')

	agent_config = AgentConfig()

	# 1. Check Chrome / Chromium
	import shutil

	chrome_exec = shutil.which('google-chrome') or shutil.which('chrome') or shutil.which('chromium')
	if not chrome_exec and sys.platform == 'win32':
		win_paths = [
			Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
			Path(r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'),
			Path(os.path.expanduser(r'~\AppData\Local\Google\Chrome\Application\chrome.exe')),
		]
		for p in win_paths:
			if p.exists():
				chrome_exec = str(p)
				break

	if chrome_exec:
		table.add_row('Google Chrome', '[bold green]PASS[/bold green]', f'Found at {chrome_exec}')
	else:
		table.add_row('Google Chrome', '[bold yellow]DETECT[/bold yellow]', 'Default browser path detected via Playwright/CDP')

	# Check Profile Directory & Clean Locks
	agent_config.clean_profile_locks()
	p_dir = Path(os.path.expanduser(agent_config.chrome_user_data_dir or ''))
	if p_dir.exists():
		table.add_row('Chrome Profile', '[bold green]PASS[/bold green]', f'Active profile directory ({p_dir})')
	else:
		table.add_row('Chrome Profile', '[bold cyan]INFO[/bold cyan]', f'Will be auto-created on first run at {p_dir}')

	# 2. Check LLM Connectivity
	llm = get_default_llm()
	llm_name = llm.__class__.__name__
	from browser_use.llm.messages import UserMessage

	async def test_llm():
		import time

		t0 = time.time()
		try:
			await asyncio.wait_for(llm.ainvoke([UserMessage(content='ping')]), timeout=8.0)
			latency_ms = int((time.time() - t0) * 1000)
			return True, f'Connected ({llm_name}, latency: {latency_ms}ms)'
		except Exception as ex:
			return False, f'Error: {ex}'

	try:
		ok, msg = asyncio.run(test_llm())
		table.add_row('LLM Connectivity', '[bold green]PASS[/bold green]' if ok else '[bold red]FAIL[/bold red]', msg)
	except Exception as ex:
		table.add_row('LLM Connectivity', '[bold red]FAIL[/bold red]', str(ex))

	# 3. Resume & Candidate Profile
	user_profile = UserProfile.from_env_or_defaults()
	if user_profile.resume_path.exists():
		sz = user_profile.resume_path.stat().st_size // 1024
		table.add_row('Resume PDF', '[bold green]PASS[/bold green]', f'Found ({sz} KB) at {user_profile.resume_path}')
	else:
		table.add_row(
			'Resume PDF', '[bold yellow]WARN[/bold yellow]', f'Missing at {user_profile.resume_path}. Add resume.pdf to apply'
		)

	if user_profile.resume_text_path.exists():
		table.add_row('Resume Text', '[bold green]PASS[/bold green]', f'Found at {user_profile.resume_text_path}')
	else:
		table.add_row('Resume Text', '[bold cyan]INFO[/bold cyan]', 'Using structured UserProfile defaults')

	profile_src = 'user_profile.json' if DEFAULT_PROFILE_JSON.exists() else 'Auto-parsed / Env'
	table.add_row(
		'Candidate Profile',
		'[bold green]PASS[/bold green]',
		f'{user_profile.name} ({user_profile.current_role}, {user_profile.years_of_experience:.1f} yrs exp, src: {profile_src})',
	)

	# 4. Storage State & Saved Authentication
	state_path = agent_config.storage_state_path
	if state_path.exists():
		try:
			data = json.loads(state_path.read_text(encoding='utf-8'))
			cookies = data.get('cookies', [])
			cookie_domains = {c.get('domain', '') for c in cookies}
			has_wf = any('wellfound' in d for d in cookie_domains)
			has_li = any('linkedin' in d for d in cookie_domains)
			has_gg = any('google' in d for d in cookie_domains)

			auth_summary = []
			if has_wf:
				auth_summary.append('Wellfound')
			if has_li:
				auth_summary.append('LinkedIn')
			if has_gg:
				auth_summary.append('Google/Gmail')

			details = f'{len(cookies)} cookies stored'
			if auth_summary:
				details += f' (Logged into: {", ".join(auth_summary)})'
			table.add_row('Saved Auth Session', '[bold green]PASS[/bold green]', details)
		except Exception as ex:
			table.add_row('Saved Auth Session', '[bold yellow]WARN[/bold yellow]', f'Invalid JSON in {state_path}: {ex}')
	else:
		table.add_row(
			'Saved Auth Session', '[bold yellow]NOT SAVED[/bold yellow]', 'No saved session yet. Run "job-agent auth" to log in.'
		)

	# 5. Database Health
	tracker = JobTracker()
	try:
		stats = tracker.get_stats()
		table.add_row(
			'SQLite Database',
			'[bold green]PASS[/bold green]',
			f'{stats["total_found"]} jobs found, {stats["total_applied"]} applied ({tracker.db_path})',
		)
	except Exception as ex:
		table.add_row('SQLite Database', '[bold red]FAIL[/bold red]', str(ex))

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
		f'[bold white]Title:[/bold white] {j.get("job_title")}\n'
		f'[bold white]Company:[/bold white] [bold cyan]{j.get("company_name")}[/bold cyan]\n'
		f'[bold white]Platform:[/bold white] {str(j.get("platform")).upper()}\n'
		f'[bold white]Location:[/bold white] {j.get("location") or "Remote"}\n'
		f'[bold white]Salary / Equity:[/bold white] {j.get("salary_range") or "Not disclosed"}\n'
		f'[bold white]URL:[/bold white] [underline blue]{j.get("job_url")}[/underline blue]\n'
		f'[bold white]Application Type:[/bold white] {j.get("application_type")}\n'
		f'[bold white]Status:[/bold white] {j.get("status")}\n'
		f'[bold white]Match Score:[/bold white] [bold green]{j.get("match_score", 0):.1f}%[/bold green]\n'
		f'[bold white]Recruiter / HR:[/bold white] {j.get("hr_name") or "None"}\n'
		f'[bold white]HR Email:[/bold white] {j.get("hr_email") or "None"}\n'
		f'[bold white]HR LinkedIn:[/bold white] {j.get("hr_linkedin") or "None"}\n\n'
		f'[bold yellow]Required Skills & Stack:[/bold yellow]\n{skills_str}\n\n'
		f'[bold yellow]Job Description Summary:[/bold yellow]\n{j.get("job_description_summary") or "None"}\n\n'
		f'[bold yellow]Notes / Feedback:[/bold yellow]\n{j.get("notes") or "None"}'
	)

	console.print(Panel(details, title=f'Job Record #{j["id"]}', border_style='cyan'))


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
	console.print(f'[bold cyan]Generating tailored pitch for {job.get("job_title")} at {job.get("company_name")}...[/bold cyan]')

	llm = get_default_llm()
	from browser_use.llm.messages import UserMessage

	prompt = build_pitch_prompt(job, user_profile)

	async def _generate() -> str:
		response = await llm.ainvoke([UserMessage(content=prompt)])
		return str(response.completion if hasattr(response, 'completion') else getattr(response, 'output', str(response))).strip()

	pitch = asyncio.run(_generate())
	console.print(
		Panel(
			pitch,
			title=f'Custom Pitch: {job.get("job_title")} @ {job.get("company_name")}',
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
	source_label = f'Saved JSON ({DEFAULT_PROFILE_JSON})' if DEFAULT_PROFILE_JSON.exists() else 'Auto-parsed / Env'

	profile_text = (
		f'[bold white]Name:[/bold white] {user.name}\n'
		f'[bold white]Current Role:[/bold white] {user.current_role}\n'
		f'[bold white]Current Employer:[/bold white] {user.current_company or "Not specified"}\n'
		f'[bold white]Experience:[/bold white] {user.years_of_experience} years\n'
		f'[bold white]Location:[/bold white] {user.location}\n'
		f'[bold white]Email:[/bold white] {user.email}\n'
		f'[bold white]Phone:[/bold white] {user.phone}\n'
		f'[bold white]LinkedIn:[/bold white] {user.linkedin_url}\n'
		f'[bold white]GitHub:[/bold white] {user.github_url or "None"}\n'
		f'[bold white]Portfolio:[/bold white] {user.portfolio_url or "None"}\n'
		f'[bold white]Education:[/bold white] {user.education}\n'
		f'[bold white]Profile Source:[/bold white] [green]{source_label}[/green]\n'
		f'[bold white]Resume PDF:[/bold white] {user.resume_path} ({"[green]Found[/green]" if user.resume_path.exists() else "[red]Missing[/red]"})\n'
		f'[bold white]Resume Text:[/bold white] {user.resume_text_path} ({"[green]Found[/green]" if user.resume_text_path.exists() else "[red]Missing[/red]"})\n\n'
		f'[bold yellow]Core Skills ({len(user.skills)}):[/bold yellow]\n{", ".join(user.skills)}\n\n'
		f'[bold yellow]Executive Summary:[/bold yellow]\n{user.summary}'
	)

	console.print(Panel(profile_text, title='Candidate Active Profile', border_style='cyan'))


def run_interactive_setup(
	resume: str | None = None,
	auto: bool = False,
	clean: bool = False,
) -> None:
	"""Interactive candidate onboarding wizard."""
	console.print(
		Panel.fit(
			'[bold cyan]🛠️  Job Agent Interactive Profile Setup Wizard[/bold cyan]\n'
			'Interactively configure your candidate profile and job preferences.\n'
			'[dim]All details are saved locally in data/user_profile.json — no personal info needed in .env![/dim]',
			title='Candidate Setup',
			border_style='cyan',
		)
	)

	target_resume: Path | None = None
	if resume:
		p = Path(resume)
		if p.exists():
			target_resume = p
		else:
			console.print(f'[bold red]Error:[/bold red] Resume file not found at {p}')
			if not click.confirm('Continue without parsing resume?', default=True):
				return
	elif not clean:
		for candidate in [
			DEFAULT_DATA_DIR / 'resume.txt',
			DEFAULT_DATA_DIR / 'resume.pdf',
			Path('job_agent/data/resume.txt'),
			Path('job_agent/data/resume.pdf'),
		]:
			if candidate.exists():
				target_resume = candidate
				break

	current_profile = UserProfile.from_env_or_defaults() if (DEFAULT_PROFILE_JSON.exists() and not clean) else UserProfile()

	extracted_data: dict[str, Any] = {}
	if target_resume and target_resume.exists() and not clean:
		console.print(f'\n[bold cyan]📄 Resume Found:[/bold cyan] {target_resume}')
		with console.status('[bold green]Parsing resume and calculating experience years...[/bold green]'):
			try:
				from job_agent.services.resume_parser import ResumeParser

				extracted_data = ResumeParser.parse(target_resume, use_llm=True)
				console.print(
					f'[bold green]✓ Successfully parsed resume![/bold green] '
					f'Name: [bold white]{extracted_data.get("name")}[/bold white], '
					f'Experience: [bold cyan]{extracted_data.get("years_of_experience")} years[/bold cyan], '
					f'Role: [bold white]{extracted_data.get("current_role")}[/bold white]'
				)
			except Exception as e:
				console.print(f'[yellow]⚠️ Resume auto-parsing encountered an issue: {e}[/yellow]')

	def get_val(key: str, default: Any) -> Any:
		if key in extracted_data and extracted_data[key]:
			return extracted_data[key]
		curr = getattr(current_profile, key, None)
		if curr:
			return curr
		return default

	if auto:
		name = get_val('name', 'Abhishek Jangid')
		role = get_val('current_role', 'Full Stack Developer')
		company = get_val('current_company', None)
		exp = float(get_val('years_of_experience', 0.7))
		email = get_val('email', 'abhishekjangid3489@gmail.com')
		phone = get_val('phone', '+919799219379')
		loc = get_val('location', 'Remote')
		linkedin = get_val('linkedin_url', 'https://www.linkedin.com/in/candidate')
		github = get_val('github_url', None)
		portfolio = get_val('portfolio_url', None)
		skills = get_val('skills', ['TypeScript', 'Python', 'React', 'FastAPI'])
		summary = get_val('summary', 'Software engineer specializing in AI agents and web automation.')
		education = get_val('education', 'Computer Science')

		profile = UserProfile(
			name=name,
			current_role=role,
			current_company=company,
			years_of_experience=exp,
			email=email,
			phone=phone,
			location=loc,
			linkedin_url=linkedin,
			github_url=github,
			portfolio_url=portfolio,
			skills=skills,
			summary=summary,
			education=education,
			resume_path=target_resume
			if target_resume and target_resume.suffix.lower() == '.pdf'
			else current_profile.resume_path,
			resume_text_path=target_resume
			if target_resume and target_resume.suffix.lower() != '.pdf'
			else current_profile.resume_text_path,
		)
		saved_p = profile.save_to_file()
		console.print(f'\n[bold green]✓ Auto-saved candidate profile to {saved_p}![/bold green]')
		return

	console.print('\n[bold yellow]Step 1: Candidate Professional Details[/bold yellow]')
	console.print('[dim]Press Enter to accept the value in brackets, or type to edit:[/dim]\n')

	name = click.prompt('Full Name', default=get_val('name', 'Abhishek Jangid'), type=str)
	current_role = click.prompt('Current Role / Title', default=get_val('current_role', 'Full Stack Developer'), type=str)
	raw_company = click.prompt('Current Employer / Company', default=str(get_val('current_company', 'None') or 'None'), type=str)
	current_company = None if raw_company.strip().lower() in ('none', 'n/a', '') else raw_company.strip()

	years_of_exp_raw = click.prompt(
		'Total Experience in Years (e.g. 0.5, 0.7, 1.0, 2.0)',
		default=float(get_val('years_of_experience', 0.7)),
		type=float,
	)
	years_of_exp = max(0.0, float(years_of_exp_raw))

	email = click.prompt('Primary Email', default=get_val('email', 'abhishekjangid3489@gmail.com'), type=str)
	phone = click.prompt('Phone Number', default=get_val('phone', '+919799219379'), type=str)
	location = click.prompt('Location / City', default=get_val('location', 'Jodhpur, Rajasthan, India'), type=str)

	linkedin_url = click.prompt(
		'LinkedIn Profile URL', default=get_val('linkedin_url', 'https://www.linkedin.com/in/abhishek-jangid-3532b1323'), type=str
	)
	raw_github = click.prompt(
		'GitHub Profile URL', default=str(get_val('github_url', 'https://github.com/abhishekkkk-15') or 'None'), type=str
	)
	github_url = None if raw_github.strip().lower() in ('none', 'n/a', '') else raw_github.strip()

	raw_port = click.prompt(
		'Portfolio / Project URL', default=str(get_val('portfolio_url', 'https://cloud-agent.abhishekkkk.in') or 'None'), type=str
	)
	portfolio_url = None if raw_port.strip().lower() in ('none', 'n/a', '') else raw_port.strip()

	education = click.prompt('Education', default=get_val('education', 'Master of Computer Applications (MCA)'), type=str)

	curr_skills = get_val('skills', ['TypeScript', 'Python', 'React', 'FastAPI', 'Node.js', 'PostgreSQL', 'Docker', 'AI Agents'])
	skills_default = ', '.join(curr_skills) if isinstance(curr_skills, list) else str(curr_skills)
	skills_input = click.prompt('Core Skills (comma-separated)', default=skills_default, type=str)
	skills = [s.strip() for s in skills_input.split(',') if s.strip()]

	summary = click.prompt(
		'Professional Summary / Pitch',
		default=get_val(
			'summary',
			'Applied AI Engineer specializing in autonomous coding agents, backend systems, and modern web applications.',
		),
		type=str,
	)

	console.print('\n[bold yellow]Step 2: Job Preferences & Autopilot Settings[/bold yellow]\n')
	pref = JobPreferences.from_env_or_defaults()

	roles_input = click.prompt('Target Roles (comma-separated)', default=', '.join(pref.target_roles), type=str)
	target_roles = [r.strip() for r in roles_input.split(',') if r.strip()]

	locs_input = click.prompt('Target Locations (comma-separated)', default=', '.join(pref.target_locations), type=str)
	target_locations = [loc.strip() for loc in locs_input.split(',') if loc.strip()]

	platforms_input = click.prompt(
		'Target Platforms (comma-separated: wellfound, linkedin, naukri)', default=', '.join(pref.platforms), type=str
	)
	target_platforms = [
		p.strip().lower() for p in platforms_input.split(',') if p.strip().lower() in ('wellfound', 'linkedin', 'naukri')
	]
	if not target_platforms:
		target_platforms = ['wellfound', 'linkedin', 'naukri']

	mode = click.prompt(
		'Operating Mode (ask = confirm before submit; free = autonomous autopilot)',
		default=pref.mode,
		type=click.Choice(['ask', 'free']),
	)
	max_apps = click.prompt('Max Applications Per Run', default=pref.max_applications_per_run, type=int)
	min_fit = click.prompt('Minimum Fit Score % (strict relevance threshold)', default=pref.min_fit_score, type=float)

	profile = UserProfile(
		name=name,
		current_role=current_role,
		current_company=current_company,
		years_of_experience=years_of_exp,
		email=email,
		phone=phone,
		location=location,
		linkedin_url=linkedin_url,
		github_url=github_url,
		portfolio_url=portfolio_url,
		skills=skills,
		summary=summary,
		education=education,
		resume_path=target_resume if target_resume and target_resume.suffix.lower() == '.pdf' else current_profile.resume_path,
		resume_text_path=target_resume
		if target_resume and target_resume.suffix.lower() != '.pdf'
		else current_profile.resume_text_path,
	)

	preferences = JobPreferences(
		target_roles=target_roles,
		target_locations=target_locations,
		platforms=target_platforms,
		mode=mode,
		max_applications_per_run=max_apps,
		min_fit_score=min_fit,
		experience_level='entry_level' if years_of_exp <= 1.5 else ('associate' if years_of_exp <= 3.5 else 'mid_senior'),
	)

	profile_path = profile.save_to_file()
	pref_path = preferences.save_to_file()

	summary_card = (
		f'[bold white]Candidate:[/bold white] {profile.name} ([cyan]{profile.current_role}[/cyan])\n'
		f'[bold white]Experience:[/bold white] [bold cyan]{profile.years_of_experience:.1f} years[/bold cyan]\n'
		f'[bold white]Company:[/bold white] {profile.current_company or "None"}\n'
		f'[bold white]Contact:[/bold white] {profile.email} | {profile.phone}\n'
		f'[bold white]Location:[/bold white] {profile.location}\n'
		f'[bold white]LinkedIn:[/bold white] {profile.linkedin_url}\n'
		f'[bold white]Skills ({len(profile.skills)}):[/bold white] {", ".join(profile.skills[:8])}...\n\n'
		f'[bold yellow]Target Roles:[/bold yellow] {", ".join(preferences.target_roles)}\n'
		f'[bold yellow]Target Locations:[/bold yellow] {", ".join(preferences.target_locations)}\n'
		f'[bold yellow]Platforms:[/bold yellow] {", ".join(preferences.platforms)}\n'
		f'[bold yellow]Mode:[/bold yellow] {preferences.mode.upper()} | [bold yellow]Min Fit Score:[/bold yellow] {preferences.min_fit_score}%\n\n'
		f'[bold green]Saved Local Files:[/bold green]\n'
		f'  ✓ Candidate Profile: {profile_path}\n'
		f'  ✓ Preferences: {pref_path}'
	)

	console.print('\n')
	console.print(Panel(summary_card, title='🎉 Setup Completed Successfully', border_style='green'))
	console.print(
		'[bold green]✓ Done![/bold green] Your details are saved locally. You do NOT need to keep personal information in .env.'
	)
	console.print(
		'Run [bold cyan]uv run job-agent doctor[/bold cyan] to verify system health or [bold cyan]uv run job-agent auto --dry-run[/bold cyan] to test applications.\n'
	)


@cli.command(name='setup')
@click.option('--resume', default=None, help='Path to resume file (PDF or TXT) to auto-extract details from')
@click.option('--auto', is_flag=True, help='Auto-save extracted resume details without interactive prompts')
@click.option('--clean', is_flag=True, help='Start with clean empty fields instead of prefilling existing profile')
def setup_cli_command(resume: str | None, auto: bool, clean: bool) -> None:
	"""Interactively configure candidate profile and job preferences without editing .env."""
	run_interactive_setup(resume=resume, auto=auto, clean=clean)


@profile_group.command(name='setup')
@click.option('--resume', default=None, help='Path to resume file (PDF or TXT) to auto-extract details from')
@click.option('--auto', is_flag=True, help='Auto-save extracted resume details without interactive prompts')
@click.option('--clean', is_flag=True, help='Start with clean empty fields instead of prefilling existing profile')
def profile_setup_command(resume: str | None, auto: bool, clean: bool) -> None:
	"""Interactively configure candidate profile and job preferences."""
	run_interactive_setup(resume=resume, auto=auto, clean=clean)


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

	console.print(f'  [bold]AI Model:[/bold] {model}')
	console.print(f'  [bold]API Base URL:[/bold] {base_url}')
	console.print(
		f'  [bold]Candidate Name:[/bold] {user.name} ({"[green]OK[/green]" if user.name != "Candidate Name" else "[yellow]Default[/yellow]"})'
	)
	console.print(
		f'  [bold]Resume PDF:[/bold] {user.resume_path} ({"[green]OK[/green]" if user.resume_path.exists() else "[red]Missing[/red]"})'
	)
	console.print(
		f'  [bold]Resume Text:[/bold] {user.resume_text_path} ({"[green]OK[/green]" if user.resume_text_path.exists() else "[red]Missing[/red]"})'
	)

	# Check 2: Database
	tracker = JobTracker()
	stats = tracker.get_stats()
	console.print(f'  [bold]Database Path:[/bold] {tracker.db_path} ([green]Connected[/green])')
	console.print(f'  [bold]Total Jobs in Database:[/bold] {stats["total_found"]}')

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
