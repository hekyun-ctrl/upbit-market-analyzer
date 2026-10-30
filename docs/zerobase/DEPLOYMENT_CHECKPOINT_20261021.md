# ZB 운영 배포 체크포인트 — 2026-10-21

사용자 승인: 운영 배포·알림 재개. 새 전략 수익성/ENGINEERING_READY는 미확인이라 ZB 운영 연결 supervisor와 전이 상태 알림만 준비. 새 진입 publication은 비용/상품/독립5m/펀딩/라이브 lifecycle 미완료로 false.

PR https://github.com/hekyun-ctrl/upbit-market-analyzer/pull/14 merged. 운영 feature/lbank-futures-monitor 소스 06b88b94a6b5ff0ab5fda0ed0c08a03bc5faa03b.
검증: 저장소 루트 python -m unittest discover -p 'test_lbank*.py' →397 PASS, ZB42시험 포함. 기존 진입/위험 규칙 미변경. 기존15분paper progress 기본false, 새transition-only supervisor/read-only native DB/영속 notice ACK. ACK는기기수신/체결이아님.

Railway project=e7d240ac-2468-45c9-8411-33f5e24f425b env=677e12e4-aff7-4880-bc11-ff30de315e68 service=8edd0d1a-3f5e-47fb-8e01-a0cc57935c34. staged없음, /data50MB확인.
배포 blocker: connect_service_source(repo,branch,commitSha=06b88...,staged=true,environmentId=677...)가 16 UNAUTHENTICATED: Request had invalid authentication credentials 응답. isError=true/error_code INVALID_ARGUMENT. 새배포 트리거되지않음. 재조회읽기는성공.
latest deployment 4bbda6c7-29e8-4811-8322-5f8d0574c1be SUCCESS/source25388b431c9c9d38df6a434670586c65f382d604 유지. 새ZB배포 성공이아님. 새런타임/APIack없음. 로컬Railway/ghCLI없음. 인증우회/새서비스생성안함.

재개: Railway재인증 후 describe_service로타인staged유무확인→동일env source06b88정확히stage→accept_deploy(사용자승인기존있음)→새deployment terminalSUCCESS/source확인→ZB_RUNTIME_STARTED/STATUS 실제native연결·block원인/ZB_NOTIFICATION APIack/기존NATIVE_ENTRY 위험loop확인. redeploy가새source를받는다고가정하지말것.
롤백 source25388b431c9c9d38df6a434670586c65f382d604 / deployment4bbda6c7-29e8-4811-8322-5f8d0574c1be. 예약연구중지유지. 배포·알림재개 완료나검증된실전알람으로표현하지않음.