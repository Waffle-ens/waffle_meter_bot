from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import discord
from discord import app_commands


DEFAULT_REPOSITORY = "Waffle-ens/waffle_meter"
DEFAULT_CHECK_INTERVAL_SECONDS = 600
DEFAULT_STATE_FILE = "/data/state.json"
DEFAULT_DB_FILE = "/data/bot.db"
GITHUB_API_BASE = "https://api.github.com"
NPCAP_DOWNLOAD_PAGE = "https://npcap.com/#download"
USER_AGENT = "waffle-meter-discord-bot/1.0"
EMBED_COLOR = 0x4F8CC9


@dataclass(frozen=True)
class Config:
    repository: str
    bot_token: str
    state_file: Path
    db_file: Path
    check_interval_seconds: int
    include_prereleases: bool
    send_initial_notification: bool
    github_token: str
    sync_commands: bool
    test_guild_id: int | None


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


class StateStore:
    def __init__(self, state_file: Path, db_file: Path) -> None:
        self.state_file = state_file
        self.db_file = db_file

    def initialize(self) -> None:
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_file) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_channels (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def read_current_version(self) -> str | None:
        if not self.state_file.exists():
            return None

        with self.state_file.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)

        version = data.get("version")
        if not isinstance(version, str) or not version:
            return None
        return version

    def write_current_version(self, version: str) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(f"{self.state_file.suffix}.tmp")

        with temp_file.open("w", encoding="utf-8") as file:
            json.dump({"version": version}, file, ensure_ascii=False, separators=(",", ":"))
            file.write("\n")

        temp_file.replace(self.state_file)

    def upsert_notification_channel(self, guild_id: int, channel_id: int) -> None:
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_file) as connection:
            connection.execute(
                """
                INSERT INTO notification_channels (guild_id, channel_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )

    def list_notification_channels(self) -> list[tuple[int, int]]:
        with sqlite3.connect(self.db_file) as connection:
            rows = connection.execute(
                "SELECT guild_id, channel_id FROM notification_channels ORDER BY guild_id"
            ).fetchall()
        return [(int(guild_id), int(channel_id)) for guild_id, channel_id in rows]

    def delete_notification_channel(self, guild_id: int) -> None:
        with sqlite3.connect(self.db_file) as connection:
            connection.execute("DELETE FROM notification_channels WHERE guild_id = ?", (guild_id,))


def log(message: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{now}] {message}", flush=True)


def read_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def read_optional_int_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value)


def load_config() -> Config:
    repository = os.getenv("GITHUB_REPOSITORY", DEFAULT_REPOSITORY).strip()
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    state_file = Path(os.getenv("STATE_FILE", DEFAULT_STATE_FILE)).expanduser()
    db_file = Path(os.getenv("DB_FILE", DEFAULT_DB_FILE)).expanduser()
    check_interval_seconds = int(
        os.getenv("CHECK_INTERVAL_SECONDS", str(DEFAULT_CHECK_INTERVAL_SECONDS))
    )

    if check_interval_seconds < 60:
        raise ValueError("CHECK_INTERVAL_SECONDS must be at least 60.")
    if "/" not in repository:
        raise ValueError("GITHUB_REPOSITORY must look like OWNER/REPO.")
    if not bot_token:
        raise ValueError("DISCORD_BOT_TOKEN is required.")

    return Config(
        repository=repository,
        bot_token=bot_token,
        state_file=state_file,
        db_file=db_file,
        check_interval_seconds=check_interval_seconds,
        include_prereleases=read_bool_env("INCLUDE_PRERELEASES"),
        send_initial_notification=read_bool_env("SEND_INITIAL_NOTIFICATION"),
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        sync_commands=read_bool_env("SYNC_COMMANDS", True),
        test_guild_id=read_optional_int_env("DISCORD_TEST_GUILD_ID"),
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


def parse_github_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def make_embed(title: str, release: ReleaseInfo, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        url=release.html_url,
        description=description,
        color=EMBED_COLOR,
    )
    timestamp = parse_github_timestamp(release.published_at)
    if timestamp:
        embed.timestamp = timestamp
    return embed


def build_update_embed(
    previous_tag: str | None,
    latest: ReleaseInfo,
    update_count: int | None,
    npcap: NpcapInfo,
) -> discord.Embed:
    description = "\n".join(
        [
            format_update_line(previous_tag, latest.tag_name, update_count),
            f"Waffle Meter: {latest.download_url}",
            f"Npcap: {npcap.installer_url}",
        ]
    )
    return make_embed("Waffle Meter 업데이트 알림", latest, description)


def build_latest_embed(latest: ReleaseInfo, npcap: NpcapInfo) -> discord.Embed:
    description = "\n".join(
        [
            f"최신 버전: {normalize_version(latest.tag_name)}",
            f"Waffle Meter: {latest.download_url}",
            f"Npcap: {npcap.installer_url}",
        ]
    )
    return make_embed("Waffle Meter 최신 버전", latest, description)


def build_install_embed(latest: ReleaseInfo, npcap: NpcapInfo) -> discord.Embed:
    description = "\n".join(
        [
            f"Npcap: {npcap.installer_url}",
            f"Waffle Meter: {latest.download_url}",
            "",
            "1. 링크 된 Npcap 을 우선 설치해주세요. 설치 중 "
            "`Install Npcap in WinPcap API-compatible Mode` 옵션을 반드시 체크했는지 "
            "확인해주세요. (A2power 사용 하던 사용자는 재설치 할 필요 없습니다.)",
            "2. 제공된 Waffle Meter 링크로 msi 를 다운 받아 설치해주세요.",
            "3. 설치완료 시 바탕화면에 바로가기 아이콘이 생성됩니다.",
        ]
    )
    return make_embed("Waffle Meter 설치방법", latest, description)


class WaffleMeterBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.store = StateStore(config.state_file, config.db_file)
        self.tree = app_commands.CommandTree(self)
        self.update_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        await asyncio.to_thread(self.store.initialize)
        self.register_commands()

        if self.config.sync_commands:
            if self.config.test_guild_id:
                guild = discord.Object(id=self.config.test_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log(f"Synced {len(synced)} slash commands to test guild {guild.id}.")
            else:
                synced = await self.tree.sync()
                log(f"Synced {len(synced)} global slash commands.")

        self.update_task = asyncio.create_task(self.update_loop())

    async def on_ready(self) -> None:
        if self.user:
            log(f"Logged in as {self.user} ({self.user.id}).")

    async def close(self) -> None:
        if self.update_task:
            self.update_task.cancel()
        await super().close()

    def register_commands(self) -> None:
        @self.tree.command(name="최신버전", description="현재 Waffle Meter 최신 버전을 확인합니다.")
        async def latest_version(interaction: discord.Interaction) -> None:
            await interaction.response.defer(thinking=True)
            await self.remember_notification_channel(interaction)

            try:
                latest, npcap = await self.fetch_latest_with_npcap()
            except Exception as exc:
                log(f"/최신버전 failed: {exc}")
                await interaction.followup.send("최신 버전 정보를 가져오지 못했습니다.")
                return

            await interaction.followup.send(embed=build_latest_embed(latest, npcap))

        @self.tree.command(name="설치방법", description="Waffle Meter 설치 링크와 설치 순서를 안내합니다.")
        async def install_guide(interaction: discord.Interaction) -> None:
            await interaction.response.defer(thinking=True)
            await self.remember_notification_channel(interaction)

            try:
                latest, npcap = await self.fetch_latest_with_npcap()
            except Exception as exc:
                log(f"/설치방법 failed: {exc}")
                await interaction.followup.send("설치 정보를 가져오지 못했습니다.")
                return

            await interaction.followup.send(embed=build_install_embed(latest, npcap))

    async def remember_notification_channel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            return
        await asyncio.to_thread(
            self.store.upsert_notification_channel,
            interaction.guild_id,
            interaction.channel_id,
        )
        log(
            "Registered notification channel "
            f"{interaction.channel_id} for guild {interaction.guild_id}."
        )

    async def fetch_latest_with_npcap(self) -> tuple[ReleaseInfo, NpcapInfo]:
        releases, npcap = await asyncio.gather(
            asyncio.to_thread(fetch_releases, self.config),
            asyncio.to_thread(fetch_npcap_info),
        )
        if not releases:
            raise RuntimeError(f"No releases found for {self.config.repository}.")
        return releases[0], npcap

    async def update_loop(self) -> None:
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                await self.check_for_update()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Update check failed: {exc}")

            await asyncio.sleep(self.config.check_interval_seconds)

    async def check_for_update(self) -> None:
        releases = await asyncio.to_thread(fetch_releases, self.config)
        if not releases:
            raise RuntimeError(f"No releases found for {self.config.repository}.")

        latest = releases[0]
        current_version = await asyncio.to_thread(self.store.read_current_version)

        if current_version == latest.tag_name:
            log(f"No update. Current version is {normalize_version(latest.tag_name)}.")
            return

        if current_version is None and not self.config.send_initial_notification:
            await asyncio.to_thread(self.store.write_current_version, latest.tag_name)
            log(
                "Initialized state without sending a notification. "
                f"Current version is {normalize_version(latest.tag_name)}."
            )
            return

        channels = await asyncio.to_thread(self.store.list_notification_channels)
        if not channels:
            await asyncio.to_thread(self.store.write_current_version, latest.tag_name)
            log("Update detected, but no notification channels are registered.")
            return

        update_count = (
            None if current_version is None else count_new_releases(releases, current_version)
        )
        npcap = await asyncio.to_thread(fetch_npcap_info)
        embed = build_update_embed(current_version, latest, update_count, npcap)
        sent_count = await self.broadcast_update(embed, channels)

        await asyncio.to_thread(self.store.write_current_version, latest.tag_name)
        log(
            "Processed update "
            f"{normalize_version(current_version) if current_version else 'initial'} "
            f"-> {normalize_version(latest.tag_name)} for {sent_count} channel(s)."
        )

    async def broadcast_update(
        self,
        embed: discord.Embed,
        channels: list[tuple[int, int]],
    ) -> int:
        sent_count = 0

        for guild_id, channel_id in channels:
            try:
                channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
                if not hasattr(channel, "send"):
                    raise RuntimeError(f"Channel {channel_id} is not messageable.")
                await channel.send(embed=embed)
                sent_count += 1
            except (discord.Forbidden, discord.NotFound):
                await asyncio.to_thread(self.store.delete_notification_channel, guild_id)
                log(f"Removed unavailable notification channel {channel_id} for guild {guild_id}.")
            except Exception as exc:
                log(f"Failed to send update to channel {channel_id} in guild {guild_id}: {exc}")

        return sent_count


async def run_bot() -> None:
    try:
        config = load_config()
    except Exception as exc:
        log(f"Configuration error: {exc}")
        raise SystemExit(2) from exc

    bot = WaffleMeterBot(config)
    await bot.start(config.bot_token)


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
