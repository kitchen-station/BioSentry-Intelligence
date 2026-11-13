-- An admin reads the ward overview and writes nothing. Granted only from the command line
-- (python -m biosentry.db.admin); no route writes a role.
ALTER TABLE clinician DROP CONSTRAINT clinician_role_check;
ALTER TABLE clinician ADD CONSTRAINT clinician_role_check
  CHECK (role IN ('rn', 'md', 'charge_rn', 'admin'));

-- The overview counts the last day of audit rows on every view.
CREATE INDEX audit_log_at ON audit_log (at);

-- An admin is not a carer: never assigned to a patient, whoever tries.
CREATE FUNCTION care_assignment_not_admin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT role FROM clinician WHERE id = NEW.clinician_id) = 'admin' THEN
    RAISE EXCEPTION 'an admin is never assigned to a patient' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER care_assignment_not_admin BEFORE INSERT OR UPDATE ON care_assignment
  FOR EACH ROW EXECUTE FUNCTION care_assignment_not_admin();

-- A role is set when the clinician is created and never changed.
CREATE FUNCTION clinician_role_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.role IS DISTINCT FROM OLD.role THEN
    RAISE EXCEPTION 'a clinician''s role cannot change' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER clinician_role_fixed BEFORE UPDATE OF role ON clinician
  FOR EACH ROW EXECUTE FUNCTION clinician_role_fixed();
