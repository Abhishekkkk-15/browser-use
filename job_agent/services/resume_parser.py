from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MONTH_MAP = {
	'jan': 1,
	'feb': 2,
	'mar': 3,
	'apr': 4,
	'may': 5,
	'jun': 6,
	'jul': 7,
	'aug': 8,
	'sep': 9,
	'oct': 10,
	'nov': 11,
	'dec': 12,
}


def clean_resume_text(raw: str) -> str:
	"""Normalize Unicode, ligatures, and dashes for reliable parsing across PDF/TXT."""
	if not raw:
		return ''
	# Replace common ligatures
	ligatures = {
		'\ufb00': 'ff',
		'\ufb01': 'fi',
		'\ufb02': 'fl',
		'\ufb03': 'ffi',
		'\ufb04': 'ffl',
	}
	for lig, repl in ligatures.items():
		raw = raw.replace(lig, repl)

	raw = unicodedata.normalize('NFKD', raw)
	# Replace dashes and hyphens with standard hyphen
	raw = re.sub(r'[\u2010-\u2015\u2212]', '-', raw)
	# Replace non-breaking spaces
	raw = raw.replace('\u00a0', ' ')
	# Remove non-printable control characters except newline and tab
	raw = ''.join(ch for ch in raw if ch in ('\n', '\t') or ord(ch) >= 32)
	return raw


def parse_date_to_months(month_str: str | None, year_str: str) -> int:
	"""Convert month name/abbreviation and 4-digit year into total month counter."""
	try:
		y = int(year_str)
	except ValueError:
		y = 2026
	m = 1
	if month_str:
		m = MONTH_MAP.get(month_str[:3].lower(), 1)
	return y * 12 + (m - 1)


def calculate_experience_years(text: str, reference_date: datetime | None = None) -> float:
	"""Calculate total professional experience in years from date ranges in resume text.

	Accurately calculates junior and fractional experience (e.g. 0.5, 0.7, 1.0 years).
	"""
	if reference_date is None:
		reference_date = datetime.now()

	now_months = reference_date.year * 12 + (reference_date.month - 1)

	# 1. Check explicit mentions like '0.5 years of experience' or '2+ yrs experience'
	exp_mention = re.search(r'(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s*(?:of\s*)?experience', text, re.IGNORECASE)
	if exp_mention:
		try:
			val = float(exp_mention.group(1))
			if 0.1 <= val <= 35.0:
				return val
		except ValueError:
			pass

	ranges: list[tuple[int, int]] = []

	# Pattern 1: Month Year to Month Year / Present (e.g. 'Feb 2026 - Present')
	p1 = re.compile(
		r'(?i)(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})\s*(?:–|-|to)\s*(Present|Current|Now|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}|\d{4})'
	)
	for m in p1.finditer(text):
		start_m, start_y, end_part = m.groups()
		start_total = parse_date_to_months(start_m, start_y)
		if end_part.lower() in ('present', 'current', 'now'):
			end_total = now_months
		else:
			end_parts = end_part.split()
			if len(end_parts) == 2:
				end_total = parse_date_to_months(end_parts[0], end_parts[1])
			else:
				end_total = parse_date_to_months(None, end_parts[0])
		if end_total >= start_total:
			ranges.append((start_total, end_total))

	# Pattern 2: Year - Year / Present (e.g. 2025 - Present, 2024 - 2026)
	if not ranges:
		p2 = re.compile(r'(\b\d{4}\b)\s*(?:–|-|to)\s*(Present|Current|Now|\b\d{4}\b)', re.IGNORECASE)
		for m in p2.finditer(text):
			start_y, end_part = m.groups()
			start_total = parse_date_to_months(None, start_y)
			if end_part.lower() in ('present', 'current', 'now'):
				end_total = now_months
			else:
				end_total = parse_date_to_months(None, end_part)
			if end_total >= start_total:
				ranges.append((start_total, end_total))

	if not ranges:
		return 0.5  # Sensible junior baseline

	# Sort and merge overlapping intervals to avoid double counting
	ranges.sort()
	merged = [ranges[0]]
	for r in ranges[1:]:
		last_s, last_e = merged[-1]
		cur_s, cur_e = r
		if cur_s <= last_e:
			merged[-1] = (last_s, max(last_e, cur_e))
		else:
			merged.append(r)

	total_months = sum((e - s) for s, e in merged)
	years = round(total_months / 12.0, 1)
	return max(0.5, years)


