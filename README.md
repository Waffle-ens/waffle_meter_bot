# Waffle Meter Bot

`Waffle-ens/waffle_meter`의 GitHub Releases를 확인하고, 새 버전이 올라오면 Discord 서버에 업데이트 알림을 보내는 상시 실행 봇입니다.

## 기능

- 10분마다 `Waffle-ens/waffle_meter` GitHub Releases 확인
- 새 릴리즈 감지 시 등록된 서버 채널에 업데이트 임베드 전송
- `/최신버전` 명령어로 현재 최신 Waffle Meter 버전, 설치 파일, 릴리즈 노트 안내
- `/설치방법` 명령어로 v2.0 기준 설치 파일, SmartScreen, UAC, 자동 업데이트 안내
- 업데이트 알림에 GitHub Release body 기반 패치노트 요약 표시
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
      -> /최신버전: 최신 버전, 설치 파일, 릴리즈 노트, 최근 변경사항 임베드 응답
      -> /설치방법: v2.0 설치 순서와 자동 업데이트 안내 임베드 응답
      -> 명령어가 실행된 채널을 서버별 알림 채널로 저장

  -> Update loop
      -> 10분마다 GitHub Releases API 확인
      -> /data/state.json의 현재 버전과 비교
      -> 업데이트가 있으면 일반 사용자용 설치 asset 선택
      -> GitHub Release body에서 패치노트 요약 생성
      -> /data/bot.db에 저장된 서버별 채널로 업데이트 임베드 전송
      -> /data/state.json에 최신 버전 저장
```

다운로드 asset은 `waffle_meter-win-Setup.exe`를 최우선으로 선택합니다. 없으면 `waffle_meter-win-Portable.zip`, 일반 `.exe`, 일반 `.zip` 순서로 fallback하며, `RELEASES`, `releases.win.json`, `.nupkg` 파일은 사용자용 다운로드 링크로 선택하지 않습니다.

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
업데이트: 2.0.8 -> 2.0.9 (+1 version)
설치 파일: https://github.com/Waffle-ens/waffle_meter/releases/download/2.0.9/waffle_meter-win-Setup.exe
릴리즈 노트: https://github.com/Waffle-ens/waffle_meter/releases/tag/2.0.9

앱 내 자동 업데이트를 사용할 수 있습니다.
이미 설치되어 있다면 미터기 업데이트 알림에서 "지금 재시작"을 눌러 적용하세요.

패치노트
- 끝난 전투/기록 재생에서 내 캐릭터 바 색상이 잘못 보이던 문제를 수정했습니다.
- 보스 체력 게이지도 게이지 형태 옵션을 따르도록 개선했습니다.
```

## 설치방법 메시지

```text
설치 파일: https://github.com/Waffle-ens/waffle_meter/releases/download/2.0.9/waffle_meter-win-Setup.exe
릴리즈 노트: https://github.com/Waffle-ens/waffle_meter/releases/tag/2.0.9

1. 위 설치 파일(waffle_meter-win-Setup.exe)을 다운로드해 실행합니다.
2. Windows SmartScreen 경고가 뜨면 "추가 정보" -> "실행"을 눌러 진행합니다.
3. 처음 실행할 때 패킷 캡처 권한 상승(UAC)이 한 번 표시됩니다.
4. 설치 후 시작 메뉴 또는 바탕화면의 waffle_meter 바로가기로 실행합니다.
5. 이후 업데이트는 앱에서 자동 확인하며, 알림에서 "지금 재시작"을 누르면 자동 적용됩니다.

참고: v1.x MSI 사용자는 v2.0 설치 시 기존 버전이 자동 정리됩니다.
```
