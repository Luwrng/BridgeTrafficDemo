-- Cấu trúc 5 cũ, chỉ làm fixture kiểm thử nâng cấp 5 -> 6.
-- Không chạy file này để tạo database vận hành mới; dùng sqlserver_schema.sql.
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.visits', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.visits (
        id varchar(32) NOT NULL PRIMARY KEY,
        realm varchar(16) NOT NULL CONSTRAINT DF_visits_realm DEFAULT 'real',
        plate varchar(16) NOT NULL,
        entered_at datetimeoffset(3) NULL, exited_at datetimeoffset(3) NULL,
        entry_camera char(1) NULL, exit_camera char(1) NULL,
        status varchar(24) NOT NULL, issue nvarchar(1000) NULL,
        created_at datetimeoffset(3) NOT NULL,
        entry_source varchar(64) NULL, deleted_at datetimeoffset(3) NULL
    );
    CREATE INDEX ix_visits_open ON dbo.visits(realm,plate,status) INCLUDE(entered_at,exited_at,entry_camera);
    CREATE INDEX ix_visits_date ON dbo.visits(realm,created_at DESC);
END;
IF OBJECT_ID(N'dbo.passages', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.passages (
        id varchar(32) NOT NULL PRIMARY KEY,
        visit_id varchar(32) NOT NULL REFERENCES dbo.visits(id),
        event_key nvarchar(450) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
        direction varchar(16) NOT NULL, camera_id char(1) NOT NULL,
        occurred_at datetimeoffset(3) NOT NULL,
        source_kind varchar(16) NOT NULL, source_name nvarchar(200) NOT NULL,
        media_time float NOT NULL, confidence float NOT NULL,
        scene_file varchar(80) NOT NULL, crop_file varchar(80) NOT NULL,
        scene_sha256 char(64) NOT NULL, crop_sha256 char(64) NOT NULL
    );
    CREATE INDEX ix_passages_visit ON dbo.passages(visit_id,occurred_at);
END;
IF OBJECT_ID(N'dbo.gate_state', N'U') IS NULL
    CREATE TABLE dbo.gate_state (id int NOT NULL PRIMARY KEY CHECK(id=1), revision bigint NOT NULL DEFAULT 0);
IF NOT EXISTS (SELECT 1 FROM dbo.gate_state WHERE id=1)
    INSERT INTO dbo.gate_state(id,revision) VALUES(1,0);
IF OBJECT_ID(N'dbo.plate_release', N'U') IS NULL
    CREATE TABLE dbo.plate_release (plate varchar(16) NOT NULL PRIMARY KEY, last_seen_at datetimeoffset(3) NOT NULL);
IF OBJECT_ID(N'dbo.visit_corrections', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.visit_corrections (
        id varchar(32) NOT NULL PRIMARY KEY,
        visit_id varchar(32) NOT NULL REFERENCES dbo.visits(id),
        action varchar(32) NOT NULL, camera_id char(1) NOT NULL,
        occurred_at datetimeoffset(3) NOT NULL, confirmed_at datetimeoffset(3) NOT NULL,
        reason nvarchar(500) NOT NULL
    );
    CREATE INDEX ix_corrections_visit ON dbo.visit_corrections(visit_id,confirmed_at);
END;
IF OBJECT_ID(N'dbo.schema_migrations', N'U') IS NULL
    CREATE TABLE dbo.schema_migrations(version int NOT NULL PRIMARY KEY, installed_at datetimeoffset(3) NOT NULL DEFAULT SYSDATETIMEOFFSET());
IF NOT EXISTS (SELECT 1 FROM dbo.schema_migrations WHERE version=5)
    INSERT INTO dbo.schema_migrations(version) VALUES(5);
IF OBJECT_ID(N'dbo.migration_runs', N'U') IS NULL
    CREATE TABLE dbo.migration_runs (
        id varchar(32) NOT NULL PRIMARY KEY, source_sha256 char(64) NOT NULL,
        backup_path nvarchar(1000) NOT NULL, counts_json nvarchar(max) NOT NULL,
        completed_at datetimeoffset(3) NOT NULL DEFAULT SYSDATETIMEOFFSET()
    );
