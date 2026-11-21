# Recovery checkpoint — 2026-10-23 05:16 KST

Freshly read mandatory retry/checkpoint/STOP files, unchanged blobs 6a5c3254387602e916046f180c287f1950c61dbf / 324986077642a711532136e86c6b93ea7bf41e1e / c02580c2d50fc8cf97d6eed267ea88680ec1ead5.

Official https://status.railway.com/ latest update 2026-10-22 19:42 UTC / 2026-10-23 04:42 KST: no change; Pro/Enterprise normal, Hobby builds still being enabled, Free/Trial follow; new signups paused. Existing target tier not established, no billing changes authorized. Major Outage still Identified. No new target build availability evidenced.

No deployment/restart/outbound/secrets read. Runtime not re-read this run; prior direct service observation at 2026-10-22 23:09 KST was live, no staged changes, latest old SUCCESS deployment 4bbda6c7-29e8-4811-8322-5f8d0574c1be. Config pin 06b88b94a6b5ff0ab5fda0ed0c08a03bc5faa03b is not new-build proof. STOP runtime_applied=false unchanged; no stopped or deployed claim. Rollback source 25388b431c9c9d38df6a434670586c65f382d604 / deployment above retained.

Next: establish applicable build recovery, review latest production and concurrent staged work, then one exact authorized-source build with LBANK_PAPER_NOTIFICATIONS_ENABLED=false and LBANK_ZB_RUNTIME_ENABLED=false. Preserve tests and reference/invalidation/risk paths. Verify runtime without paper/ZB-status test sends; old ACK requirement superseded. Recheck credential-safety checkpoint before broader deployment; Telegram rotation unconfirmed. Temporary outage: task retained, no old research resumed. No actual deployment/stop/performance transition; suppress duplicate user notification.
