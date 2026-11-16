-- A unit may carry one short line that says how to read its NEWS2 (contract hospital v2.3: the ER is
-- ranked "as an aid"). It is served with the unit; null is no note. Printable text only, one line.
ALTER TABLE run_unit ADD COLUMN score_note TEXT
  CHECK (length(score_note) BETWEEN 1 AND 300 AND score_note !~ '[[:cntrl:]]');
