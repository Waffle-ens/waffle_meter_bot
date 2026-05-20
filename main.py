from __future__ import annotations

import argparse
import html
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_REPOSITORY = "Waffle-ens/waffle_meter"
DEFAULT_CHECK_INTERVAL_SECONDS = 600
DEFAULT_STATE_FILE = "/data/state.json"
GITHUB_API_BASE = "https://api.github.com"
NPCAP_DOWNLOAD_PAGE = "https://npcap.com/#download"
USER_AGENT = "waffle-meter-discord-notifier/1.0"

stop_requested = False


@dataclass(frozen=True)
class Config:
    repository: str
    webhook_url: str
    state_file: Path
    check_interval_seconds: int
    include_prereleases: bool
    send_initial_notification: bool
    dry_run: bool
    github_token: str


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    name: str
    html_url: str
    download_url: str
    published_at: str
    prerelease: bool


@dataclass(frozen=True)
class NpcapInfo:
    version: str
    installer_url: str


class NpcapLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_href: str | None = None
        self.matches: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        self._current_href = attrs_dict.get("href")

    def handle_data(self, data: str) -> None:
        if not self._current_href:
            return
        text = " ".join(html.unescape(data).split())
        match = re.search(r"\bNpcap\s+([0-9]+(?:\.[0-9]+)*)\s+installer\b", text, re.I)
        if match:
            self.matches.append((match.group(1), self._current_href))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._current_href = None


def log(message: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{now}] {message}", flush=True)


def read_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config(args: argparse.Namespace) -> Config:
    repository = os.getenv("GITHUB_REPOSITORY", DEFAULT_REPOSITORY).strip()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    state_file = Path(os.getenv("STATE_FILE", DEFAULT_STATE_FILE)).expanduser()
    check_interval_seconds = int(
        os.getenv("CHECK_INTERVAL_SECONDS", str(DEFAULT_CHECK_INTERVAL_SECONDS))
    )
    dry_run = args.dry_run or read_bool_env("DRY_RUN")

    if check_interval_seconds < 60:
        raise ValueError("CHECK_INTERVAL_SECONDS must be at least 60.")
    if "/" not in repository:
        raise ValueError("GITHUB_REPOSITORY must look like OWNER/REPO.")
    if not webhook_url and not dry_run:
        raise ValueError("DISCORD_WEBHOOK_URL is required unless DRY_RUN=true.")

    return Config(
        repository=repository,
        webhook_url=webhook_url,
        state_file=state_file,
        check_interval_seconds=check_interval_seconds,
        include_prereleases=read_bool_env("INCLUDE_PRERELEASES"),
        send_initial_notification=read_bool_env("SEND_INITIAL_NOTIFICATION"),
        dry_run=dry_run,
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
    )


def request_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 20,
) -> bytes:
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    request = Request(url, headers=request_headers)

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to request {url}: {exc.reason}") from exc


def request_json(url: str, config: Config) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if config.github_token:
        headers["Authorization"] = f"Bearer {config.github_token}"

    return json.loads(request_bytes(url, headers=headers).decode("utf-8"))


def normalize_version(version: str) -> str:
    return version[1:] if version.lower().startswith("v") else version


def pick_download_url(release: dict[str, Any]) -> str:
    assets = release.get("assets") or []
    if not assets:
        return release["html_url"]

    preferred_extensions = (".msi", ".exe", ".zip")

    def score(asset: dict[str, Any]) -> tuple[int, str]:
        name = str(asset.get("name", "")).lower()
        for index, extension in enumerate(preferred_extensions):
            if name.endswith(extension):
                return (index, name)
        return (len(preferred_extensions), name)

    selected = sorted(assets, key=score)[0]
    return selected.get("browser_download_url") or release["html_url"]


def parse_release(release: dict[str, Any]) -> ReleaseInfo:
    return ReleaseInfo(
        tag_name=release["tag_name"],
        name=release.get("name") or release["tag_name"],
        html_url=release["html_url"],
        download_url=pick_download_url(release),
        published_at=release.get("published_at") or "",
        prerelease=bool(release.get("prerelease")),
    )


def fetch_releases(config: Config) -> list[ReleaseInfo]:
    url = f"{GITHUB_API_BASE}/repos/{config.repository}/releases?per_page=100"
    releases = request_json(url, config)
    parsed = [parse_release(release) for release in releases]
    if config.include_prereleases:
        return parsed
    return [release for release in parsed if not release.prerelease]


