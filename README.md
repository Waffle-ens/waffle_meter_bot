# Waffle Meter Bot

`Waffle-ens/waffle_meter`의 GitHub Releases를 확인하고, 새 버전이 올라오면 Discord 서버에 업데이트 알림을 보내는 상시 실행 봇입니다.

## 기능

- 10분마다 `Waffle-ens/waffle_meter` GitHub Releases 확인
- 새 릴리즈 감지 시 등록된 서버 채널에 업데이트 임베드 전송
- `/최신버전` 명령어로 현재 최신 Waffle Meter 버전 확인
- `/설치방법` 명령어로 Waffle Meter / Npcap 링크와 설치 순서 안내
- 서버에서 두 명령어 중 하나를 사용하면 해당 채널을 업데이트 알림 채널로 자동 저장
- 상태 파일에는 마지막 알림 버전 하나만 저장

## 명령어

```text
/최신버전
/설치방법
```

## 사용 기술

- Python 3.12
- discord.py
- SQLite
- GitHub Releases API
- Docker Compose

## 동작 아키텍처

```text
Discord Bot
  -> Slash commands
      -> /최신버전: 최신 버전 임베드 응답
      -> /설치방법: 설치 링크와 설치 순서 임베드 응답
      -> 명령어가 실행된 채널을 서버별 알림 채널로 저장

  -> Update loop
      -> 10분마다 GitHub Releases API 확인
      -> /data/state.json의 현재 버전과 비교
      -> 업데이트가 있으면 Npcap 다운로드 페이지에서 최신 installer 링크 추출
      -> /data/bot.db에 저장된 서버별 채널로 업데이트 임베드 전송
      -> /data/state.json에 최신 버전 저장
```

## 실행

```bash
cp .env.example .env
vi .env
docker compose up -d --build
```

필수 설정은 `.env`의 `DISCORD_BOT_TOKEN`입니다.

처음 전역 슬래시 명령어를 등록하면 Discord에 표시되기까지 시간이 걸릴 수 있습니다. 테스트 서버에 즉시 동기화하려면 `.env`에 `DISCORD_TEST_GUILD_ID`를 넣어 실행할 수 있습니다.

## 업데이트 알림 예시

```text
업데이트: 1.2.2 -> 1.3.1 (+2 versions)
Waffle Meter: https://github.com/Waffle-ens/waffle_meter/releases/download/v1.3.1/waffle_meter.v1.3-1.3.1.msi
Npcap: https://npcap.com/dist/npcap-1.88.exe
```

## 설치방법 메시지

```text
Npcap: https://npcap.com/dist/npcap-1.88.exe
Waffle Meter: https://github.com/Waffle-ens/waffle_meter/releases/download/v1.3.1/waffle_meter.v1.3-1.3.1.msi

1. 링크 된 Npcap 을 우선 설치해주세요. 설치 중 `Install Npcap in WinPcap API-compatible Mode` 옵션을 반드시 체크했는지 확인해주세요. (A2power 사용 하던 사용자는 재설치 할 필요 없습니다.)
2. 제공된 Waffle Meter 링크로 msi 를 다운 받아 설치해주세요.
3. 설치완료 시 바탕화면에 바로가기 아이콘이 생성됩니다.
```
