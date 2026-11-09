-- One shared ward: a sim registers its run with a client_id (one per page load), and a different
-- client cannot take the ward over while the newest run is live. NULL: a fixture run, or a run
-- from before this column.
ALTER TABLE run ADD COLUMN client_id TEXT;
