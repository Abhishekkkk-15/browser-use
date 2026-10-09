from job_agent.platforms.base import PlatformAdapter
from job_agent.platforms.linkedin import LinkedInAdapter
from job_agent.platforms.naukri import NaukriAdapter
from job_agent.platforms.wellfound import WellfoundAdapter

_ADAPTERS: dict[str, PlatformAdapter] = {
	'linkedin': LinkedInAdapter(),
	'wellfound': WellfoundAdapter(),
	'naukri': NaukriAdapter(),
}


def get_platform_adapter(platform: str) -> PlatformAdapter:
	"""Retrieve platform adapter by name."""
	adapter = _ADAPTERS.get(platform.lower())
	if not adapter:
		# Fall back to LinkedIn as standard template
		return _ADAPTERS['linkedin']
	return adapter


__all__ = [
	'PlatformAdapter',
	'LinkedInAdapter',
	'WellfoundAdapter',
	'NaukriAdapter',
	'get_platform_adapter',
]
