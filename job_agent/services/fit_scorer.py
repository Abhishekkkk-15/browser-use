from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from job_agent.config import UserProfile


class JobFitResult(BaseModel):
	"""Detailed match scoring breakdown between candidate profile and a job posting."""

	score: float = Field(ge=0.0, le=100.0, description='Overall fit score percentage (0-100)')
	matched_skills: list[str] = Field(default_factory=list, description='Skills required that the candidate possesses')
	missing_skills: list[str] = Field(default_factory=list, description='Skills required that the candidate lacks')
	title_score: float = Field(default=0.0, ge=0.0, le=100.0, description='Job title relevance score (0-100)')
	experience_score: float = Field(default=0.0, ge=0.0, le=100.0, description='Experience level alignment score (0-100)')
	reasoning: str = Field(description='Human-readable explanation of why this job is or is not a match')


class JobFitScorer:
	"""Evaluates job postings against candidate profile to calculate best-fit priority."""

	SYNONYMS: dict[str, set[str]] = {
		'react': {'react', 'react.js', 'reactjs'},
		'next': {'next', 'next.js', 'nextjs'},
		'node': {'node', 'node.js', 'nodejs'},
		'postgres': {'postgres', 'postgresql', 'psql', 'pgvector'},
		'python': {'python', 'python3', 'py'},
		'fastapi': {'fastapi', 'fast-api'},
		'express': {'express', 'express.js', 'expressjs'},
		'ai': {'ai', 'artificial intelligence', 'genai', 'generative ai', 'llm', 'llms', 'agents'},
		'rag': {'rag', 'retrieval-augmented generation', 'vector database', 'embeddings'},
		'docker': {'docker', 'containerization', 'containers'},
		'k8s': {'k8s', 'kubernetes'},
		'mongo': {'mongo', 'mongodb'},
		'ts': {'ts', 'typescript'},
		'js': {'js', 'javascript'},
	}

	@classmethod
	def normalize_skill(cls, skill: str) -> str:
		s = skill.strip().lower()
		for canonical, synonyms in cls.SYNONYMS.items():
			if s in synonyms:
				return canonical
		return s

	@classmethod
	def score_fit(
		cls,
		job: dict[str, Any],
		user: UserProfile,
		target_roles: list[str] | None = None,
		target_locations: list[str] | None = None,
	) -> JobFitResult:
		"""Compute a multi-dimensional best-fit score between a job posting and candidate."""
		# Extract job fields
		title = str(job.get('job_title', '')).lower()
		description = str(job.get('job_description_summary', '')).lower()
		location = str(job.get('location', '')).lower()
		raw_skills = job.get('required_skills', [])
		if isinstance(raw_skills, str):
			skills_list = [s.strip() for s in raw_skills.split(',') if s.strip()]
		else:
			skills_list = list(raw_skills)

		# 1. Skill Matching
		user_norm_skills = {cls.normalize_skill(s) for s in user.skills}
		user_text_lower = f'{user.current_role} {user.summary} {" ".join(user.skills)}'.lower()

		matched: list[str] = []
		missing: list[str] = []

		if skills_list:
			for sk in skills_list:
				norm = cls.normalize_skill(sk)
				if norm in user_norm_skills or sk.lower() in user_text_lower:
					matched.append(sk)
				else:
					missing.append(sk)
			skill_ratio = len(matched) / max(len(skills_list), 1)
			skill_score = skill_ratio * 100.0
		else:
			# Infer skills from description if required_skills was empty
			found_in_desc = [s for s in user.skills if s.lower() in description]
			if found_in_desc:
				matched = found_in_desc
				skill_score = min(len(found_in_desc) * 15.0, 90.0)
			else:
				# If neither skills nor description matches user skills
				skill_score = 15.0

		# 2. Title & Role Matching
		title_score = 10.0  # baseline
		candidate_roles = [user.current_role.lower()]
		if target_roles:
			candidate_roles.extend([r.lower() for r in target_roles])

		for role in candidate_roles:
			role_words = [w for w in re.split(r'[\s/,-]+', role) if len(w) > 2]
			matches = sum(1 for w in role_words if w in title)
			if matches:
				calculated = 35.0 + (matches / len(role_words)) * 65.0
				if calculated > title_score:
					title_score = min(calculated, 100.0)

		# Bonus for key matching domain terms
		for keyword in ('ai', 'engineer', 'developer', 'software', 'full stack', 'backend', 'agent', 'rag'):
			if keyword in title and (keyword in user_text_lower or any(keyword in r for r in candidate_roles)):
				title_score = min(title_score + 10.0, 100.0)

		# 3. Experience Alignment & Strict Seniority Gates
		user_exp = float(user.years_of_experience)

		# Seniority Mismatch Gate
		seniority_regex = r'\b(senior|sr\.?|lead|staff|principal|head\s+of|director|manager|vp)\b'
		is_senior_role = bool(re.search(seniority_regex, title, re.IGNORECASE))
		if is_senior_role and user_exp < 2.5:
			return JobFitResult(
				score=0.0,
				matched_skills=matched,
				missing_skills=missing,
				reasoning=f"Seniority mismatch: '{job.get('job_title')}' requires Senior experience (Candidate has {user_exp:.1f} yr exp)",
			)

		# Check explicit years requirement in description or title
		full_text_for_exp = f'{description} {job.get("experience_required", "")} {title}'.lower()
		exp_match = re.search(r'(\d+(?:\.\d+)?)\+?\s*(?:to\s*(\d+(?:\.\d+)?))?\s*(?:years|yrs)', full_text_for_exp)
		min_req = None
		if exp_match:
			try:
				min_req = float(exp_match.group(1))
			except ValueError:
				pass

		exp_penalty = 0.0
		exp_note = ''
		if min_req is not None:
			if user_exp >= min_req:
				exp_score = 100.0
			elif user_exp >= min_req - 0.5:
				exp_score = 75.0
			else:
				gap = min_req - user_exp
				# HARD GATE: If candidate has <= 1 yr experience and job requires 2+ yrs
				if user_exp <= 1.0 and min_req >= 2.0:
					return JobFitResult(
						score=0.0,
						matched_skills=matched,
						missing_skills=missing,
						reasoning=f'Experience gap: Requires {min_req:.0f}+ yrs exp (Candidate has {user_exp:.1f} yr exp)',
					)
				# 2+ years gap for any experience level
				if gap >= 2.0:
					return JobFitResult(
						score=0.0,
						matched_skills=matched,
						missing_skills=missing,
						reasoning=f'Experience gap: Requires {min_req:.0f}+ yrs exp (Candidate has {user_exp:.1f} yr exp)',
					)

				exp_penalty = gap * 25.0
				exp_note = f'Requires {min_req:.0f}+ yrs exp'
				exp_score = max(20.0, 100.0 - (gap * 35.0))
		else:
			# Check junior/entry keywords
			if re.search(r'\b(junior|jr\.?|entry|intern|associate|graduate|fresh)\b', title, re.IGNORECASE):
				exp_score = 100.0 if user_exp <= 2.0 else 80.0
			elif user_exp < 1.0:
				exp_score = 50.0
			else:
				exp_score = 75.0

		# 4. Location & Remote Alignment
		loc_penalty = 0.0
		loc_note = ''
		if target_locations:
			is_remote_wanted = any('remote' in loc.lower() for loc in target_locations)
			job_is_remote = any(k in location for k in ('remote', 'everywhere', 'anywhere', 'work from home', 'telecommute'))
			matched_specific_loc = any(loc.lower() in location for loc in target_locations if 'remote' not in loc.lower())

			if is_remote_wanted and not job_is_remote and not matched_specific_loc:
				loc_penalty = 40.0
				loc_note = 'On-site in non-target location'

		# If 0 skills matched and title relevance is low, score should be zero
		if len(matched) == 0 and title_score < 40.0:
			final_score = 0.0
		else:
			# Weighted Overall Score: 45% Skills, 30% Title, 25% Experience
			base_score = (skill_score * 0.45) + (title_score * 0.30) + (exp_score * 0.25)
			final_score = max(0.0, min(100.0, base_score - loc_penalty - exp_penalty))

		final_score = round(final_score, 1)

		# Generate human-readable reasoning
		parts: list[str] = [f'{final_score:.0f}% Fit']
		if matched:
			parts.append(f'Matched {len(matched)} skills ({", ".join(matched[:4])})')
		if title_score >= 80:
			parts.append('High title relevance')
		elif title_score < 40:
			parts.append('Low title relevance')
		if loc_note:
			parts.append(loc_note)
		if exp_note:
			parts.append(exp_note)
		if missing and len(matched) > 0:
			parts.append(f'Missing: {", ".join(missing[:3])}')

		reasoning = '; '.join(parts)

		return JobFitResult(
			score=final_score,
			matched_skills=matched,
			missing_skills=missing,
			title_score=round(title_score, 1),
			experience_score=round(exp_score, 1),
			reasoning=reasoning,
		)
