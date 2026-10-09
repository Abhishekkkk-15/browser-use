from __future__ import annotations

import urllib.parse

from job_agent.config import JobPreferences, UserProfile


def build_linkedin_search_prompt(user: UserProfile, prefs: JobPreferences) -> str:
	role = prefs.target_roles[0] if prefs.target_roles else 'Software Engineer'
	location = prefs.target_locations[0] if prefs.target_locations else 'Remote'
	max_postings = prefs.max_searches_per_platform

	encoded_role = urllib.parse.quote(role)
	encoded_loc = urllib.parse.quote(location)
	exp_param = prefs.get_linkedin_experience_param(user.years_of_experience)
	# Direct pre-filtered URL with f_AL=true (Easy Apply) and experience parameter
	direct_search_url = (
		f'https://www.linkedin.com/jobs/search/?keywords={encoded_role}&location={encoded_loc}&f_AL=true{exp_param}'
	)

	return f"""
You are an autonomous AI recruiter assistant searching LinkedIn for high-match jobs.

YOUR OBJECTIVE:
Catalog relevant postings matching candidate's role and experience level (up to {max_postings} positions) and any visible recruiter/HR contacts into the tracker database.

CANDIDATE EXPERIENCE:
- Candidate has {user.years_of_experience:.1f} years of professional experience.
- When candidate has <= 1 year of experience: DO NOT save or apply to jobs requiring 2+ years of experience, and NEVER save jobs with 'Senior', 'Lead', 'Staff', or 'Principal' in the title!

STEP-BY-STEP WORKFLOW:
1. Navigate directly to: {direct_search_url}
   (If that redirects or is blank, navigate to https://www.linkedin.com/jobs/ and enter '{role}' in search bar).
2. Dismiss any modal/dialog, login prompt, or overlay by clicking the close button ('X') or pressing Escape.
3. Browse through the job cards listed in the left-hand search results panel (examine up to {max_postings} postings):
   a. Click on each job card to load its details in the right-hand panel.
   b. If clicking opens a new tab, switch to the new tab, extract the information, call save_job, and close the tab.
   c. Extract:
      - Job title
      - Company name
      - Location
      - Salary range (if visible)
      - Summary of responsibilities and technical stack
      - Key required skills
   d. EXACT JOB URL EXTRACTION:
      - Look at the job title in the top header of the right-hand details pane: click or copy the link address (it points to https://www.linkedin.com/jobs/view/<jobId>/).
      - Alternatively, check the browser address bar: if it has currentJobId=123456789, use https://www.linkedin.com/jobs/view/123456789/ as the job_url.
      - NEVER use the generic search results URL (https://www.linkedin.com/jobs/search/...) as the job_url! Each posting must have a specific /jobs/view/<id>/ URL.
   e. APPLICATION MECHANISM:
      - Look at the application button in the right panel:
        * If button text says "Easy Apply" -> pass application_type="easy_apply".
        * If button text says "Apply" -> pass application_type="external".
   f. RECRUITER / HIRING TEAM:
      - Check the posting for recruiter details ("Meet the hiring team" or "Posted by").
      - If visible, call save_hr_contact(job_url=..., hr_name=..., ...).
   g. STRICT RELEVANCE & EXPERIENCE CHECK BEFORE SAVING:
      - Only save postings that genuinely match candidate's target role '{role}', location '{location}', and experience ({user.years_of_experience:.1f} yrs).
      - If the posting requires 2+ years or is Senior/Lead, SKIP IT without calling save_job!
      - If relevant, call save_job(platform="linkedin", ...) with all extracted details.
4. Move to the next job card in the list. Scroll the list if needed.
5. RESILIENCE & COMPLETION RULES:
   - Focus on QUALITY and RELEVANCE over quantity. Never save irrelevant or over-experienced jobs.
   - If any individual job fails to load, simply click the next job card.
   - Do NOT click 'Easy Apply' to submit during this search phase.
   - Skip blacklisted companies: {prefs.blacklisted_companies}.
   - When you have inspected the available listings and saved the matching relevant postings (up to {max_postings}), conclude with done.
"""


