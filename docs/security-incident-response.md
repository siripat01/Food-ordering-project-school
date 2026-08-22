# Credential Incident Runbook

## Incident

A MongoDB connection URI containing credentials was committed in the legacy backend file `service/order/order.py`. The current `main` branch was rewritten to one sanitized root commit on 2026-08-22, but Git hosting caches, forks, and old clones may still retain the former object. This repository intentionally does not reproduce that value.

Assume the credential is compromised even if the database currently shows no suspicious activity.

## Required external rotation

Perform these actions in the MongoDB provider, not in this repository:

1. Create a new application database user with a unique strong password and only the permissions required by this application.
2. Restrict network access to the deployment egress addresses where practical.
3. Put the new URI in the deployment secret manager and update local untracked `.env` files.
4. Deploy and verify readiness with the new credential.
5. Revoke the exposed database user/password immediately after verification.
6. Review provider access/audit logs, database users, network allowlists, and unexpected data changes from the first public commit through the revocation time.
7. Rotate any other credential that reused the same password.

Do not wait for Git history cleanup before revoking the credential. Deleting a string from the current branch does not revoke it.

## Git history cleanup status

The repository owner explicitly authorized a full `main` history rewrite on 2026-08-22. The sanitized root commit was force-pushed with a lease, and the local reflog/unreachable objects were pruned. This cleanup does not revoke the credential.

Remaining coordination:

1. Rotate and revoke the compromised credential externally.
2. Ask collaborators to fresh-clone instead of merging or rebasing old clones.
3. Remove stale build artifacts and request hosting-provider cache removal if the old blob remains accessible.
4. Check forks and non-branch refs separately; rewriting `main` cannot remove another repository's copy.
5. Re-run provider audit checks. History cleanup is not a substitute for rotation.
