from __future__ import annotations

import asyncio
import logging
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
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from job_agent.config import JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.orchestrator import JobAgentOrchestrator
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
	default='Software Engineer,Backend Developer',
	help='Comma-separated target role keywords',
)
@click.option(
	'--locations',
	default='Remote,Bangalore',
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
	"""Execute the complete end-to-end job hunt pipeline."""
	console.print(
		Panel.fit(
			f'[bold cyan]AI Job Application Agent[/bold cyan]\n'
			f'[yellow]Mode:[/yellow] {"[bold green]DRY RUN (Simulated)[/bold green]" if dry_run else "[bold red]LIVE SUBMISSION[/bold red]"}\n'
			f'[yellow]Platforms:[/yellow] {platforms}\n'
			f'[yellow]Roles:[/yellow] {roles}\n'
			f'[yellow]Locations:[/yellow] {locations}\n'
			f'[yellow]Max Applications:[/yellow] {max_apply}',
			title='Job Agent Runner',
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
@click.option('--platforms', default='linkedin,wellfound,naukri', help='Platforms to search')
@click.option('--roles', default='Software Engineer', help='Role keywords')
@click.option('--location', default='Remote', help='Location')
def search_command(platforms: str, roles: str, location: str) -> None:
	"""Execute Phase 1: Search job boards and catalog vacancies into database."""
	console.print('[bold cyan]Searching job boards...[/bold cyan]')
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
	console.print(f'[bold green]Search complete:[/bold green] {results}')


@cli.command(name='apply')
@click.option('--dry-run/--live', default=True, help='Simulate form filling or submit live')
@click.option('--limit', default=5, type=int, help='Max jobs to apply for')
def apply_command(dry_run: bool, limit: int) -> None:
	"""Execute Phase 3: Apply to discovered pending jobs."""
	console.print(f'[bold cyan]Applying to up to {limit} pending jobs (Dry Run: {dry_run})...[/bold cyan]')
	user_profile = UserProfile.from_env_or_defaults()
	preferences = JobPreferences(dry_run=dry_run, max_applications_per_run=limit)

	orchestrator = JobAgentOrchestrator(
		user_profile=user_profile,
		preferences=preferences,
	)
	count = asyncio.run(orchestrator.run_apply_only(limit=limit))
	console.print(f'[bold green]Applied to {count} jobs.[/bold green]')


@cli.command(name='extract')
@click.option('--limit', default=10, type=int, help='Number of postings to research')
def extract_command(limit: int) -> None:
	"""Execute Phase 2: Find HR contacts and recruiter info for pending jobs."""
	console.print(f'[bold cyan]Extracting recruiter contacts for up to {limit} jobs...[/bold cyan]')
	orchestrator = JobAgentOrchestrator()
	count = asyncio.run(orchestrator.run_extract_only(limit=limit))
	console.print(f'[bold green]Processed contacts for {count} postings.[/bold green]')


@cli.command(name='email')
@click.option('--limit', default=5, type=int, help='Max cold emails to send')
def email_command(limit: int) -> None:
	"""Execute Phase 4: Send cold emails to contacts with known emails."""
	console.print(f'[bold cyan]Dispatching cold emails (Limit: {limit})...[/bold cyan]')
	orchestrator = JobAgentOrchestrator()
	count = asyncio.run(orchestrator.run_email_only(limit=limit))
	console.print(f'[bold green]Dispatched {count} cold emails.[/bold green]')


@cli.command(name='stats')
def stats_command() -> None:
	"""Display real-time statistics dashboard of applications and interviews."""
	tracker = JobTracker()
	stats = tracker.get_stats()

	table = Table(title='[Job Hunt Campaign Metrics]', border_style='cyan')
	table.add_column('Metric', style='bold yellow')
	table.add_column('Value', style='bold green', justify='right')

	table.add_row('Total Jobs Found', str(stats['total_found']))
	table.add_row('Applications Submitted', str(stats['total_applied']))
	table.add_row('Interviews Scheduled', str(stats['total_interviews']))
	table.add_row('Recruiter Emails Found', str(stats['total_hr_emails']))
	table.add_row('Cold Emails Sent', str(stats['total_emails_sent']))

	console.print(table)

	if stats.get('platform_breakdown'):
		plat_table = Table(title='Platform Breakdown', border_style='blue')
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
 

@cli.command(name='jobs')
@click.option('--limit', default=20, type=int, help='Maximum jobs to display')
@click.option('--platform', default=None, help='Filter by platform: linkedin, wellfound, naukri')
def jobs_command(limit: int, platform: str | None) -> None:
	"""Display a detailed table of saved jobs from the database."""
	tracker = JobTracker()
	jobs = tracker.get_all_jobs(platform=platform, limit=limit)

	if not jobs:
		console.print('[yellow]No jobs found in database.[/yellow]')
		return

	table = Table(title=f'[Cataloged Jobs in Database (Showing {len(jobs)})]', border_style='cyan')
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


@cli.command(name='interactive')
@click.option(
	'--url',
	default='https://wellfound.com/jobs',
	help='Starting URL for interactive browser session',
)
@click.option('--task', default=None, help='Initial task to run in the interactive browser')
def interactive_command(url: str, task: str | None) -> None:
	"""Launch a visible Chrome window with live in-browser demo panel and continuous REPL."""
	import os

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
	from job_agent.orchestrator import get_default_llm

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
