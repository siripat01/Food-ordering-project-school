# Credential Incident Runbook

## Incident

A MongoDB connection URI containing credentials was committed in the legacy backend file `service/order/order.py`. The value also exists in Git history. This repository intentionally does not reproduce that value.

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

## Optional Git history cleanup after rotation

History rewriting is disruptive and is not performed automatically. Coordinate with all collaborators and repository administrators first.

1. Protect a forensic backup and record the affected commit IDs privately.
2. Ask collaborators to stop pushing and merge or close outstanding work.
3. Use `git filter-repo` with a replacement rule or path-specific callback that removes only the exposed URI. Never place the real credential directly in a shell command or shared terminal transcript.
4. Scan every rewritten ref locally with a secret scanner.
5. Force-push the rewritten branches and tags only after explicit repository-owner approval.
6. Invalidate caches/build artifacts and ask collaborators to fresh-clone instead of rebasing old clones.
7. Request cache removal from the Git hosting provider if the old blob remains accessible.
8. Re-run provider audit checks; history cleanup is not a substitute for rotation.

If preserving public history is more important than removing the old blob, leave history intact after rotation and rely on the revoked credential. Document the decision in the incident record.