def fetch_npcap_info() -> NpcapInfo:
    page = request_bytes(NPCAP_DOWNLOAD_PAGE).decode("utf-8", errors="replace")
    parser = NpcapLinkParser()
    parser.feed(page)

    if not parser.matches:
        raise RuntimeError("Could not find the Npcap installer link on npcap.com.")

    version, href = parser.matches[0]
    return NpcapInfo(version=version, installer_url=urljoin(NPCAP_DOWNLOAD_PAGE, href))


def read_current_version(state_file: Path) -> str | None:
    if not state_file.exists():
        return None

    with state_file.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    version = data.get("version")
    if not isinstance(version, str) or not version:
        return None
    return version


def write_current_version(state_file: Path, version: str) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = state_file.with_suffix(f"{state_file.suffix}.tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump({"version": version}, file, ensure_ascii=False, separators=(",", ":"))
        file.write("\n")

    temp_file.replace(state_file)


def count_new_releases(releases: list[ReleaseInfo], previous_tag: str) -> int | None:
    for index, release in enumerate(releases):
        if release.tag_name == previous_tag:
            return index
    return None


def format_update_line(previous_tag: str | None, latest_tag: str, update_count: int | None) -> str:
    latest = normalize_version(latest_tag)
    if previous_tag is None:
        return f"업데이트: {latest} (initial)"

    previous = normalize_version(previous_tag)
    if update_count is None:
        suffix = "+1 or more versions"
    else:
        suffix = f"+{update_count} version" if update_count == 1 else f"+{update_count} versions"

    return f"업데이트: {previous} -> {latest} ({suffix})"


def build_discord_payload(
    *,
    previous_tag: str | None,
    latest: ReleaseInfo,
    update_count: int | None,
    npcap: NpcapInfo,
) -> dict[str, Any]:
    description = "\n".join(
        [
            format_update_line(previous_tag, latest.tag_name, update_count),
            f"Waffle Meter: {latest.download_url}",
            f"Npcap: {npcap.installer_url}",
        ]
    )

    embed: dict[str, Any] = {
        "title": "Waffle Meter 업데이트 알림",
        "url": latest.html_url,
        "description": description,
        "color": 0x4F8CC9,
    }
    if latest.published_at:
        embed["timestamp"] = latest.published_at

    return {"embeds": [embed]}


def send_discord_webhook(webhook_url: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError(f"Discord webhook returned HTTP {response.status}.")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord webhook returned HTTP {exc.code}: {body[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to send Discord webhook: {exc.reason}") from exc


def check_once(config: Config) -> None:
    releases = fetch_releases(config)
    if not releases:
        raise RuntimeError(f"No releases found for {config.repository}.")

    latest = releases[0]
    current_version = read_current_version(config.state_file)

    if current_version == latest.tag_name:
        log(f"No update. Current version is {normalize_version(latest.tag_name)}.")
        return

    if current_version is None and not config.send_initial_notification:
        write_current_version(config.state_file, latest.tag_name)
        log(
            "Initialized state without sending a notification. "
            f"Current version is {normalize_version(latest.tag_name)}."
        )
        return

    update_count = None if current_version is None else count_new_releases(releases, current_version)
    npcap = fetch_npcap_info()
    payload = build_discord_payload(
        previous_tag=current_version,
        latest=latest,
        update_count=update_count,
        npcap=npcap,
    )

    if config.dry_run:
        log("DRY_RUN=true. Discord payload follows:")
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    else:
        send_discord_webhook(config.webhook_url, payload)
        log(
            "Sent Discord notification for "
            f"{normalize_version(current_version) if current_version else 'initial'} "
            f"-> {normalize_version(latest.tag_name)}."
        )

    write_current_version(config.state_file, latest.tag_name)


def handle_shutdown(signum: int, _frame: Any) -> None:
    global stop_requested
    stop_requested = True
    log(f"Received signal {signum}; shutting down after this cycle.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notify Discord when waffle_meter is updated.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print the Discord payload instead of sending it.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = load_config(args)
    except Exception as exc:
        log(f"Configuration error: {exc}")
        return 2

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    had_error = False

    while not stop_requested:
        try:
            check_once(config)
        except Exception as exc:
            had_error = True
            log(f"Check failed: {exc}")

        if args.once or stop_requested:
            break

        log(f"Sleeping for {config.check_interval_seconds} seconds.")
        time.sleep(config.check_interval_seconds)

    return 1 if args.once and had_error else 0


if __name__ == "__main__":
    sys.exit(main())
