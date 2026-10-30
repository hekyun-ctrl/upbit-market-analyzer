# Deployment retry after OAuth reconnect

User explicitly approved production deploy and notification resume. OAuth account and existing service reads succeeded. Reviewed exactly one staged change: source.commitSha=06b88b94a6b5ff0ab5fda0ed0c08a03bc5faa03b.

accept_deploy returned committed=true, deploymentStatus=triggered, workflow commitChanges/677e12e4-aff7-4880-bc11-ff30de315e68/23ae5cf2-dd36-49ba-a3c2-f66fa0b558a8. Read-back confirmed source pin applied and no pending work. Repeated deployment listing still showed only old live SUCCESS 4bbda6c7-29e8-4811-8322-5f8d0574c1be, source 25388b431c9c9d38df6a434670586c65f382d604. Environment health: existing monitor online 1/1, no recent failures.

Explicit connect_service_source for the approved exact source then returned: `Deploys have been paused temporarily` (isError=true, INVALID_ARGUMENT). No new build/deployment ID was created. Pause scope/cause/duration unknown; do not describe this as user-disabled setting without evidence. No exposed pause/resume operation found. Did not reuse old build, create another service, read secrets, or modify unrelated Upbit service.

Status: NOT DEPLOYED. New ZB supervisor notification/runtime unverified; reference monitor remains old online release. Rollback remains old live deployment above. Next: once Railway temporary pause clears, build exact approved source in existing monitor service, observe terminal SUCCESS, verify runtime source/ZB_RUNTIME_STARTED/native freshness/API_ACK and reference/risk path. New ZB entries remain fail-closed pending qualification. No automation resumed.
