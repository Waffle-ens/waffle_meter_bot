# Waffle Meter Bot

`Waffle-ens/waffle_meter`의 GitHub Releases를 주기적으로 확인하고, 새 버전이 올라오면 Discord 채널에 업데이트 알림을 보내는 경량 알림 봇입니다.

## 기능

- 10분마다 `Waffle-ens/waffle_meter` GitHub Releases 확인
- 새 릴리즈 감지 시 Discord Webhook 임베드 전송
- Waffle Meter 다운로드 링크와 최신 Npcap installer 링크 안내
- 상태 파일에는 마지막 알림 버전 하나만 저장
- 첫 실행 시 기본값은 현재 최신 버전만 저장하고 알림은 전송하지 않음

## 사용 기술

- Python 3.13
- Python 표준 라이브러리만 사용
- Discord Webhook
- GitHub Releases API
- Docker Compose

## 동작 아키텍처

```text
Docker container
  -> main.py
      -> GitHub Releases API 확인
      -> /data/state.json의 현재 버전과 비교
      -> Npcap 다운로드 페이지에서 최신 installer 링크 추출
      -> Discord Webhook 임베드 전송
      -> /data/state.json에 최신 버전 저장
      -> 10분 대기 후 반복
```

상태 파일은 현재 알림 완료 버전 하나만 저장합니다.

```json
{"version":"v1.3.1"}
```

## 실행

```bash
cp .env.example .env
vi .env
docker compose up -d --build
```

필수 설정은 `.env`의 `DISCORD_WEBHOOK_URL`입니다.

## 메시지 예시

```text
업데이트: 1.2.2 -> 1.3.1 (+2 versions)
Waffle Meter: https://github.com/Waffle-ens/waffle_meter/releases/download/v1.3.1/waffle_meter.v1.3-1.3.1.msi
Npcap: https://npcap.com/dist/npcap-1.88.exe
```
