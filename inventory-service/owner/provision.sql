BEGIN;
CREATE ROLE chintamani_owner LOGIN;
GRANT chintamani_owner TO CURRENT_USER;
CREATE SCHEMA chintamani_owner AUTHORIZATION chintamani_owner;
SET LOCAL ROLE chintamani_owner;
REVOKE ALL ON SCHEMA chintamani_owner FROM PUBLIC;
RESET ROLE;
ALTER ROLE chintamani_owner SET search_path TO chintamani_owner;
COMMIT;
