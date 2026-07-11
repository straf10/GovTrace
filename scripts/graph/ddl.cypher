// E8 (Sprint E) — Neo4j DDL: unique constraints + indexes.
// Τρέχει ΜΕΤΑ το neo4j-admin database import full (βλ. docker-compose.graph.yml).
// Μοντέλο 5 labels (NEO4J_INTEGRATION_FINAL.md): Organization, Contractor, Award, CPV, Nuts.

CREATE CONSTRAINT organization_vat IF NOT EXISTS
FOR (o:Organization) REQUIRE o.vat IS UNIQUE;

CREATE CONSTRAINT contractor_vat IF NOT EXISTS
FOR (c:Contractor) REQUIRE c.vat IS UNIQUE;

CREATE CONSTRAINT award_adam IF NOT EXISTS
FOR (a:Award) REQUIRE a.adam IS UNIQUE;

CREATE CONSTRAINT cpv_code IF NOT EXISTS
FOR (v:CPV) REQUIRE v.code IS UNIQUE;

CREATE CONSTRAINT nuts_code IF NOT EXISTS
FOR (n:Nuts) REQUIRE n.code IS UNIQUE;

// Indexes για τα verify queries (βλ. SPRINT_E_PLAN.md §E8 βήμα 5)
CREATE INDEX award_date IF NOT EXISTS FOR (a:Award) ON (a.date);
// #20 (CHECK 2026-07-11): amount_ex_vat -- η τιμή προέρχεται από το
// totalCostWithoutVAT (ΧΩΡΙΣ ΦΠΑ), το παλιό όνομα amount_vat παραπλανούσε.
CREATE INDEX award_amount IF NOT EXISTS FOR (a:Award) ON (a.amount_ex_vat);
