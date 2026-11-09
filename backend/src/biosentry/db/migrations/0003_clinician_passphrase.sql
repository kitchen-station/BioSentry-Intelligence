-- Real sign-in. NULL means no passphrase is set: that clinician cannot sign in (fails closed).
-- The value is `scrypt$n$r$p$salt$hash` (biosentry.db.passphrase); the passphrase is never stored.
ALTER TABLE clinician ADD COLUMN passphrase_hash TEXT;

-- Sessions issued by the old stub sign-in (a clinician id alone) must not outlive it.
DELETE FROM session;

-- Sign-in counts a clinician id's recent failures in the audit log before it hashes anything.
CREATE INDEX audit_log_sign_in ON audit_log (resource, at) WHERE action = 'sign_in';
