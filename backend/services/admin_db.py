"""
Staff (admin) accounts in their OWN database, apart from customers and
bookings (16/11/2026, admin site split).

    ADMIN_MYSQL_DATABASE   default "tickandbook_admin"
    ADMIN_MYSQL_USER / ADMIN_MYSQL_PASSWORD   optional; default = the main
                           MYSQL_USER / MYSQL_PASSWORD (same MySQL server)

Tables here: admins (username, bcrypt hash, name) and super_admins. Only the
admin site's backend (APP_MODE=admin) ever opens this database; the customer
site's backend never does. Everything staff CHANGE (settings, switches,
markups, texts...) still lives in the main database, so it reaches the
customer site straight away, as before.

On first use, if this database has no staff accounts yet, the accounts (with
their ids and password hashes) and super-admin list are copied over from the
main database once, so every login and id keeps working. The old rows in the
main database are left untouched; remove them once the admin site is in use.
"""
import logging
import os
import threading

import mysql.connector
from mysql.connector import pooling

import database

logger = logging.getLogger(__name__)

ADMIN_DB = os.getenv("ADMIN_MYSQL_DATABASE", "tickandbook_admin").strip() or "tickandbook_admin"
_USER = os.getenv("ADMIN_MYSQL_USER", "").strip() or database.MYSQL_USER
_PASSWORD = os.getenv("ADMIN_MYSQL_PASSWORD", "") if os.getenv("ADMIN_MYSQL_USER", "").strip() else database.MYSQL_PASSWORD

_pool = None
_lock = threading.Lock()


def _conn():
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _setup()
                _pool = pooling.MySQLConnectionPool(
                    pool_name="admin_pool", pool_size=5, host=database.MYSQL_HOST, port=database.MYSQL_PORT,
                    user=_USER, password=_PASSWORD, database=ADMIN_DB, autocommit=False,
                    charset="utf8mb4", collation="utf8mb4_unicode_ci")
    return _pool.get_connection()


def _setup() -> None:
    """Create the database and tables, then copy accounts over once."""
    try:
        c = mysql.connector.connect(host=database.MYSQL_HOST, port=database.MYSQL_PORT, user=_USER, password=_PASSWORD)
        cur = c.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{ADMIN_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.close()
        c.close()
    except Exception as e:
        logger.warning(f"Could not create the admin database '{ADMIN_DB}' (create it by hand if needed): {e}")
    c = mysql.connector.connect(host=database.MYSQL_HOST, port=database.MYSQL_PORT, user=_USER,
                                password=_PASSWORD, database=ADMIN_DB)
    cur = c.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                username      VARCHAR(100)  NOT NULL UNIQUE,
                password_hash VARCHAR(255)  NOT NULL,
                full_name     VARCHAR(150),
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS super_admins (
                admin_id INT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
        c.commit()
        cur.execute("SELECT COUNT(*) FROM admins")
        if cur.fetchone()[0] == 0:
            _copy_from_main(cur)
            c.commit()
        cur.execute("SELECT COUNT(*) FROM super_admins")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT IGNORE INTO super_admins (admin_id) SELECT id FROM admins WHERE username='admin'")
            c.commit()
    finally:
        cur.close()
        c.close()
    _loosen_main_links()


def _copy_from_main(cur) -> None:
    main = database.get_db_connection()
    mcur = main.cursor(dictionary=True)
    try:
        try:
            mcur.execute("SELECT id, username, password_hash, full_name, created_at FROM admins")
            admins = mcur.fetchall()
        except Exception:
            admins = []
        try:
            mcur.execute("SELECT admin_id FROM super_admins")
            supers = [r["admin_id"] for r in mcur.fetchall()]
        except Exception:
            supers = []
    finally:
        mcur.close()
        main.close()
    for a in admins:
        cur.execute("INSERT IGNORE INTO admins (id, username, password_hash, full_name, created_at) VALUES (%s,%s,%s,%s,%s)",
                    (a["id"], a["username"], a["password_hash"], a["full_name"], a["created_at"]))
    for sid in supers:
        cur.execute("INSERT IGNORE INTO super_admins (admin_id) VALUES (%s)", (sid,))
    if admins:
        logger.warning(f"Admin database '{ADMIN_DB}': copied {len(admins)} staff account(s) and "
                       f"{len(supers)} super admin(s) from the main database")


def _loosen_main_links() -> None:
    """settings_change_requests (main database) pointed at the main admins
    table with foreign keys; staff created on the admin database would break
    them, so the keys go (the ids stay and are joined across databases)."""
    main = database.get_db_connection()
    cur = main.cursor()
    try:
        for fk in ("fk_settings_change_proposer", "fk_settings_change_reviewer"):
            cur.execute("""SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA=DATABASE()
                           AND TABLE_NAME='settings_change_requests' AND CONSTRAINT_NAME=%s""", (fk,))
            if cur.fetchone()[0]:
                cur.execute(f"ALTER TABLE settings_change_requests DROP FOREIGN KEY {fk}")
        main.commit()
    except Exception as e:
        logger.warning(f"Could not drop the old staff foreign keys on settings_change_requests: {e}")
    finally:
        cur.close()
        main.close()


def _one(sql: str, args: tuple = ()):
    c = _conn()
    cur = c.cursor(dictionary=True)
    try:
        cur.execute(sql, args)
        return cur.fetchone()
    finally:
        cur.close()
        c.close()


def _all(sql: str, args: tuple = ()):
    c = _conn()
    cur = c.cursor(dictionary=True)
    try:
        cur.execute(sql, args)
        return cur.fetchall()
    finally:
        cur.close()
        c.close()


def _write(sql: str, args: tuple = ()) -> int:
    c = _conn()
    cur = c.cursor()
    try:
        cur.execute(sql, args)
        c.commit()
        return cur.rowcount
    finally:
        cur.close()
        c.close()


# ── Staff accounts ───────────────────────────────────────────────────────────

def create_admin_if_not_exists(username: str, password_hash: str, full_name: str | None = None) -> bool:
    if _one("SELECT id FROM admins WHERE username=%s", (username,)):
        return False
    _write("INSERT INTO admins (username, password_hash, full_name) VALUES (%s,%s,%s)", (username, password_hash, full_name))
    return True


def get_admin_by_username(username: str) -> dict | None:
    return _one("SELECT * FROM admins WHERE username=%s", (username,))


def list_admins() -> list[dict]:
    """Never includes password_hash."""
    return _all("SELECT id, username, full_name, created_at FROM admins ORDER BY created_at")


def usernames_by_id(ids) -> dict:
    ids = [i for i in {*ids} if i is not None]
    if not ids:
        return {}
    rows = _all(f"SELECT id, username FROM admins WHERE id IN ({','.join(['%s'] * len(ids))})", tuple(ids))
    return {r["id"]: r["username"] for r in rows}


def is_super_admin(admin_id) -> bool:
    if not admin_id:
        return False
    return _one("SELECT 1 AS ok FROM super_admins WHERE admin_id=%s", (admin_id,)) is not None
