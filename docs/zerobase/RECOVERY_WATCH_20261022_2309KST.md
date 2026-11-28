# Recovery checkpoint — 2026-10-22 23:09 KST

Read current DEPLOYMENT_RETRY_20261021.md (blob 6a5c3254387602e916046f180c287f1950c61dbf), DEPLOYMENT_CHECKPOINT_20261021.md (324986077642a711532136e86c6b93ea7bf41e1e), and STOP_PAPER_NOTIFICATIONS.json (c02580c2d50fc8cf97d6eed267ea88680ec1ead5).

Official https://status.railway.com/ latest update 2026-10-22 13:53 UTC / 22:53 KST: delayed deployment queue cleared and Pro/Enterprise starts at normal speed; Hobby, Free and Trial builds remain unavailable. Incident still Identified / Major Outage. Target workspace tier unverified; no upgrade authorized. No duplicate build trigger.

Fresh Railway describe_service read succeeded for project e7d240ac-2468-45c9-8411-33f5e24f425b, environment 677e12e4-aff7-4880-bc11-ff30de315e68, service 8edd0d1a-3f5e-47fb-8e01-a0cc57935c34. Service live; no staged changes. Latest deployment remains 4bbda6c7-29e8-4811-8322-5f8d0574c1be SUCCESS created 2026-10-20T15:21:53.430Z. Config source pinned to approved 06b88b94a6b5ff0ab5fda0ed0c08a03bc5faa03b; config pin is not proof of a new running build. /data 50 MB, one sfo replica; pre-deploy unit-test command retained. Variable names only were exposed; no values read. No runtime logs or new source execution verified this run.

STOP checkpoint still runtime_applied=false. Saved false switches cannot be called applied. No deployment/restart/outbound/secret/account/order changes. Existing rollback deployment above and last-observed source 25388b431c9c9d38df6a434670586c65f382d604 retained. No strategy-performance or completed-stop claim.

Next: after applicable build availability, review latest production branch and concurrent work/staged changes before one authorized exact-source build. Ensure LBANK_PAPER_NOTIFICATIONS_ENABLED=false and LBANK_ZB_RUNTIME_ENABLED=false, verify runtime and preserve reference/invalidation/risk paths. Do not send ZB status or paper messages to satisfy superseded API_ACK checks. Recheck credential-safety checkpoint before broader deployment; Telegram credential rotation remains unconfirmed. Temporary platform limitation: retain recovery task; old research remains stopped. Suppress duplicate user notification.