class ResumeParser:
	"""Comprehensive resume parser supporting PDF and plain text with rule-based and LLM extraction."""

	@classmethod
	def extract_text(cls, file_path: Path | str) -> str:
		"""Extract and sanitize text from a PDF, Markdown, or text file."""
		path = Path(file_path)
		if not path.exists():
			raise FileNotFoundError(f'Resume file not found at {path}')

		suffix = path.suffix.lower()
		# If companion .txt exists and has content, prefer it for pristine markdown/text
		companion_txt = path.with_suffix('.txt')
		if suffix == '.pdf' and companion_txt.exists() and companion_txt.stat().st_size > 100:
			try:
				return clean_resume_text(companion_txt.read_text(encoding='utf-8'))
			except Exception:
				pass

		if suffix == '.pdf':
			try:
				import pypdf

				reader = pypdf.PdfReader(str(path))
				pages_text = []
				for page in reader.pages:
					try:
						p_text = page.extract_text(extraction_mode='layout')
					except Exception:
						p_text = page.extract_text()
					pages_text.append(p_text or '')
				raw = '\n'.join(pages_text)
				return clean_resume_text(raw)
			except Exception as e:
				logger.warning(f'Failed to extract text from PDF using pypdf: {e}')
				raise

		# Plain text / Markdown fallback
		try:
			raw = path.read_text(encoding='utf-8')
		except UnicodeDecodeError:
			raw = path.read_text(encoding='latin-1', errors='ignore')

		return clean_resume_text(raw)

	@classmethod
	def parse_rule_based(cls, text: str) -> dict[str, Any]:
		"""Extract candidate fields using robust regex and layout heuristics."""
		data: dict[str, Any] = {}

		# 1. Candidate Name
		name = None
		for line in text.splitlines():
			clean_line = line.strip().lstrip('#').strip()
			if (
				clean_line
				and len(clean_line.split()) in (2, 3, 4)
				and not any(
					k in clean_line.lower()
					for k in ['resume', 'curriculum', 'page', 'summary', 'contact', 'applied', 'engineer', 'developer']
				)
				and '@' not in clean_line
				and 'http' not in clean_line
			):
				name = clean_line
				break
		data['name'] = name or 'Candidate Name'

		# 2. Email
		email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)
		data['email'] = email_match.group(0) if email_match else 'candidate@example.com'

		# 3. Phone
		phone_match = re.search(r'(\+?\d[\d\s-]{8,15}\d)', text)
		data['phone'] = phone_match.group(0).strip() if phone_match else '+1234567890'

		# 4. URLs (LinkedIn, GitHub, Portfolio)
		linkedin_match = re.search(r'https?://(?:www\.)?linkedin\.com/in/[\w-]+', text)
		data['linkedin_url'] = linkedin_match.group(0) if linkedin_match else 'https://www.linkedin.com/in/candidate'

		github_match = re.search(r'https?://(?:www\.)?github\.com/[\w-]+', text)
		data['github_url'] = github_match.group(0) if github_match else None

		# Portfolio (any url not linkedin or github)
		portfolio_matches = re.findall(
			r'https?://(?!www\.linkedin|github|linkedin|mail\.google|google\.com)[a-zA-Z0-9.\-_\/]+', text
		)
		data['portfolio_url'] = portfolio_matches[0].rstrip('.,)') if portfolio_matches else None

		# 5. Location
		location = 'Remote'
		for line in text.splitlines()[:15]:
			if '|' in line:
				parts = [p.strip() for p in line.split('|')]
				for part in parts:
					if (
						any(
							k in part.lower()
							for k in [
								'india',
								'usa',
								'united states',
								'remote',
								'ca',
								'ny',
								'london',
								'bangalore',
								'rajasthan',
								'jodhpur',
								'delhi',
								'mumbai',
							]
						)
						and '@' not in part
						and 'http' not in part
						and len(part) < 60
					):
						location = part
						break
				if location != 'Remote':
					break
		data['location'] = location

		# 6. Current Role & Company
		role = 'Software Engineer'
		company = None
		# Look in EXPERIENCE section
		exp_sec_m = re.search(r'(?i)##\s*(?:PROFESSIONAL\s+)?EXPERIENCE\s*\n+###\s*([^\n—–\-]+)(?:[—–\-]\s*([^\n(]+))?', text)
		if exp_sec_m:
			role = exp_sec_m.group(1).strip()
			if exp_sec_m.group(2):
				company = exp_sec_m.group(2).strip()
		else:
			# Look for subtitle right below name
			lines = [line.strip() for line in text.splitlines() if line.strip()]
			if len(lines) >= 2 and '@' not in lines[1] and 'http' not in lines[1]:
				role_sub = lines[1].split('|')[0].strip()
				if len(role_sub) < 50:
					role = role_sub

		data['current_role'] = role
		data['current_company'] = company

		# 7. Total Experience in Years (calculated from dates)
		data['years_of_experience'] = calculate_experience_years(text)

		# 8. Skills
		skills: list[str] = []
		skills_sec_m = re.search(r'(?is)##\s*(?:TECHNICAL\s+)?SKILLS\s*\n(.*?)(?=\n##|\Z)', text)
		if skills_sec_m:
			raw_skills = skills_sec_m.group(1)
			for line in raw_skills.splitlines():
				line = re.sub(r'^[-\*•]\s*', '', line.strip())
				if ':' in line:
					line = line.split(':', 1)[1]
				for s in line.split(','):
					s_clean = s.strip()
					if s_clean and len(s_clean) < 40 and not s_clean.lower().startswith('languages'):
						skills.append(s_clean)
		else:
			# Fallback common tech extraction
			common_tech = [
				'Python',
				'FastAPI',
				'Django',
				'Flask',
				'TypeScript',
				'JavaScript',
				'React',
				'Next.js',
				'Node.js',
				'Express.js',
				'PostgreSQL',
				'MongoDB',
				'Docker',
				'Kubernetes',
				'AWS',
				'Redis',
				'GraphQL',
				'Tailwind CSS',
				'LangChain',
				'RAG',
				'AI Agents',
				'Git',
				'Linux',
			]
			for tech in common_tech:
				if re.search(rf'\b{re.escape(tech)}\b', text, re.IGNORECASE):
					skills.append(tech)

		data['skills'] = skills[:25] if skills else ['Python', 'FastAPI', 'TypeScript', 'React', 'Docker']

		# 9. Summary / Bio
		summary = None
		sum_m = re.search(r'(?is)##\s*(?:PROFESSIONAL\s+)?SUMMARY\s*\n+(.*?)(?=\n##|\Z)', text)
		if sum_m:
			summary = ' '.join(sum_m.group(1).split())
		data['summary'] = summary or f'{role} specializing in scalable web development, backend engineering, and AI automation.'

		# 10. Education
		education = None
		edu_m = re.search(r'(?is)##\s*EDUCATION\s*\n+(.*?)(?=\n##|\Z)', text)
		if edu_m:
			education = '; '.join([item.strip().lstrip('-*•').strip() for item in edu_m.group(1).splitlines() if item.strip()])
		data['education'] = education or 'Computer Science or equivalent degree'

		return data

	@classmethod
	async def parse_with_llm(cls, text: str, llm: Any) -> dict[str, Any]:
		"""Extract structured candidate profile from resume text using an LLM."""
		from browser_use.llm.messages import UserMessage

		prompt = f"""You are an expert HR data parsing system.
Extract the candidate profile from the resume below into a clean, strictly-formatted JSON object.

Fields required:
- name: string (Full Name)
- email: string
- phone: string
- location: string (City, Country, or Remote)
- linkedin_url: string
- github_url: string (or null)
- portfolio_url: string (or null)
- current_role: string (Current or most recent title)
- current_company: string (or null)
- years_of_experience: float (Calculate precisely from employment dates up to current year 2026. If candidate started early 2026, it is around 0.5 to 0.8 years)
- skills: list of strings (Key 10-20 technical skills)
- education: string
- summary: string (2-3 sentences professional summary)

Resume Content:
{text[:4000]}

Respond ONLY with a valid JSON object without markdown formatting or code blocks.
"""
		try:
			response = await llm.ainvoke([UserMessage(content=prompt)])
			content = response.completion.strip()
			if content.startswith('```'):
				content = re.sub(r'^```(?:json)?\s*', '', content)
				content = re.sub(r'\s*```$', '', content)
			data = json.loads(content)
			if 'years_of_experience' in data:
				data['years_of_experience'] = float(data['years_of_experience'])
			return data
		except Exception as e:
			logger.warning(f'LLM resume parsing failed, falling back to rule-based parser: {e}')
			return cls.parse_rule_based(text)

	@classmethod
	def parse(
		cls,
		file_path: Path | str,
		use_llm: bool = True,
		llm: Any | None = None,
	) -> dict[str, Any]:
		"""Parse resume file into a dictionary suitable for UserProfile.

		Tries LLM parsing if configured, otherwise uses deterministic rule-based parsing.
		"""
		path = Path(file_path)
		# If path is PDF and a corresponding .txt file exists in the same folder or data folder, prefer .txt for clean parsing
		txt_companion = path.with_suffix('.txt')
		if path.suffix.lower() == '.pdf' and txt_companion.exists():
			text = cls.extract_text(txt_companion)
		else:
			text = cls.extract_text(path)

		if use_llm:
			try:
				if llm is None:
					from job_agent.orchestrator import get_default_llm

					llm = get_default_llm()

				import asyncio

				parsed = asyncio.run(cls.parse_with_llm(text, llm))
				if parsed and parsed.get('name') and parsed.get('name') != 'Candidate Name':
					parsed['resume_path'] = path
					return parsed
			except Exception as ex:
				logger.debug(f'LLM parsing attempt skipped or failed: {ex}')

		parsed = cls.parse_rule_based(text)
		parsed['resume_path'] = path
		return parsed
