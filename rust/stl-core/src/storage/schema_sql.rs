//! The library schema: the source of truth since the SQLAlchemy models were
//! removed (Phase 4h). Edit it here; a column added to an existing table also
//! needs its `ADD_COLUMNS` entry so older databases are migrated.
//!
//! `TABLES`: in dependency order, each table's `CREATE TABLE` and its indexes.
//! `ADD_COLUMNS`: the `ALTER TABLE ... ADD COLUMN` run for a column missing
//! from an existing table.

pub struct TableDdl {
    pub name: &'static str,
    pub create: &'static str,
    pub indexes: &'static [&'static str],
}

pub const TABLES: &[TableDdl] = &[
    TableDdl {
        name: "categories",
        create: r#"
CREATE TABLE categories (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "collections",
        create: r#"
CREATE TABLE collections (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "library_groups",
        create: r#"
CREATE TABLE library_groups (
	id INTEGER NOT NULL, 
	uuid VARCHAR(36) NOT NULL, 
	name VARCHAR(256) NOT NULL, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (uuid), 
	UNIQUE (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "libraries",
        create: r#"
CREATE TABLE libraries (
	id INTEGER NOT NULL, 
	uuid VARCHAR(36) NOT NULL, 
	name VARCHAR(256) NOT NULL, 
	root TEXT NOT NULL, 
	pattern TEXT, 
	monitored INTEGER NOT NULL DEFAULT 0, 
	last_crawled_at DATETIME, 
	archive_count INTEGER, 
	new_count INTEGER, 
	updated_count INTEGER, 
	origin VARCHAR(32), 
	created_at DATETIME, 
	group_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (uuid), 
	UNIQUE (name), 
	FOREIGN KEY(group_id) REFERENCES library_groups (id)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "creators",
        create: r#"
CREATE TABLE creators (
	name VARCHAR(256) NOT NULL, 
	full_name VARCHAR(512), 
	patreon_url TEXT, 
	artstation_url TEXT, 
	gumroad_url TEXT, 
	myminifactory_url TEXT, 
	PRIMARY KEY (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "filter_cache",
        create: r#"
CREATE TABLE filter_cache (
	id INTEGER NOT NULL, 
	payload TEXT NOT NULL, 
	updated_at DATETIME, 
	PRIMARY KEY (id)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "releases",
        create: r#"
CREATE TABLE releases (
	id INTEGER NOT NULL, 
	name VARCHAR(256) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "scales",
        create: r#"
CREATE TABLE scales (
	id INTEGER NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "sculptors",
        create: r#"
CREATE TABLE sculptors (
	id INTEGER NOT NULL, 
	name VARCHAR(256) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "tags",
        create: r#"
CREATE TABLE tags (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "subcollections",
        create: r#"
CREATE TABLE subcollections (
	id INTEGER NOT NULL, 
	collection_id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_subcollection_per_collection UNIQUE (collection_id, name), 
	FOREIGN KEY(collection_id) REFERENCES collections (id) ON DELETE CASCADE
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "models",
        create: r#"
CREATE TABLE models (
	id INTEGER NOT NULL, 
	title VARCHAR(512), 
	creator VARCHAR(256), 
	source VARCHAR(128), 
	release_year INTEGER, 
	release_month INTEGER, 
	url TEXT, 
	description TEXT, 
	library_id INTEGER NOT NULL, 
	rel_path TEXT NOT NULL, 
	rel_folder TEXT NOT NULL DEFAULT '', 
	archive_hash VARCHAR(64) NOT NULL, 
	archive_size INTEGER, 
	archive_mtime_ns INTEGER, 
	stl_list_hash VARCHAR(64), 
	sequence INTEGER, 
	presupports VARCHAR(32), 
	warning TEXT, 
	compression_rate FLOAT, 
	is_missing BOOLEAN DEFAULT '0' NOT NULL, 
	to_print BOOLEAN DEFAULT '0' NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	sculptor_id INTEGER, 
	collection_id INTEGER, 
	subcollection_id INTEGER, 
	category_id INTEGER, 
	release_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (library_id, rel_path), 
	FOREIGN KEY(library_id) REFERENCES libraries (id), 
	FOREIGN KEY(sculptor_id) REFERENCES sculptors (id) ON DELETE SET NULL, 
	FOREIGN KEY(collection_id) REFERENCES collections (id) ON DELETE SET NULL, 
	FOREIGN KEY(subcollection_id) REFERENCES subcollections (id) ON DELETE SET NULL, 
	FOREIGN KEY(category_id) REFERENCES categories (id) ON DELETE SET NULL, 
	FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE SET NULL
)

"#,
        indexes: &[
            r#"CREATE INDEX ix_models_library_folder ON models (library_id, rel_folder)"#,
            r#"CREATE INDEX ix_models_archive_hash ON models (archive_hash)"#,
            r#"CREATE INDEX ix_models_category_id ON models (category_id)"#,
            r#"CREATE INDEX ix_models_collection_id ON models (collection_id)"#,
            r#"CREATE INDEX ix_models_creator ON models (creator)"#,
            r#"CREATE INDEX ix_models_release_date ON models (release_year, release_month)"#,
            r#"CREATE INDEX ix_models_release_id ON models (release_id)"#,
            r#"CREATE INDEX ix_models_release_year ON models (release_year)"#,
            r#"CREATE INDEX ix_models_sculptor_id ON models (sculptor_id)"#,
            r#"CREATE INDEX ix_models_source ON models (source)"#,
            r#"CREATE INDEX ix_models_subcollection_id ON models (subcollection_id)"#,
            r#"CREATE INDEX ix_models_title ON models (title)"#,
        ],
    },
    TableDdl {
        name: "images",
        create: r#"
CREATE TABLE images (
	id INTEGER NOT NULL, 
	model_id INTEGER NOT NULL, 
	filename VARCHAR(512), 
	thumbnail_path VARCHAR(512), 
	thumbnail BLOB, 
	dhash VARCHAR(32), 
	PRIMARY KEY (id), 
	FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE
)

"#,
        indexes: &[
            r#"CREATE INDEX ix_images_model_id ON images (model_id)"#,
        ],
    },
    TableDdl {
        name: "model_scales",
        create: r#"
CREATE TABLE model_scales (
	model_id INTEGER NOT NULL, 
	scale_id INTEGER NOT NULL, 
	PRIMARY KEY (model_id, scale_id), 
	FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE, 
	FOREIGN KEY(scale_id) REFERENCES scales (id) ON DELETE CASCADE
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "model_tags",
        create: r#"
CREATE TABLE model_tags (
	model_id INTEGER NOT NULL, 
	tag_id INTEGER NOT NULL, 
	PRIMARY KEY (model_id, tag_id), 
	FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE, 
	FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "stl_files",
        create: r#"
CREATE TABLE stl_files (
	model_id INTEGER NOT NULL, 
	filename VARCHAR(512) NOT NULL, 
	file_size INTEGER, 
	archive_hash VARCHAR(64), 
	PRIMARY KEY (model_id, filename),
	FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE
)

"#,
        indexes: &[
        ],
    },
    TableDdl {
        name: "archive_files",
        create: r#"
CREATE TABLE archive_files (
	model_id INTEGER NOT NULL,
	name TEXT NOT NULL,
	size INTEGER,
	crc INTEGER NOT NULL,
	FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE
)

"#,
        indexes: &[
            r#"CREATE INDEX ix_archive_files_model ON archive_files (model_id)"#,
            r#"CREATE INDEX ix_archive_files_crc ON archive_files (crc, size)"#,
        ],
    },
];

/// `(table, column, ddl)`.
pub const ADD_COLUMNS: &[(&str, &str, &str)] = &[
    ("libraries", "group_id", r#"ALTER TABLE "libraries" ADD COLUMN "group_id" INTEGER REFERENCES library_groups (id)"#),
    ("categories", "id", r#"ALTER TABLE "categories" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("categories", "name", r#"ALTER TABLE "categories" ADD COLUMN "name" VARCHAR(128) NOT NULL"#),
    ("collections", "id", r#"ALTER TABLE "collections" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("collections", "name", r#"ALTER TABLE "collections" ADD COLUMN "name" VARCHAR(128) NOT NULL"#),
    ("creators", "name", r#"ALTER TABLE "creators" ADD COLUMN "name" VARCHAR(256) NOT NULL"#),
    ("creators", "full_name", r#"ALTER TABLE "creators" ADD COLUMN "full_name" VARCHAR(512)"#),
    ("creators", "patreon_url", r#"ALTER TABLE "creators" ADD COLUMN "patreon_url" TEXT"#),
    ("creators", "artstation_url", r#"ALTER TABLE "creators" ADD COLUMN "artstation_url" TEXT"#),
    ("creators", "gumroad_url", r#"ALTER TABLE "creators" ADD COLUMN "gumroad_url" TEXT"#),
    ("creators", "myminifactory_url", r#"ALTER TABLE "creators" ADD COLUMN "myminifactory_url" TEXT"#),
    ("filter_cache", "id", r#"ALTER TABLE "filter_cache" ADD COLUMN "id" INTEGER NOT NULL DEFAULT 1"#),
    ("filter_cache", "payload", r#"ALTER TABLE "filter_cache" ADD COLUMN "payload" TEXT NOT NULL"#),
    ("filter_cache", "updated_at", r#"ALTER TABLE "filter_cache" ADD COLUMN "updated_at" DATETIME"#),
    ("releases", "id", r#"ALTER TABLE "releases" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("releases", "name", r#"ALTER TABLE "releases" ADD COLUMN "name" VARCHAR(256) NOT NULL"#),
    ("scales", "id", r#"ALTER TABLE "scales" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("scales", "name", r#"ALTER TABLE "scales" ADD COLUMN "name" VARCHAR(64) NOT NULL"#),
    ("sculptors", "id", r#"ALTER TABLE "sculptors" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("sculptors", "name", r#"ALTER TABLE "sculptors" ADD COLUMN "name" VARCHAR(256) NOT NULL"#),
    ("tags", "id", r#"ALTER TABLE "tags" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("tags", "name", r#"ALTER TABLE "tags" ADD COLUMN "name" VARCHAR(128) NOT NULL"#),
    ("subcollections", "id", r#"ALTER TABLE "subcollections" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("subcollections", "collection_id", r#"ALTER TABLE "subcollections" ADD COLUMN "collection_id" INTEGER NOT NULL"#),
    ("subcollections", "name", r#"ALTER TABLE "subcollections" ADD COLUMN "name" VARCHAR(128) NOT NULL"#),
    ("models", "id", r#"ALTER TABLE "models" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("models", "title", r#"ALTER TABLE "models" ADD COLUMN "title" VARCHAR(512)"#),
    ("models", "creator", r#"ALTER TABLE "models" ADD COLUMN "creator" VARCHAR(256)"#),
    ("models", "source", r#"ALTER TABLE "models" ADD COLUMN "source" VARCHAR(128)"#),
    ("models", "release_year", r#"ALTER TABLE "models" ADD COLUMN "release_year" INTEGER"#),
    ("models", "release_month", r#"ALTER TABLE "models" ADD COLUMN "release_month" INTEGER"#),
    ("models", "url", r#"ALTER TABLE "models" ADD COLUMN "url" TEXT"#),
    ("models", "description", r#"ALTER TABLE "models" ADD COLUMN "description" TEXT"#),
    ("models", "archive_hash", r#"ALTER TABLE "models" ADD COLUMN "archive_hash" VARCHAR(64) NOT NULL"#),
    ("models", "archive_size", r#"ALTER TABLE "models" ADD COLUMN "archive_size" INTEGER"#),
    ("models", "archive_mtime_ns", r#"ALTER TABLE "models" ADD COLUMN "archive_mtime_ns" INTEGER"#),
    ("models", "stl_list_hash", r#"ALTER TABLE "models" ADD COLUMN "stl_list_hash" VARCHAR(64)"#),
    ("models", "sequence", r#"ALTER TABLE "models" ADD COLUMN "sequence" INTEGER"#),
    ("models", "presupports", r#"ALTER TABLE "models" ADD COLUMN "presupports" VARCHAR(32)"#),
    ("models", "warning", r#"ALTER TABLE "models" ADD COLUMN "warning" TEXT"#),
    ("models", "compression_rate", r#"ALTER TABLE "models" ADD COLUMN "compression_rate" FLOAT"#),
    ("models", "is_missing", r#"ALTER TABLE "models" ADD COLUMN "is_missing" BOOLEAN NOT NULL DEFAULT 0"#),
    ("models", "to_print", r#"ALTER TABLE "models" ADD COLUMN "to_print" BOOLEAN NOT NULL DEFAULT 0"#),
    ("models", "created_at", r#"ALTER TABLE "models" ADD COLUMN "created_at" DATETIME"#),
    ("models", "updated_at", r#"ALTER TABLE "models" ADD COLUMN "updated_at" DATETIME"#),
    ("models", "sculptor_id", r#"ALTER TABLE "models" ADD COLUMN "sculptor_id" INTEGER"#),
    ("models", "collection_id", r#"ALTER TABLE "models" ADD COLUMN "collection_id" INTEGER"#),
    ("models", "subcollection_id", r#"ALTER TABLE "models" ADD COLUMN "subcollection_id" INTEGER"#),
    ("models", "category_id", r#"ALTER TABLE "models" ADD COLUMN "category_id" INTEGER"#),
    ("models", "release_id", r#"ALTER TABLE "models" ADD COLUMN "release_id" INTEGER"#),
    ("images", "id", r#"ALTER TABLE "images" ADD COLUMN "id" INTEGER NOT NULL"#),
    ("images", "model_id", r#"ALTER TABLE "images" ADD COLUMN "model_id" INTEGER NOT NULL"#),
    ("images", "filename", r#"ALTER TABLE "images" ADD COLUMN "filename" VARCHAR(512)"#),
    ("images", "thumbnail_path", r#"ALTER TABLE "images" ADD COLUMN "thumbnail_path" VARCHAR(512)"#),
    ("images", "thumbnail", r#"ALTER TABLE "images" ADD COLUMN "thumbnail" BLOB"#),
    ("images", "dhash", r#"ALTER TABLE "images" ADD COLUMN "dhash" VARCHAR(32)"#),
    ("model_scales", "model_id", r#"ALTER TABLE "model_scales" ADD COLUMN "model_id" INTEGER NOT NULL"#),
    ("model_scales", "scale_id", r#"ALTER TABLE "model_scales" ADD COLUMN "scale_id" INTEGER NOT NULL"#),
    ("model_tags", "model_id", r#"ALTER TABLE "model_tags" ADD COLUMN "model_id" INTEGER NOT NULL"#),
    ("model_tags", "tag_id", r#"ALTER TABLE "model_tags" ADD COLUMN "tag_id" INTEGER NOT NULL"#),
    ("stl_files", "model_id", r#"ALTER TABLE "stl_files" ADD COLUMN "model_id" INTEGER NOT NULL"#),
    ("stl_files", "filename", r#"ALTER TABLE "stl_files" ADD COLUMN "filename" VARCHAR(512) NOT NULL"#),
    ("stl_files", "file_size", r#"ALTER TABLE "stl_files" ADD COLUMN "file_size" INTEGER"#),
    ("stl_files", "archive_hash", r#"ALTER TABLE "stl_files" ADD COLUMN "archive_hash" VARCHAR(64)"#),
];

/// `db.ENSURE_INDEXES`: indexes added to existing databases as well.
pub const ENSURE_INDEXES: &[&str] = &[
    r#"CREATE INDEX IF NOT EXISTS ix_models_archive_hash ON models (archive_hash)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_library_folder ON models (library_id, rel_folder)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_release_date ON models (release_year, release_month)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_images_model_id ON images (model_id)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_creator_source ON models (creator, source)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_source ON models (source)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_sculptor_collection ON models (sculptor_id, collection_id)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_collection_subcollection ON models (collection_id, subcollection_id)"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_is_missing ON models (is_missing) WHERE is_missing = 1"#,
    r#"CREATE INDEX IF NOT EXISTS ix_images_dhash ON images (dhash) WHERE dhash IS NOT NULL"#,
    r#"CREATE INDEX IF NOT EXISTS ix_models_presupports ON models (presupports) WHERE presupports IS NOT NULL"#,
    // The filter pane's warning counts read this instead of every row of `models`.
    r#"CREATE INDEX IF NOT EXISTS ix_models_warning ON models (warning) WHERE warning IS NOT NULL"#,
];

/// `db.MODELS_VIEW_SQL`, recreated on every start.
pub const MODELS_VIEW: &str = r#"CREATE VIEW models_view AS
SELECT
    m.id, m.title, m.creator, m.source,
    m.release_year, m.release_month,
    path_join(lib.root, m.rel_path) AS archive_path, m.presupports, m.warning,
    m.library_id, m.rel_path, m.rel_folder, coalesce(grp.name, lib.name) AS library_name,
    lib.group_id AS library_group_id, lib.name AS folder_name,
    m.archive_size, m.compression_rate,
    m.sequence, m.created_at,
    m.sculptor_id, m.collection_id, m.subcollection_id,
    m.category_id, m.release_id,
    m.is_missing, m.archive_hash, m.to_print,
    scu.name AS sculptor_name,
    col.name AS collection_name,
    sub.name AS subcollection_name,
    cat.name AS category_name,
    rel.name AS release_name
FROM models m
LEFT JOIN sculptors      scu ON m.sculptor_id      = scu.id
LEFT JOIN collections    col ON m.collection_id    = col.id
LEFT JOIN subcollections sub ON m.subcollection_id = sub.id
LEFT JOIN categories     cat ON m.category_id      = cat.id
LEFT JOIN releases       rel ON m.release_id       = rel.id
JOIN libraries           lib ON m.library_id       = lib.id
LEFT JOIN library_groups grp ON lib.group_id       = grp.id"#;

/// SQL for a model's absolute archive path (`m` is the models table's alias):
/// its library's root joined with its relative path, in this system's spelling.
pub fn archive_path_sql(m: &str) -> String {
    format!("path_join((SELECT root FROM libraries WHERE libraries.id = {m}.library_id), {m}.rel_path)")
}

/// SQL for the absolute folder of a model's archive in `/` form (the folder
/// tree's spelling).
pub fn archive_folder_sql(m: &str) -> String {
    format!("posix_join((SELECT root FROM libraries WHERE libraries.id = {m}.library_id), {m}.rel_folder)")
}

/// `vectors`: the embedding model the stored vectors must come from.
pub const EMBED_MODEL_ID: &str = r#"clip-vit-b32-onnx"#;
pub const EMBED_DIM: i64 = 512;
pub const VECTOR_TABLE: &str = r#"image_vec"#;
pub const VECTOR_INFO: &str = r#"vector_store_info"#;
pub const VECTOR_INFO_CREATE: &str = r#"CREATE TABLE IF NOT EXISTS vector_store_info (model TEXT NOT NULL, dim INTEGER NOT NULL)"#;
pub const VECTOR_TABLE_CREATE: &str = r#"CREATE VIRTUAL TABLE IF NOT EXISTS image_vec USING vec0(image_id INTEGER PRIMARY KEY, embedding float[512] distance_metric=cosine)"#;
/// `vectors._CREATE_TRIGGER`: per connection, deletes a removed image's vector.
pub const VECTOR_DELETE_TRIGGER: &str = r#"CREATE TEMP TRIGGER IF NOT EXISTS image_vec_image_deleted AFTER DELETE ON main.images BEGIN DELETE FROM image_vec WHERE image_id = OLD.id; END"#;