def build_wellfound_search_prompt(user: UserProfile, prefs: JobPreferences) -> str:
	role = prefs.target_roles[0] if prefs.target_roles else 'AI Engineer'
	location = prefs.target_locations[0] if prefs.target_locations else 'Remote'
	max_postings = prefs.max_searches_per_platform

	return f"""
You are an autonomous AI recruiter searching Wellfound (formerly AngelList Talent) for startup jobs.

YOUR OBJECTIVE:
Catalog relevant tech jobs (up to {max_postings} positions) matching candidate experience and any visible recruiter/founder contacts into the tracker database.

CANDIDATE EXPERIENCE:
- Candidate has {user.years_of_experience:.1f} years of professional experience.
- DO NOT save jobs requiring 2+ years of experience, and DO NOT save Senior/Lead/Staff roles if candidate has <= 1 year of experience!

STEP-BY-STEP WORKFLOW:

1. INITIAL NAVIGATION & OBSERVATION:
   - Navigate to https://wellfound.com/jobs
   - Wait for the page to fully load.
   - Dismiss any cookie banners, sign-in dialogs, or onboarding modals by clicking the close/dismiss icon.
   - DO NOT click any Help, Beacon, Support, Intercom, or chat widget at the bottom right.
   - Notice that the page already displays a feed of active startup jobs!

2. FILTER CONFIGURATION (ROLES & LOCATION):
   - You may configure the search filters:
     a. ROLE: Click the Role filter input, type '{role}', and click the matching autocomplete suggestion.
     b. LOCATION: Click the Location filter input, type '{location}', and select '{location}' from the dropdown.
   - CHECK FOR RESTRICTIVE FILTERS: Wellfound frequently has default filter pills already active. If you see restrictive filters, adjust them. If candidate has <= 1 year experience, ensure experience is set to 0-1 years (Junior / Entry-Level).

3. ZERO-RESULTS RECOVERY (CRITICAL):
   - If after applying filters the page shows "0 results", "No jobs match your criteria", or an empty list:
     a. DO NOT STOP! DO NOT CALL DONE! Calling done on 0 results is a critical failure.
     b. Click the 'x' to remove the restrictive filters you just added (e.g. remove experience pill, remove location pill).
     c. If still 0 results, remove the '{role}' filter pill as well, OR refresh / navigate back to https://wellfound.com/jobs.
     d. FALL BACK TO THE VISIBLE FEED: Wellfound's default feed has active startup listings. Browse this visible feed directly!

4. INSPECTING AND CATALOGING JOBS (STRICT RELEVANCE & EXPERIENCE):
   - Browse through the job cards in the feed (focus on {role}, AI Engineer, Machine Learning, Data Science, Software Engineer, Backend, Fullstack, or Tech roles).
   - For EACH job:
     a. Click the job title or company card to view the full job posting details.
     b. If clicking opens a new tab, switch to the new tab, extract the information, call save_job, and close the tab to return to the feed.
     c. Verify relevance to '{role}', '{location}', and EXPERIENCE ({user.years_of_experience:.1f} yrs):
        - If candidate has <= 1 year of experience and job requires 2+ years, or is 'Senior'/'Lead'/'Staff', DO NOT call save_job. Return to feed and inspect next job!
        - If the job is unrelated (e.g. Sales, Marketing, completely different stack, or on-site in a distant foreign country without Remote), DO NOT call save_job. Return to feed and inspect next job!
     d. If genuinely relevant, extract details:
        - Job title (e.g. 'AI Engineer - LLMs & Generative AI')
        - Company name (e.g. 'CODEMIND AI')
        - Exact job URL (copy from address bar or link)
        - Location and remote status (e.g. 'Remote')
        - Compensation (salary range and equity percentage)
        - Summary of job responsibilities & duties
        - Required skills and tech stack (e.g. Python, PyTorch, LangChain, FastAPI)
     e. Call save_job(platform="wellfound", ...) with all extracted fields. The system will reject non-relevant jobs.
     f. Check the page for the hiring team: Wellfound often shows an "Active hiring team" or "Meet the team" section with founders, engineering leads, or recruiters. If visible, call save_hr_contact(job_url=..., hr_name=..., ...).
     g. If inspected in same page, use browser back (go_back) or click "Jobs" to return to the feed.
     h. Proceed to the next job card and repeat.

5. RESILIENCE & COMPLETION RULES:
   - Focus on RELEVANCE over volume. Never save irrelevant postings or jobs requiring too much experience.
   - If a filter is tricky or stubborn, scroll the default feed to inspect relevant startup listings.
   - If any individual job fails to load, immediately return to the feed and click the next listing.
   - Do NOT click 'Apply' to submit applications during this discovery phase.
   - Skip blacklisted companies: {prefs.blacklisted_companies}.
   - Conclude with done when you have inspected the available listings and saved the matching relevant jobs (up to {max_postings} postings).
"""


def build_naukri_search_prompt(user: UserProfile, prefs: JobPreferences) -> str:
	role = prefs.target_roles[0] if prefs.target_roles else 'Software Engineer'
	location = prefs.target_locations[0] if prefs.target_locations else 'Bangalore'
	max_postings = prefs.max_searches_per_platform

	encoded_role = urllib.parse.quote(role)
	direct_url = (
		f'https://www.naukri.com/{role.lower().replace(" ", "-")}-jobs-in-{location.lower().replace(" ", "-")}?k={encoded_role}'
	)

	return f"""
You are an autonomous AI recruiter searching Naukri.com for matching software engineering jobs.

YOUR OBJECTIVE:
Catalog relevant software engineering jobs (up to {max_postings} positions) into the tracker database.

1. Navigate directly to: {direct_url}
   (If that does not load, go to https://www.naukri.com/ and search for '{role}' in '{location}').
2. Dismiss any promo popups, cookie banners, or notifications by clicking 'x' or 'Later'.
3. Browse the job search result cards (examine up to {max_postings} postings):
   a. Click on each job card to view its description.
   b. If opening in a new tab, switch to it, extract information, call save_job, and close the tab.
   c. Extract:
      - Job title
      - Company name
      - Exact job URL
      - Experience required
      - Location
      - Salary (if disclosed)
      - Key skills tags
      - Recruiter name / contact details if shown at the bottom of the card
   d. Call save_job(platform="naukri", ...)
   e. If recruiter info is shown, call save_hr_contact()
   f. Return or switch back to the search results to inspect the next job.
4. RESILIENCE RULES:
   - Prioritize RELEVANCE. Do not save unrelated postings.
   - Do not click Apply during this search phase.
   - Skip blacklisted companies: {prefs.blacklisted_companies}.
5. Conclude with done once the available relevant jobs (up to {max_postings}) are cataloged.
"""
