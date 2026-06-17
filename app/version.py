"""Build version + update-check configuration.

Releasing an update (see README "Releasing updates"): bump __version__, build
the exe, and publish a GitHub Release tagged "v<version>" with the zip attached.
The in-app banner compares __version__ to the latest GitHub release tag.
"""
__version__ = "1.0.0"

# Your repository, "owner/name" (e.g. "alice/MoneyPilot"). Until this is a real
# repo, the update check stays DORMANT — it makes no network calls at all.
GITHUB_REPO = "YOUR_GITHUB_USERNAME/MoneyPilot"

UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
