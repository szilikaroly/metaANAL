-- Metaanalízis-asszisztens tudásbázis (SQLite, FTS5)
-- A strukturált, saját szavainkkal összefoglalt tudás (knowledge, formula, decision_rule,
-- checklist_item, tool, worked_example) a tudasbazis/seed/*.json fájlokból épül fel és
-- verziókövetett. A forrásdokumentumok TELJES SZÖVEGE (chunk) csak helyben kerül be a
-- `kb ingest` paranccsal, mert a könyvek/cikkek szerzői jogvédettek — a .sqlite fájl
-- ezért nincs a git-ben.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS source (
    source_id    TEXT PRIMARY KEY,          -- pl. borenstein2009
    citation     TEXT NOT NULL,
    short        TEXT,
    year         INTEGER,
    doi          TEXT,
    kind         TEXT,                      -- book | book_sample | article | tutorial | guide | exemplar | standard | curated | user
    license_note TEXT,
    file_hint    TEXT,                      -- a helyi fájl nevének felismerő mintája (ingesthez)
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS stage (
    stage_id    TEXT PRIMARY KEY,           -- S00 … S14
    ord         INTEGER NOT NULL,
    name_hu     TEXT NOT NULL,
    name_en     TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS knowledge (
    k_id      TEXT PRIMARY KEY,             -- K-<forrás>-<sorszám>
    stage_id  TEXT REFERENCES stage(stage_id),
    kind      TEXT NOT NULL CHECK (kind IN ('concept','guidance','formula','threshold','pitfall',
                                            'convention','tool','example','definition','check','criterion')),
    title     TEXT NOT NULL,
    body      TEXT NOT NULL,
    source_id TEXT REFERENCES source(source_id),
    locator   TEXT,                         -- oldal / fejezet / táblázat
    tags      TEXT
);

CREATE TABLE IF NOT EXISTS formula (
    formula_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    expression TEXT NOT NULL,
    variables  TEXT,
    engine_ref TEXT,                        -- a motor megfelelő függvénye (modul.függvény)
    stage_id   TEXT REFERENCES stage(stage_id),
    source_id  TEXT REFERENCES source(source_id),
    locator    TEXT,
    notes      TEXT
);

CREATE TABLE IF NOT EXISTS decision_rule (
    rule_id        TEXT PRIMARY KEY,        -- D-xxx döntési szabály, Vxxx adatvalidálási szabály
    stage_id       TEXT REFERENCES stage(stage_id),
    applies_to     TEXT NOT NULL,           -- planner | reviewer | evaluator | orchestrator | engine | all
    condition      TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    rationale      TEXT,
    strength       TEXT NOT NULL CHECK (strength IN ('must','should','consider','avoid')),
    machine_check  TEXT,                    -- ha a motor ellenőrzi: kód / JSON-útvonal
    source_ids     TEXT,                    -- vesszővel elválasztva
    locator        TEXT
);

CREATE TABLE IF NOT EXISTS checklist_item (
    item_id       TEXT PRIMARY KEY,
    checklist     TEXT NOT NULL,            -- PRISMA2020 | PRISMA_P | PRISMA_S | PREFLIGHT | REVIEWER | EVALUATOR | AMSTAR2 | GRADE
    section       TEXT,
    ord           INTEGER,
    text          TEXT NOT NULL,
    how_to_verify TEXT,
    stage_id      TEXT REFERENCES stage(stage_id),
    source_ids    TEXT
);

CREATE TABLE IF NOT EXISTS tool (
    tool_id            TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    category           TEXT,                -- database | discovery | registry | screening | software | reference_manager | writing | ai_assistant
    purpose            TEXT,
    access             TEXT,                -- free | free_account | subscription | institutional | api_key | paid
    url                TEXT,
    claude_integration TEXT,                -- pl. "MCP: mcp__PubMed" / "WebFetch" / "kézi"
    notes              TEXT,
    source_ids         TEXT
);

CREATE TABLE IF NOT EXISTS worked_example (
    example_id            TEXT PRIMARY KEY,
    source_id             TEXT REFERENCES source(source_id),
    title                 TEXT,
    location              TEXT,
    effect_measure        TEXT,
    input_type            TEXT,
    method_settings       TEXT,             -- JSON
    studies               TEXT,             -- JSON
    reported_results      TEXT,             -- JSON
    verification          TEXT,             -- JSON
    usable_as_test_oracle INTEGER
);

-- teljes szöveg (csak helyben)
-- stabil hivatkozás: <source_id>#<seq> (a chunk_id újratöltéskor változhat)
CREATE TABLE IF NOT EXISTS chunk (
    chunk_id  INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source(source_id),
    seq       INTEGER NOT NULL,
    locator   TEXT,
    file_sha256 TEXT,                       -- a forrásfájl (ingest_file.sha256): fájlonkénti újratöltés
    text      TEXT NOT NULL
);

-- a betöltött fájlok nyilvántartása: melyik fájl (sha256) szövege van az egyes forrásokban —
-- ez alapján az ingest sosem írja felül csendben egy másik dokumentum teljes szövegét
CREATE TABLE IF NOT EXISTS ingest_file (
    source_id TEXT NOT NULL REFERENCES source(source_id),
    sha256    TEXT NOT NULL,
    filename  TEXT NOT NULL,
    path      TEXT,
    chunks    INTEGER,
    ts        TEXT,
    PRIMARY KEY (source_id, sha256)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text, locator, content='chunk', content_rowid='chunk_id',
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    k_id UNINDEXED, title, body, tags,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE VIRTUAL TABLE IF NOT EXISTS rule_fts USING fts5(
    rule_id UNINDEXED, condition, recommendation, rationale,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE INDEX IF NOT EXISTS idx_knowledge_stage ON knowledge(stage_id);
CREATE INDEX IF NOT EXISTS idx_rule_stage ON decision_rule(stage_id);
CREATE INDEX IF NOT EXISTS idx_chunk_source ON chunk(source_id, seq);

-- kényelmi nézet: minden szabály forrás-hivatkozással
CREATE VIEW IF NOT EXISTS v_rules AS
SELECT r.rule_id, r.stage_id, s.name_hu AS stage, r.applies_to, r.strength, r.condition,
       r.recommendation, r.rationale, r.machine_check, r.source_ids, r.locator
FROM decision_rule r LEFT JOIN stage s ON s.stage_id = r.stage_id;
