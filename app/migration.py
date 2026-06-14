import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

CURRENT_VERSION = 2


async def migrate(conn):
    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    ))
    result = await conn.execute(text("SELECT version FROM schema_version"))
    row = result.fetchone()
    if row is None:
        await conn.execute(text("INSERT INTO schema_version (version) VALUES (1)"))
        current = 1
    else:
        current = row[0]

    if current < 2:
        await _migrate_v2(conn)
        await conn.execute(text(f"UPDATE schema_version SET version = {CURRENT_VERSION}"))
        logger.info(f"Migrated schema from v{current} to v{CURRENT_VERSION}")


async def _migrate_v2(conn):
    safe_adds = [
        "ALTER TABLE tasks ADD COLUMN version INTEGER DEFAULT 1",
        "ALTER TABLE event_log ADD COLUMN notified BOOLEAN DEFAULT 1",
    ]
    for stmt in safe_adds:
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS notification_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(128) NOT NULL,
            channel_type VARCHAR(32) NOT NULL,
            config JSON DEFAULT '{}',
            rate_limit_per_minute INTEGER DEFAULT 30,
            enabled BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS task_channel_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            channel_id INTEGER NOT NULL REFERENCES notification_channels(id) ON DELETE CASCADE,
            template TEXT DEFAULT '',
            enabled BOOLEAN DEFAULT 1,
            UNIQUE(task_id, channel_id)
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS delivery_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            channel_id INTEGER NOT NULL REFERENCES notification_channels(id) ON DELETE CASCADE,
            event_id INTEGER REFERENCES event_log(id) ON DELETE SET NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            attempt INTEGER DEFAULT 1,
            request_payload TEXT DEFAULT '',
            response_status INTEGER DEFAULT 0,
            response_body TEXT DEFAULT '',
            latency_ms INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS monitoring_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            rule_type VARCHAR(32) NOT NULL,
            config JSON DEFAULT '{}',
            logic_group VARCHAR(8) DEFAULT 'AND',
            enabled BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user VARCHAR(64) DEFAULT 'system',
            action VARCHAR(64) NOT NULL,
            resource_type VARCHAR(32) DEFAULT '',
            resource_id INTEGER DEFAULT 0,
            old_value JSON DEFAULT NULL,
            new_value JSON DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))

    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_delivery_log_event ON delivery_log(event_id)"
    ))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_delivery_log_channel ON delivery_log(channel_id, created_at)"
    ))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_monitoring_rules_task ON monitoring_rules(task_id)"
    ))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at)"
    ))
