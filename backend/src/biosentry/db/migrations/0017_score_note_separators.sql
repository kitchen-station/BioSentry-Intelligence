-- 0016 refused control characters with [[:cntrl:]], which depends on the database locale and may let
-- the C1 controls (U+0080 to U+009F) through. Say the ranges out: C0, DEL, C1, and the line and
-- paragraph separators U+2028 and U+2029, which also break a line on a page. Same rule as the API's.
ALTER TABLE run_unit
  DROP CONSTRAINT run_unit_score_note_check,
  ADD CONSTRAINT run_unit_score_note_check
    CHECK (length(score_note) BETWEEN 1 AND 300 AND score_note !~ '[\u0001-\u001f\u007f-\u009f  ]');
