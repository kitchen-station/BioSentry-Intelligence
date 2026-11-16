-- Every floor has its own labelled staff: one charge nurse, two staff nurses and one physician (W4 keeps
-- its five). The fifteen new people are synthetic and have NO passphrase: nobody can sign in as one until
-- the owner sets it (`make db-passphrase id=<id>`). A person's scope is unchanged: it is decided by the
-- patients assigned to them, never by their unit.
-- `display_name` is the surname the pages show ("Surname - Role - Unit"); null for an admin.
ALTER TABLE clinician ADD COLUMN display_name TEXT
  CHECK (length(display_name) BETWEEN 1 AND 60 AND display_name !~ '[\u0001-\u001f\u007f-\u009f  ]');

INSERT INTO clinician (id, role, unit) VALUES
  ('rn-pangilinan', 'rn', 'ER'), ('rn-guevarra', 'rn', 'ER'), ('md-sarmiento', 'md', 'ER'),
  ('rn-abad', 'rn', 'ICU'), ('rn-lacson', 'rn', 'ICU'), ('md-ocampo', 'md', 'ICU'),
  ('rn-pascual', 'rn', 'MED3A'), ('rn-domingo', 'rn', 'MED3A'), ('md-hizon', 'md', 'MED3A'),
  ('rn-salazar', 'rn', 'MED3B'), ('rn-tolentino', 'rn', 'MED3B'), ('md-evangelista', 'md', 'MED3B'),
  ('rn-ilagan', 'rn', 'SURG5A'), ('rn-cabrera', 'rn', 'SURG5A'), ('md-estrada', 'md', 'SURG5A')
  ON CONFLICT (id) DO NOTHING;

UPDATE clinician c SET display_name = v.name
  FROM (VALUES
    ('rn-manalo', 'Manalo'), ('rn-delacruz', 'de la Cruz'), ('rn-reyes', 'Reyes'),
    ('md-santos', 'Santos'), ('md-aquino', 'Aquino'),
    ('cn-er', 'Dizon'), ('rn-pangilinan', 'Pangilinan'), ('rn-guevarra', 'Guevarra'), ('md-sarmiento', 'Sarmiento'),
    ('cn-icu', 'Macaraeg'), ('rn-abad', 'Abad'), ('rn-lacson', 'Lacson'), ('md-ocampo', 'Ocampo'),
    ('cn-med3a', 'Navarro'), ('rn-pascual', 'Pascual'), ('rn-domingo', 'Domingo'), ('md-hizon', 'Hizon'),
    ('cn-med3b', 'Gonzales'), ('rn-salazar', 'Salazar'), ('rn-tolentino', 'Tolentino'), ('md-evangelista', 'Evangelista'),
    ('cn-surg5a', 'Valdez'), ('rn-ilagan', 'Ilagan'), ('rn-cabrera', 'Cabrera'), ('md-estrada', 'Estrada')
  ) AS v(id, name)
  WHERE c.id = v.id AND c.display_name IS NULL;
