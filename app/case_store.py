"""案件、任务、证据、审核与审计的数据层。

设计原则：
1. 用 SQLite 做真实持久化，不把“演示任务”写进 JSON 冒充办案记录；
2. 案件分数只从 data/hotspots.geojson 或 data/risk_grid.geojson 读取，不由前端传入；
3. 证据文件落盘并计算 SHA-256，报告里如实标注“哈希校验不是法定电子签章”；
4. 所有写操作都写审计日志，权限在服务端校验。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app import config

INSTANCE = Path(os.getenv("POLICE_INSTANCE_DIR", str(config.ROOT / "instance")))
DB_PATH = Path(os.getenv("POLICE_DB_PATH", str(INSTANCE / "police.db")))
EVIDENCE_DIR = Path(os.getenv("POLICE_EVIDENCE_DIR", str(INSTANCE / "evidence")))
EVIDENCE_KEY_PATH = INSTANCE / ".evidence_key"
COOKIE_NAME = "police_session"
SESSION_HOURS = int(os.getenv("POLICE_SESSION_HOURS", "12"))
PBKDF2_ROUNDS = 260_000

ROLE_LABELS = {
    "admin": "系统管理员",
    "commander": "指挥员",
    "officer": "民警",
    "reviewer": "审核员",
}
CASE_STATUSES = ["待派单", "核查中", "待复核", "已复核", "已归档"]
TASK_STATUSES = ["待接收", "核查中", "已完成", "已退回"]
REFERRAL_DEPARTMENTS = [
    "生态环境局",
    "自然资源局",
    "水利局",
    "应急管理局",
    "城市管理局",
    "林业和草原局",
    "农业农村局",
    "公安机关（行刑衔接）",
]
REFERRAL_STATUSES = ["待接收", "已接收", "已反馈", "已退回"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_dirs() -> None:
    INSTANCE.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _evidence_key() -> bytes:
    """证据链 HMAC 密钥。密钥与数据库分开存放，删除证据行后无法伪造链头。"""
    env_key = os.getenv("POLICE_EVIDENCE_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")
    if EVIDENCE_KEY_PATH.is_file():
        text = EVIDENCE_KEY_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text.encode("utf-8")
    _ensure_dirs()
    key = secrets.token_hex(32)
    EVIDENCE_KEY_PATH.write_text(key, encoding="utf-8")
    try:
        os.chmod(EVIDENCE_KEY_PATH, 0o600)
    except OSError:
        pass
    print(f"[evidence] 已生成证据链密钥：{EVIDENCE_KEY_PATH}（请与数据库分开备份）", flush=True)
    return key.encode("utf-8")


def connect() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','commander','officer','reviewer')),
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    case_no TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL DEFAULT 'hotspot',
    source_id TEXT NOT NULL,
    hotspot_id TEXT NOT NULL,
    hotspot_name TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    priority_score REAL,
    priority_level TEXT,
    priority_label TEXT,
    current_danger REAL,
    current_danger_level TEXT,
    current_danger_label TEXT,
    lon REAL,
    lat REAL,
    status TEXT NOT NULL DEFAULT '待派单',
    assignee_id INTEGER REFERENCES users(id),
    assignee_name TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    due_at TEXT,
    closed_at TEXT,
    source TEXT NOT NULL,
    data_snapshot TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    assignee_id INTEGER REFERENCES users(id),
    assignee_name TEXT,
    status TEXT NOT NULL DEFAULT '待接收',
    due_at TEXT,
    note TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    mime TEXT,
    captured_at TEXT,
    lon REAL,
    lat REAL,
    note TEXT,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    reviewer_id INTEGER NOT NULL REFERENCES users(id),
    reviewer_name TEXT,
    decision TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor_id INTEGER,
    actor_name TEXT,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS arrival_tokens (
    token_hash TEXT PRIMARY KEY,
    token_hint TEXT,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_by_name TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    max_uses INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS checkins (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    token_hash TEXT,
    task_id TEXT,
    actor_name TEXT,
    lon REAL,
    lat REAL,
    accuracy REAL,
    captured_at TEXT,
    note TEXT,
    device TEXT,
    source TEXT,
    evidence_ids TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_chains (
    case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
    head TEXT NOT NULL,
    count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    sealed_at TEXT,
    backfilled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE REFERENCES cases(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL CHECK(verdict IN ('confirmed','excluded','inconclusive')),
    note TEXT,
    evidence_id TEXT,
    verified_by INTEGER,
    verified_by_name TEXT,
    verified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_assignee ON cases(assignee_id);
CREATE INDEX IF NOT EXISTS idx_tasks_case ON tasks(case_id);
CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence(case_id);
CREATE INDEX IF NOT EXISTS idx_reviews_case ON reviews(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit_logs(object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_arrival_case ON arrival_tokens(case_id);
CREATE INDEX IF NOT EXISTS idx_checkins_case ON checkins(case_id, created_at);
CREATE INDEX IF NOT EXISTS idx_verifications_verdict ON verifications(verdict);

CREATE TABLE IF NOT EXISTS case_referrals (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    department TEXT NOT NULL,
    contact TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT '待接收' CHECK(status IN ('待接收','已接收','已反馈','已退回')),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_by_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    due_at TEXT,
    feedback TEXT
);

CREATE INDEX IF NOT EXISTS idx_referrals_case ON case_referrals(case_id, created_at);
CREATE INDEX IF NOT EXISTS idx_referrals_status ON case_referrals(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(case_id, sha256);
"""


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, password_hash)


def init_db() -> None:
    _ensure_dirs()
    with write_conn() as conn:
        conn.executescript(SCHEMA)
        _backfill_chains(conn)
        count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if count:
            return
        demo_mode = os.getenv("POLICE_DEMO_MODE", "1") != "0"
        if demo_mode:
            demo_users = [
                ("admin", "系统管理员", "admin", "Admin@12345"),
                ("commander", "指挥员", "commander", "Commander@12345"),
                ("officer", "现场民警", "officer", "Officer@12345"),
                ("reviewer", "审核员", "reviewer", "Reviewer@12345"),
            ]
            for username, display_name, role, password in demo_users:
                pw_hash, salt = hash_password(password)
                conn.execute(
                    "INSERT INTO users(username, display_name, role, password_hash, salt, created_at) VALUES(?,?,?,?,?,?)",
                    (username, display_name, role, pw_hash, salt, now_iso()),
                )
            print(
                "[auth] 已创建演示账号（admin/commander/officer/reviewer）。"
                "公网部署前请设置 POLICE_DEMO_MODE=0 并修改密码。",
                flush=True,
            )
        else:
            password = os.getenv("POLICE_ADMIN_PASSWORD")
            if not password:
                password = secrets.token_urlsafe(12)
                print(f"[auth] 未设置 POLICE_ADMIN_PASSWORD，临时管理员密码：{password}", flush=True)
            pw_hash, salt = hash_password(password)
            conn.execute(
                "INSERT INTO users(username, display_name, role, password_hash, salt, created_at) VALUES(?,?,?,?,?,?)",
                ("admin", "系统管理员", "admin", pw_hash, salt, now_iso()),
            )


def public_user(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    return {
        "id": d["id"],
        "username": d["username"],
        "display_name": d["display_name"],
        "role": d["role"],
        "role_label": ROLE_LABELS.get(d["role"], d["role"]),
        "active": bool(d.get("active", 1)),
    }


def authenticate(username: str, password: str) -> dict | None:
    with read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"], row["salt"]):
        return None
    return public_user(row)


def create_session(user_id: int, user_agent: str | None = None) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(hours=SESSION_HOURS)
    with write_conn() as conn:
        conn.execute(
            "INSERT INTO sessions(token, user_id, created_at, expires_at, user_agent) VALUES(?,?,?,?,?)",
            (token, user_id, created.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"), (user_agent or "")[:300]),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    return token, expires.isoformat(timespec="seconds")


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with read_conn() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=? AND s.expires_at>? AND u.active=1",
            (token, now_iso()),
        ).fetchone()
    return public_user(row) if row else None


def delete_session(token: str | None) -> None:
    if not token:
        return
    with write_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    if len(new_password) < 8:
        raise ValueError("新密码至少 8 位")
    with read_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not verify_password(old_password, row["password_hash"], row["salt"]):
        return False
    pw_hash, salt = hash_password(new_password)
    with write_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, salt=? WHERE id=?", (pw_hash, salt, user_id)
        )
    return True


def create_user(username: str, display_name: str, role: str, password: str) -> dict:
    if role not in ROLE_LABELS:
        raise ValueError("角色不合法")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise ValueError("用户名只允许 3-32 位字母、数字、下划线、点或短横线")
    pw_hash, salt = hash_password(password)
    with write_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(username, display_name, role, password_hash, salt, created_at) VALUES(?,?,?,?,?,?)",
                (username, display_name or username, role, pw_hash, salt, now_iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc
        row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return public_user(row)


def list_users(role: str | None = None) -> list[dict]:
    sql = "SELECT * FROM users WHERE active=1"
    args: list[Any] = []
    if role:
        sql += " AND role=?"
        args.append(role)
    sql += " ORDER BY role, display_name"
    with read_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [public_user(r) for r in rows]


def user_by_id(user_id: int) -> dict | None:
    with read_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
    return public_user(row) if row else None


def audit(
    conn: sqlite3.Connection,
    actor: dict | None,
    action: str,
    object_type: str,
    object_id: str,
    detail: Any = None,
) -> None:
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    conn.execute(
        "INSERT INTO audit_logs(at, actor_id, actor_name, action, object_type, object_id, detail) VALUES(?,?,?,?,?,?,?)",
        (
            now_iso(),
            actor["id"] if actor else None,
            actor["display_name"] if actor else "匿名",
            action,
            object_type,
            object_id,
            str(detail)[:2000] if detail is not None else None,
        ),
    )


def record_audit(
    actor: dict | None,
    action: str,
    object_type: str,
    object_id: str,
    detail: Any = None,
) -> None:
    """为“只读但需要留痕”的导出动作提供独立审计入口。"""
    with write_conn() as conn:
        audit(conn, actor, action, object_type, object_id, detail)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _centroid(geom: dict) -> tuple[float, float]:
    coords: list[list[float]] = []

    def collect(g: dict):
        if not g:
            return
        if g.get("type") == "Polygon":
            coords.extend(g.get("coordinates", [[]])[0])
        elif g.get("type") == "MultiPolygon":
            for poly in g.get("coordinates", []):
                if poly:
                    coords.extend(poly[0])
        elif g.get("type") == "Point":
            coords.append(g.get("coordinates", [0, 0]))

    collect(geom)
    if not coords:
        return 0.0, 0.0
    pts = coords[:-1] if len(coords) > 1 and coords[0] == coords[-1] else coords
    return (
        round(sum(float(p[0]) for p in pts) / len(pts), 6),
        round(sum(float(p[1]) for p in pts) / len(pts), 6),
    )


def _source_record(source_type: str, source_id: str) -> dict:
    if source_type == "imagery":
        for path in (config.DATA / "mpc_scenes.json", INSTANCE / "imagery_watch.json"):
            if not path.is_file():
                continue
            data = _read_json(path)
            for key in ("sentinel2", "landsat", "latest", "new_scenes"):
                for item in data.get(key) or []:
                    if not isinstance(item, dict) or str(item.get("id")) != source_id:
                        continue
                    bbox = item.get("bbox") or []
                    lon = lat = 0.0
                    if len(bbox) >= 4:
                        lon = round((float(bbox[0]) + float(bbox[2])) / 2, 6)
                        lat = round((float(bbox[1]) + float(bbox[3])) / 2, 6)
                    return {
                        "source_type": "imagery",
                        "source_id": source_id,
                        "name": f"新影像 {source_id}",
                        "lon": lon,
                        "lat": lat,
                        "priority_score": 0.0,
                        "priority_level": "",
                        "priority_label": "待核查",
                        "current_danger": 0.0,
                        "current_danger_level": "",
                        "current_danger_label": "",
                        "properties": item,
                    }
        raise LookupError(f"未找到影像 {source_id}（请先刷新影像到访）")
    if source_type == "hotspot":
        data = _read_json(config.DATA / "hotspots.geojson")
        feats = data.get("features", [])
    elif source_type == "grid":
        data = _read_json(config.DATA / "risk_grid.geojson")
        feats = data.get("features", [])
    else:
        raise ValueError("source_type 只能是 hotspot、grid 或 imagery")
    for feat in feats:
        props = feat.get("properties", {})
        sid = props.get("id") if source_type == "hotspot" else props.get("cell_id")
        if sid == source_id:
            lon, lat = _centroid(feat.get("geometry", {}))
            if source_type == "hotspot" and props.get("centroid"):
                lon, lat = float(props["centroid"][0]), float(props["centroid"][1])
            name = props.get("name") or (
                f"格网 {source_id}（{props.get('dominant_hazard_name', '综合风险')}）"
            )
            return {
                "source_type": source_type,
                "source_id": source_id,
                "name": name,
                "lon": lon,
                "lat": lat,
                "priority_score": float(
                    props.get("priority_score", props.get("priority", props.get("risk", 0))) or 0
                ),
                "priority_level": props.get("danger_level", props.get("current_danger_level", "")),
                "priority_label": props.get("danger_label", props.get("current_danger_label", "")),
                "current_danger": float(
                    props.get("current_danger", props.get("current_natural_risk", 0)) or 0
                ),
                "current_danger_level": props.get(
                    "current_danger_level", props.get("current_natural_level", "")
                ),
                "current_danger_label": props.get(
                    "current_danger_label", props.get("current_natural_label", "")
                ),
                "properties": props,
            }
    raise LookupError(f"未找到数据源 {source_type}:{source_id}")


def _next_case_no(conn: sqlite3.Connection) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"CASE-{day}-"
    row = conn.execute(
        "SELECT case_no FROM cases WHERE case_no LIKE ? ORDER BY case_no DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    seq = int(row["case_no"].rsplit("-", 1)[-1]) + 1 if row else 1
    return f"{prefix}{seq:03d}"


def create_case(
    actor: dict,
    source_type: str,
    source_id: str,
    title: str | None,
    summary: str | None,
    assignee_id: int | None,
    due_at: str | None,
) -> dict:
    rec = _source_record(source_type, source_id)
    assignee = None
    if assignee_id:
        assignee = user_by_id(int(assignee_id))
        if not assignee:
            raise ValueError("承办人不存在或已停用")
    if actor["role"] == "officer":
        if assignee_id and int(assignee_id) != actor["id"]:
            raise PermissionError("民警只能把案件指派给自己")
        assignee = actor
    with write_conn() as conn:
        case_no = _next_case_no(conn)
        case_id = f"CASE-{uuid.uuid4().hex[:10].upper()}"
        title = (title or f"{rec['name']}核查").strip()[:120]
        summary = (summary or "").strip()[:2000]
        snapshot = {
            "source": {
                "hotspot": "data/hotspots.geojson",
                "grid": "data/risk_grid.geojson",
                "imagery": "data/mpc_scenes.json",
            }[source_type],
            "source_type": source_type,
            "source_id": source_id,
            "snapshot_at": now_iso(),
            "properties": rec["properties"],
        }
        conn.execute(
            """INSERT INTO cases(
                id, case_no, source_type, source_id, hotspot_id, hotspot_name, title, summary,
                priority_score, priority_level, priority_label, current_danger, current_danger_level,
                current_danger_label, lon, lat, status, assignee_id, assignee_name, created_by,
                created_at, updated_at, due_at, source, data_snapshot
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                case_id, case_no, source_type, source_id, source_id, rec["name"], title, summary,
                rec["priority_score"], rec["priority_level"], rec["priority_label"],
                rec["current_danger"], rec["current_danger_level"], rec["current_danger_label"],
                rec["lon"], rec["lat"], "待派单", assignee["id"] if assignee else None,
                assignee["display_name"] if assignee else None, actor["id"], now_iso(), now_iso(),
                due_at, "data/hotspots.geojson / data/risk_grid.geojson / data/mpc_scenes.json + 公开遥感/气象数据",
                json.dumps(snapshot, ensure_ascii=False),
            ),
        )
        audit(
            conn, actor, "create_case", "case", case_id,
            {"case_no": case_no, "source_type": source_type, "source_id": source_id},
        )
    return get_case(case_id, actor)


def demo_actor() -> dict:
    """演示转办使用的系统操作者；仅在未登录 /sixg 时兜底。"""
    with read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username='admin' AND active=1"
        ).fetchone()
    if not row:
        raise LookupError("案件库未初始化演示账号")
    return public_user(row)


def sixg_lead_status(case_no: str) -> dict | None:
    """按 6G 演示线索编号查询已转案件，不返回敏感快照。"""
    with read_conn() as conn:
        row = conn.execute(
            """SELECT c.id, c.case_no, c.title, c.status, c.due_at, c.assignee_name,
                      c.created_at, c.updated_at,
                      (SELECT COUNT(*) FROM tasks t WHERE t.case_id=c.id) AS task_count,
                      (SELECT COUNT(*) FROM evidence e WHERE e.case_id=c.id) AS evidence_count
               FROM cases c WHERE c.source_type='sixg' AND c.source_id=?""",
            (case_no,),
        ).fetchone()
    return dict(row) if row else None


def _safe_evidence_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name or "evidence")
    return cleaned[:160] or "evidence"


def _add_bytes_evidence(
    actor: dict, case_id: str, filename: str, data: bytes, mime: str,
    note: str, lon: float | None, lat: float | None,
) -> dict:
    case_dir = EVIDENCE_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    final = case_dir / f"{digest[:16]}_{_safe_evidence_name(filename)}"
    if not final.exists():
        final.write_bytes(data)
    rel = final.relative_to(config.ROOT).as_posix()
    return add_evidence(
        actor, case_id, None, filename, rel, digest, len(data), mime,
        now_iso(), lon, lat, note,
    )


def _add_file_evidence(
    actor: dict, case_id: str, filename: str, source: Path, mime: str,
    note: str, lon: float | None, lat: float | None,
) -> dict | None:
    if not source.is_file():
        return None
    case_dir = EVIDENCE_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    final = case_dir / f"{digest[:16]}_{_safe_evidence_name(filename)}"
    if not final.exists():
        shutil.copy2(source, final)
    rel = final.relative_to(config.ROOT).as_posix()
    return add_evidence(
        actor, case_id, None, filename, rel, digest, len(data), mime,
        now_iso(), lon, lat, note,
    )


def import_sixg_lead(
    actor: dict,
    payload: dict[str, Any],
    *,
    assignee_id: int | None = None,
    due_hours: int = 72,
    extra_evidence: list[dict[str, Any]] | None = None,
) -> dict:
    """把 /api/sixg/demo 的演示线索转为正式案件、任务与证据。

    同一 SIXG 编号只建一次案；重复调用返回已有案件，避免演示按钮刷出多条案件。
    """
    case_data = payload.get("case") or {}
    sixg_no = str(case_data.get("case_no") or "").strip()
    if not re.match(r"^SIXG-\d{8}-[A-Z]{2,12}$", sixg_no):
        raise ValueError("演示线索编号不合法")
    with read_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM cases WHERE source_type='sixg' AND source_id=?",
            (sixg_no,),
        ).fetchone()
    if existing:
        try:
            case = get_case(existing["id"], actor)
        except PermissionError:
            case = None
        return {
            "created": False,
            "duplicate": True,
            "case": case,
            "case_ref": sixg_lead_status(sixg_no),
        }

    center = case_data.get("center_lonlat") or [0.0, 0.0]
    try:
        lon, lat = float(center[0]), float(center[1])
    except (TypeError, ValueError, IndexError):
        lon = lat = 0.0
    confidence = float(case_data.get("confidence") or 0.0)
    area_ha = float(case_data.get("area_ha") or 0.0)
    hazard = str(case_data.get("hazard") or "新增扰动")
    score = round(confidence * 100.0, 1)
    priority_level = "high" if score >= 85 else "medium" if score >= 70 else "low"
    priority_label = {"high": "高", "medium": "中", "low": "低"}[priority_level]

    target = None
    if assignee_id:
        target = user_by_id(int(assignee_id))
        if not target:
            raise ValueError("承办人不存在或已停用")
    elif actor["role"] == "officer":
        target = actor
    else:
        with read_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username='officer' AND active=1"
            ).fetchone()
        target = public_user(row) if row else actor
    if actor["role"] == "officer" and target and int(target["id"]) != int(actor["id"]):
        raise PermissionError("民警只能把案件指派给自己")

    due_hours = max(12, min(int(due_hours or 72), 720))
    due_at = (datetime.now(timezone.utc) + timedelta(hours=due_hours)).isoformat(timespec="seconds")
    now = now_iso()
    case_id = f"CASE-{uuid.uuid4().hex[:10].upper()}"
    summary = (
        f"{hazard}；面积约 {area_ha:.2f} 公顷；置信度 {confidence:.2f}。"
        f"来源：{case_data.get('source') or '天枢·6G 空天地一体'}。"
        "演示线索一键转办，需人工复核后形成核查结论。"
    )[:2000]
    snapshot = {
        "source_type": "sixg",
        "source_id": sixg_no,
        "snapshot_at": now,
        "converted_by": actor.get("display_name"),
        "assignee": target.get("display_name") if target else None,
        "sixg_demo": payload,
    }
    with write_conn() as conn:
        case_no = _next_case_no(conn)
        conn.execute(
            """INSERT INTO cases(
                id, case_no, source_type, source_id, hotspot_id, hotspot_name, title, summary,
                priority_score, priority_level, priority_label, current_danger, current_danger_level,
                current_danger_label, lon, lat, status, assignee_id, assignee_name, created_by,
                created_at, updated_at, due_at, source, data_snapshot
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                case_id, case_no, "sixg", sixg_no, sixg_no, f"6G 线索 {sixg_no}",
                str(case_data.get("title") or f"{hazard}核查")[:120], summary,
                score, priority_level, priority_label, score, priority_level, priority_label,
                lon, lat, "待派单", target["id"] if target else None,
                target["display_name"] if target else None, actor["id"], now, now,
                due_at, "天枢·6G 空天地一体 / Sentinel-2 / 边缘 AI",
                json.dumps(snapshot, ensure_ascii=False, default=str),
            ),
        )
        audit(
            conn, actor, "convert_sixg_lead", "case", case_id,
            {"case_no": case_no, "source_id": sixg_no, "due_at": due_at,
             "assignee": target["display_name"] if target else None},
        )

    task = create_task(
        actor, case_id,
        f"现场核查 {sixg_no} · {hazard}",
        target["id"] if target else None,
        due_at,
        "由 6G 演示线索一键转办；请核对图斑、固定证据并反馈核查结果。",
    )
    evidence_count = 0
    report = payload.get("report")
    if isinstance(report, str) and report.strip():
        _add_bytes_evidence(
            actor, case_id, "sixg_lead_report.md", report.encode("utf-8"),
            "text/markdown; charset=utf-8", f"6G 演示线索 {sixg_no} 核查报告", lon, lat,
        )
        evidence_count += 1
    _add_bytes_evidence(
        actor, case_id, "sixg_lead.json",
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        "application/json", f"6G 演示线索 {sixg_no} 原始载荷（演示口径）", lon, lat,
    )
    evidence_count += 1
    for item in extra_evidence or []:
        try:
            path = Path(item.get("path", ""))
            added = _add_file_evidence(
                actor, case_id, str(item.get("name") or path.name), path,
                str(item.get("mime") or "application/octet-stream"),
                str(item.get("note") or f"6G 演示线索 {sixg_no} 证据"), lon, lat,
            )
            if added:
                evidence_count += 1
        except Exception:
            continue
    case = get_case(case_id, actor)
    return {
        "created": True,
        "duplicate": False,
        "case": case,
        "task": task,
        "evidence_added": evidence_count,
        "due_at": due_at,
    }


def _case_row(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()


def _chain_links(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    """按上传顺序重算证据链；字段全部参与 HMAC，改任一字段都会断链。"""
    rows = conn.execute(
        "SELECT id, filename, sha256, size, task_id, captured_at, lon, lat, note, created_at "
        "FROM evidence WHERE case_id=? ORDER BY created_at, id",
        (case_id,),
    ).fetchall()
    key = _evidence_key()
    prev = "GENESIS"
    links: list[dict] = []
    for seq, ev in enumerate(rows, 1):
        payload = json.dumps(
            [
                prev, ev["id"], ev["filename"], ev["sha256"], ev["size"],
                ev["task_id"], ev["captured_at"], ev["lon"], ev["lat"],
                ev["note"], ev["created_at"],
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        link = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        links.append(
            {
                "seq": seq,
                "evidence_id": ev["id"],
                "filename": ev["filename"],
                "sha256": ev["sha256"],
                "size": ev["size"],
                "captured_at": ev["captured_at"],
                "created_at": ev["created_at"],
                "prev_hash": prev,
                "chain_hash": link,
            }
        )
        prev = link
    return links


def _update_chain(conn: sqlite3.Connection, case_id: str, *, backfilled: int = 0) -> dict | None:
    links = _chain_links(conn, case_id)
    if not links:
        conn.execute("DELETE FROM case_chains WHERE case_id=?", (case_id,))
        return None
    head = links[-1]["chain_hash"]
    existing = conn.execute(
        "SELECT backfilled, sealed_at FROM case_chains WHERE case_id=?", (case_id,)
    ).fetchone()
    flag = 1 if (backfilled or (existing and existing["backfilled"])) else 0
    sealed = existing["sealed_at"] if existing and existing["sealed_at"] else (
        now_iso() if flag else None
    )
    conn.execute(
        """INSERT INTO case_chains(case_id, head, count, updated_at, sealed_at, backfilled)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(case_id) DO UPDATE SET
             head=excluded.head, count=excluded.count, updated_at=excluded.updated_at,
             sealed_at=excluded.sealed_at, backfilled=excluded.backfilled""",
        (case_id, head, len(links), now_iso(), sealed, flag),
    )
    return {"head": head, "count": len(links), "backfilled": flag, "sealed_at": sealed}


def _backfill_chains(conn: sqlite3.Connection) -> None:
    """为历史证据补链头。补链只能从补链时刻起锚定，不能证明更早未被改动。"""
    rows = conn.execute("SELECT DISTINCT case_id FROM evidence").fetchall()
    created = 0
    for row in rows:
        exists = conn.execute(
            "SELECT 1 FROM case_chains WHERE case_id=?", (row["case_id"],)
        ).fetchone()
        if exists:
            continue
        if _update_chain(conn, row["case_id"], backfilled=1):
            created += 1
    if created:
        print(f"[evidence] 已为 {created} 个历史案件补建证据链头（标记 backfilled）", flush=True)


def _chain_summary(conn: sqlite3.Connection, case_id: str) -> dict:
    links = _chain_links(conn, case_id)
    stored = conn.execute(
        "SELECT head, count, updated_at, sealed_at, backfilled FROM case_chains WHERE case_id=?",
        (case_id,),
    ).fetchone()
    if not links:
        return {
            "valid": True, "empty": True, "count": 0, "head": None,
            "stored_head": stored["head"] if stored else None,
            "stored_count": stored["count"] if stored else 0,
            "backfilled": bool(stored["backfilled"]) if stored else False,
            "sealed_at": stored["sealed_at"] if stored else None,
            "updated_at": stored["updated_at"] if stored else None,
            "note": "尚无证据，链为空。",
        }
    head = links[-1]["chain_hash"]
    valid = bool(stored) and stored["head"] == head and int(stored["count"]) == len(links)
    if not stored:
        note = "链头缺失，请检查数据库迁移或备份恢复。"
    elif not valid:
        note = "链头与证据记录不一致：可能存在证据删除、替换或顺序变更。"
    else:
        note = "链头与证据记录一致；深度校验会进一步重算文件字节。"
    return {
        "valid": valid,
        "empty": False,
        "count": len(links),
        "head": head,
        "stored_head": stored["head"] if stored else None,
        "stored_count": int(stored["count"]) if stored else 0,
        "backfilled": bool(stored["backfilled"]) if stored else False,
        "sealed_at": stored["sealed_at"] if stored else None,
        "updated_at": stored["updated_at"] if stored else None,
        "note": note,
    }


def can_view(user: dict, case: sqlite3.Row | dict) -> bool:
    if user["role"] in ("admin", "commander", "reviewer"):
        return True
    return case["assignee_id"] == user["id"] or case["created_by"] == user["id"]


def can_manage(user: dict) -> bool:
    return user["role"] in ("admin", "commander")


def can_upload(user: dict, case: sqlite3.Row | dict) -> bool:
    if user["role"] in ("admin", "commander"):
        return True
    return user["role"] == "officer" and can_view(user, case)


def can_review(user: dict) -> bool:
    return user["role"] in ("admin", "reviewer")


def case_permission_error(user: dict, case: sqlite3.Row | dict, action: str) -> None:
    if action == "view" and not can_view(user, case):
        raise PermissionError("无权查看该案件")
    if action == "manage" and not can_manage(user):
        raise PermissionError("只有指挥员/管理员可以执行该操作")
    if action == "upload" and not can_upload(user, case):
        raise PermissionError("只有承办民警、指挥员或管理员可以上传证据")
    if action == "review" and not can_review(user):
        raise PermissionError("只有审核员/管理员可以复核")
    if action == "archive" and not can_manage(user):
        raise PermissionError("只有指挥员/管理员可以归档")


def list_cases(user: dict, status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    where = []
    args: list[Any] = []
    if user["role"] == "officer":
        where.append("(c.assignee_id=? OR c.created_by=?)")
        args.extend([user["id"], user["id"]])
    if status:
        where.append("c.status=?")
        args.append(status)
    sql = (
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.case_id=c.id) AS task_count, "
        "(SELECT COUNT(*) FROM evidence e WHERE e.case_id=c.id) AS evidence_count, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.case_id=c.id AND t.status!='已完成') AS open_task_count "
        "FROM cases c"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.updated_at DESC LIMIT ? OFFSET ?"
    args.extend([max(1, min(limit, 500)), max(0, offset)])
    with read_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d.pop("data_snapshot", None)
        result.append(d)
    return result


def get_case(case_id: str, user: dict) -> dict:
    with read_conn() as conn:
        row = _case_row(conn, case_id)
        if not row:
            raise LookupError("案件不存在")
        if not can_view(user, row):
            raise PermissionError("无权查看该案件")
        tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM tasks WHERE case_id=? ORDER BY created_at", (case_id,)
        ).fetchall()]
        evidence = [dict(r) for r in conn.execute(
            "SELECT id, case_id, task_id, filename, sha256, size, mime, captured_at, lon, lat, note, uploaded_by, created_at "
            "FROM evidence WHERE case_id=? ORDER BY created_at", (case_id,)
        ).fetchall()]
        reviews = [dict(r) for r in conn.execute(
            "SELECT * FROM reviews WHERE case_id=? ORDER BY created_at", (case_id,)
        ).fetchall()]
        audits = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_logs WHERE object_type='case' AND object_id=? ORDER BY id",
            (case_id,),
        ).fetchall()]
        checkins = [dict(r) for r in conn.execute(
            "SELECT * FROM checkins WHERE case_id=? ORDER BY created_at DESC LIMIT 50",
            (case_id,),
        ).fetchall()]
        for item in checkins:
            try:
                item["evidence_ids"] = json.loads(item.get("evidence_ids") or "[]")
            except Exception:
                item["evidence_ids"] = []
        verification = conn.execute(
            "SELECT * FROM verifications WHERE case_id=?", (case_id,)
        ).fetchone()
        chain = _chain_summary(conn, case_id)
    result = dict(row)
    try:
        result["data_snapshot"] = json.loads(result.get("data_snapshot") or "{}")
    except Exception:
        result["data_snapshot"] = {}
    result["tasks"] = tasks
    result["evidence"] = evidence
    result["reviews"] = reviews
    result["audit"] = audits
    result["checkins"] = checkins
    result["verification"] = dict(verification) if verification else None
    result["evidence_chain"] = chain
    return result


def _refresh_case_status(conn: sqlite3.Connection, case_id: str) -> str:
    row = conn.execute("SELECT status FROM cases WHERE id=?", (case_id,)).fetchone()
    if not row or row["status"] in ("已归档", "已复核"):
        return row["status"] if row else ""
    tasks = conn.execute("SELECT status FROM tasks WHERE case_id=?", (case_id,)).fetchall()
    if tasks and all(t["status"] == "已完成" for t in tasks):
        status = "待复核"
    elif tasks:
        status = "核查中"
    else:
        status = "待派单"
    conn.execute("UPDATE cases SET status=?, updated_at=? WHERE id=?", (status, now_iso(), case_id))
    return status


def create_task(
    actor: dict, case_id: str, title: str, assignee_id: int | None, due_at: str | None, note: str | None
) -> dict:
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        if case["status"] in ("已归档", "已复核"):
            raise ValueError("案件已复核或已归档，不能新增任务")
        if actor["role"] == "officer":
            if not can_view(actor, case):
                raise PermissionError("无权为该案件派单")
            if assignee_id and int(assignee_id) != actor["id"]:
                raise PermissionError("民警只能把任务指派给自己")
            assignee = actor
        else:
            if not can_manage(actor):
                raise PermissionError("只有指挥员/管理员可以创建任务")
            assignee = user_by_id(int(assignee_id)) if assignee_id else None
            if assignee_id and not assignee:
                raise ValueError("承办人不存在或已停用")
        task_id = f"TASK-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            "INSERT INTO tasks(id, case_id, title, assignee_id, assignee_name, status, due_at, note, created_by, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, case_id, title.strip()[:160], assignee["id"] if assignee else None,
             assignee["display_name"] if assignee else None, "待接收", due_at, (note or "")[:1000],
             actor["id"], now_iso(), now_iso()),
        )
        if case["status"] == "待派单":
            conn.execute("UPDATE cases SET status='核查中', updated_at=? WHERE id=?", (now_iso(), case_id))
        audit(conn, actor, "create_task", "case", case_id, {"task_id": task_id, "title": title})
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(task)


def update_task(actor: dict, task_id: str, status: str, note: str | None = None) -> dict:
    if status not in TASK_STATUSES:
        raise ValueError("任务状态不合法")
    with write_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise LookupError("任务不存在")
        case = _case_row(conn, task["case_id"])
        if not case:
            raise LookupError("案件不存在")
        if case["status"] in ("已归档", "已复核"):
            raise ValueError("案件已复核或已归档，不能修改任务")
        if actor["role"] not in ("admin", "commander") and task["assignee_id"] != actor["id"]:
            raise PermissionError("只能更新自己承办的任务")
        current = task["status"]
        allowed = {
            "待接收": {"核查中", "已退回"},
            "核查中": {"已完成", "已退回"},
            "已退回": {"核查中"},
            "已完成": set(),
        }
        if status not in allowed.get(current, set()):
            raise ValueError(f"任务状态不能从“{current}”直接改为“{status}”")
        if status == "已完成":
            ev = conn.execute("SELECT COUNT(*) AS n FROM evidence WHERE case_id=?", (task["case_id"],)).fetchone()
            if not ev["n"]:
                raise ValueError("提交完成前必须先上传至少一份证据")
        if status == "已退回" and not (note or "").strip():
            raise ValueError("退回任务必须填写原因")
        conn.execute(
            "UPDATE tasks SET status=?, note=COALESCE(?, note), updated_at=? WHERE id=?",
            (status, (note or "").strip()[:1000] or None, now_iso(), task_id),
        )
        new_case_status = _refresh_case_status(conn, task["case_id"])
        audit(conn, actor, "update_task", "case", task["case_id"],
              {"task_id": task_id, "from": current, "to": status, "case_status": new_case_status})
        updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(updated)


def add_evidence(
    actor: dict,
    case_id: str,
    task_id: str | None,
    filename: str,
    stored_path: str,
    sha256: str,
    size: int,
    mime: str | None,
    captured_at: str | None,
    lon: float | None,
    lat: float | None,
    note: str | None,
) -> dict:
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        if case["status"] in ("已归档", "已复核"):
            raise ValueError("案件已复核或已归档，不能新增证据")
        if not can_upload(actor, case):
            raise PermissionError("无权上传证据")
        if task_id:
            task = conn.execute("SELECT * FROM tasks WHERE id=? AND case_id=?", (task_id, case_id)).fetchone()
            if not task:
                raise ValueError("任务不属于该案件")
            if task["status"] == "已完成":
                raise ValueError("任务已完成，不能再挂接证据")
        existing = conn.execute(
            "SELECT * FROM evidence WHERE case_id=? AND sha256=?", (case_id, sha256)
        ).fetchone()
        if existing:
            result = dict(existing)
            result.pop("stored_path", None)
            result["duplicate"] = True
            audit(conn, actor, "evidence_duplicate", "case", case_id,
                  {"evidence_id": result["id"], "sha256": sha256})
            return result
        ev_id = f"EV-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            "INSERT INTO evidence(id, case_id, task_id, filename, stored_path, sha256, size, mime, captured_at, lon, lat, note, uploaded_by, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev_id, case_id, task_id, filename[:200], stored_path, sha256, size, mime,
             captured_at, lon, lat, (note or "")[:1000], actor["id"], now_iso()),
        )
        if case["status"] == "待派单":
            conn.execute("UPDATE cases SET status='核查中', updated_at=? WHERE id=?", (now_iso(), case_id))
        else:
            conn.execute("UPDATE cases SET updated_at=? WHERE id=?", (now_iso(), case_id))
        _update_chain(conn, case_id)
        audit(conn, actor, "upload_evidence", "case", case_id,
              {"evidence_id": ev_id, "filename": filename, "sha256": sha256, "size": size})
        result = dict(conn.execute("SELECT * FROM evidence WHERE id=?", (ev_id,)).fetchone())
    result.pop("stored_path", None)
    return result


def arrival_token_context(token: str) -> dict:
    """现场端只读上下文：校验令牌但不消耗次数，不返回案件审计和他人信息。"""
    token = (token or "").strip()
    if len(token) < 16:
        raise ValueError("到场码格式不正确")
    digest = _token_hash(token)
    with read_conn() as conn:
        row = conn.execute("SELECT * FROM arrival_tokens WHERE token_hash=?", (digest,)).fetchone()
        if not row:
            raise PermissionError("到场码无效")
        if row["revoked"]:
            raise PermissionError("到场码已作废")
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            expires = None
        if expires and expires < datetime.now(timezone.utc):
            raise PermissionError("到场码已过期")
        case = _case_row(conn, row["case_id"])
        if not case:
            raise LookupError("案件不存在")
        tasks = [dict(r) for r in conn.execute(
            "SELECT id, title, status, due_at FROM tasks WHERE case_id=? ORDER BY created_at",
            (row["case_id"],),
        ).fetchall()]
        evidence_count = conn.execute(
            "SELECT COUNT(*) AS n FROM evidence WHERE case_id=?", (row["case_id"],)
        ).fetchone()["n"]
        checkin_count = conn.execute(
            "SELECT COUNT(*) AS n FROM checkins WHERE case_id=?", (row["case_id"],)
        ).fetchone()["n"]
    try:
        snapshot_props = json.loads(case["data_snapshot"] or "{}").get("properties", {}) or {}
    except Exception:
        snapshot_props = {}
    remaining = None if not row["max_uses"] else max(0, int(row["max_uses"]) - int(row["uses"]))
    return {
        "case": {
            "id": case["id"],
            "case_no": case["case_no"],
            "title": case["title"],
            "summary": (case["summary"] or "")[:500],
            "status": case["status"],
            "priority_score": case["priority_score"],
            "priority_label": case["priority_label"],
            "current_danger": case["current_danger"],
            "current_danger_label": case["current_danger_label"],
            "lon": case["lon"],
            "lat": case["lat"],
            "due_at": case["due_at"],
            "nearest_police": snapshot_props.get("nearest_police"),
            "dist_police_km": snapshot_props.get("dist_police_km"),
            "dominant_hazard": snapshot_props.get("dominant_hazard_name") or snapshot_props.get("dominant_class") or snapshot_props.get("dominant_hazard"),
            "advice": snapshot_props.get("advice") or snapshot_props.get("recommended_action"),
        },
        "tasks": tasks,
        "evidence_count": int(evidence_count),
        "checkin_count": int(checkin_count),
        "contact": row["created_by_name"],
        "actor_id": int(row["created_by"]),
        "expires_at": row["expires_at"],
        "remaining_uses": remaining,
        "server_time": now_iso(),
        "can_checkin": case["status"] not in ("已复核", "已归档"),
        "disclaimer": "到场码只证明持码者在此时到达，不自动证明身份或执法资格；请同时核验证件。",
    }


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _controlled_evidence_path(stored_path: str) -> Path:
    path = Path(stored_path)
    if not path.is_absolute():
        path = config.ROOT / path
    path = path.resolve()
    root = EVIDENCE_DIR.resolve()
    if path != root and root not in path.parents:
        raise PermissionError("证据路径不在受控目录内")
    return path


def issue_arrival_token(
    actor: dict,
    case_id: str,
    hours: int = 24,
    max_uses: int = 0,
    note: str | None = None,
) -> dict:
    """生成案件级到场码。数据库只存哈希，明文只在响应里出现一次。"""
    hours = max(1, min(int(hours), 24 * 30))
    max_uses = max(0, min(int(max_uses), 10_000))
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        case_permission_error(actor, case, "生成到场码")
        if case["status"] == "已归档":
            raise ValueError("案件已归档，不能生成到场码")
        token = "ARR-" + secrets.token_urlsafe(24)
        hint = token[:8] + "…" + token[-4:]
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=hours)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO arrival_tokens(token_hash, token_hint, case_id, created_by, created_by_name, "
            "created_at, expires_at, max_uses, uses, revoked, note) VALUES(?,?,?,?,?,?,?,?,0,0,?)",
            (_token_hash(token), hint, case_id, actor["id"], actor["display_name"],
             now.isoformat(timespec="seconds"), expires, max_uses, (note or "")[:300]),
        )
        audit(conn, actor, "issue_arrival_token", "case", case_id,
              {"hint": hint, "expires_at": expires, "max_uses": max_uses})
    return {
        "case_id": case_id,
        "token": token,
        "token_hint": hint,
        "expires_at": expires,
        "max_uses": max_uses,
        "note": "只显示一次；请通过安全渠道发给现场民警。",
    }


def list_arrival_tokens(user: dict, case_id: str) -> list[dict]:
    with read_conn() as conn:
        case = _case_row(conn, case_id)
        if not case or not can_view(user, case):
            raise PermissionError("无权查看该案件")
        rows = conn.execute(
            "SELECT rowid AS id, token_hint, created_by_name, created_at, expires_at, "
            "max_uses, uses, revoked, note FROM arrival_tokens WHERE case_id=? ORDER BY created_at DESC",
            (case_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_arrival_token(actor: dict, case_id: str, token_row_id: int) -> dict:
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        case_permission_error(actor, case, "作废到场码")
        row = conn.execute(
            "SELECT rowid AS id, token_hint FROM arrival_tokens WHERE rowid=? AND case_id=?",
            (int(token_row_id), case_id),
        ).fetchone()
        if not row:
            raise LookupError("到场码不存在")
        conn.execute("UPDATE arrival_tokens SET revoked=1 WHERE rowid=?", (row["id"],))
        audit(conn, actor, "revoke_arrival_token", "case", case_id, {"hint": row["token_hint"]})
    return {"case_id": case_id, "revoked": True, "token_id": int(token_row_id)}


def redeem_arrival_token(
    token: str,
    actor_name: str,
    lon: float | None = None,
    lat: float | None = None,
    accuracy: float | None = None,
    task_id: str | None = None,
    note: str | None = None,
    device: str | None = None,
    captured_at: str | None = None,
) -> dict:
    """现场扫码签到。无需登录，凭案件级短期令牌写入一条不可否认的到访记录。"""
    token = (token or "").strip()
    actor_name = (actor_name or "").strip()[:60]
    if len(token) < 16:
        raise ValueError("到场码格式不正确")
    if not actor_name:
        raise ValueError("请填写到场人姓名")
    token_digest = _token_hash(token)
    with write_conn() as conn:
        row = conn.execute(
            "SELECT rowid AS id, * FROM arrival_tokens WHERE token_hash=?", (token_digest,)
        ).fetchone()
        if not row:
            raise PermissionError("到场码无效")
        if row["revoked"]:
            raise PermissionError("到场码已作废")
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            expires = None
        if expires and expires < datetime.now(timezone.utc):
            raise PermissionError("到场码已过期")
        case = _case_row(conn, row["case_id"])
        if not case:
            raise LookupError("案件不存在")
        if case["status"] in ("已复核", "已归档"):
            raise ValueError("案件已复核或归档，不能再签到")
        creator_row = conn.execute("SELECT * FROM users WHERE id=?", (row["created_by"],)).fetchone()
        if not creator_row or not creator_row["active"]:
            raise PermissionError("案件负责人账号已停用")
        creator = public_user(creator_row)
        if task_id:
            task = conn.execute(
                "SELECT * FROM tasks WHERE id=? AND case_id=?", (task_id, row["case_id"])
            ).fetchone()
            if not task:
                raise ValueError("任务不属于该案件")
            if task["status"] == "已完成":
                raise ValueError("任务已完成，不能再签到")
        cursor = conn.execute(
            "UPDATE arrival_tokens SET uses=uses+1 WHERE token_hash=? AND revoked=0 "
            "AND (max_uses=0 OR uses<max_uses)",
            (token_digest,),
        )
        if cursor.rowcount != 1:
            raise PermissionError("到场码使用次数已用尽")
        checkin_id = f"CHK-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            "INSERT INTO checkins(id, case_id, token_hash, task_id, actor_name, lon, lat, accuracy, "
            "captured_at, note, device, source, evidence_ids, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (checkin_id, row["case_id"], token_digest, task_id, actor_name, lon, lat, accuracy,
             captured_at or now_iso(), (note or "")[:500], (device or "")[:200],
             "arrival_token", "[]", now_iso()),
        )
        if case["status"] == "待派单":
            conn.execute("UPDATE cases SET status='核查中', updated_at=? WHERE id=?",
                         (now_iso(), row["case_id"]))
        else:
            conn.execute("UPDATE cases SET updated_at=? WHERE id=?", (now_iso(), row["case_id"]))
        audit(conn, creator, "checkin", "case", row["case_id"],
              {"checkin_id": checkin_id, "actor_name": actor_name, "lon": lon, "lat": lat,
               "task_id": task_id, "source": "arrival_token"})
        remaining = None if not row["max_uses"] else max(0, int(row["max_uses"]) - int(row["uses"]) - 1)
    return {
        "checkin_id": checkin_id,
        "case_id": row["case_id"],
        "case_no": case["case_no"],
        "case_title": case["title"],
        "actor_name": actor_name,
        "captured_at": captured_at or now_iso(),
        "remaining_uses": remaining,
        "actor_id": creator["id"],
        "note": "签到已写入审计；它证明“有人在此时间到达”，不自动证明身份或执法资格。",
    }


def get_checkin(checkin_id: str, case_id: str) -> dict | None:
    with read_conn() as conn:
        row = conn.execute(
            "SELECT * FROM checkins WHERE id=? AND case_id=?", (checkin_id, case_id)
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["evidence_ids"] = json.loads(item.get("evidence_ids") or "[]")
    except Exception:
        item["evidence_ids"] = []
    item.pop("token_hash", None)
    return item


def link_checkin_evidence(checkin_id: str, case_id: str, evidence_id: str) -> None:
    with write_conn() as conn:
        row = conn.execute(
            "SELECT evidence_ids FROM checkins WHERE id=? AND case_id=?", (checkin_id, case_id)
        ).fetchone()
        if not row:
            raise LookupError("签到记录不存在")
        try:
            ids = json.loads(row["evidence_ids"] or "[]")
        except Exception:
            ids = []
        if evidence_id not in ids:
            ids.append(evidence_id)
        conn.execute("UPDATE checkins SET evidence_ids=? WHERE id=?",
                     (json.dumps(ids, ensure_ascii=False), checkin_id))


def record_verification(
    actor: dict,
    case_id: str,
    verdict: str,
    note: str | None = None,
    evidence_id: str | None = None,
) -> dict:
    """记录现场核查结论。这是评测标签的唯一入口，必须由审核员/管理员填写。"""
    if verdict not in ("confirmed", "excluded", "inconclusive"):
        raise ValueError("核查结论只能是 confirmed、excluded 或 inconclusive")
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        if not can_review(actor):
            raise PermissionError("只有审核员或管理员可以录入核查结论")
        if case["status"] == "已归档":
            raise ValueError("案件已归档，不能修改核查结论")
        if evidence_id:
            ev = conn.execute(
                "SELECT id FROM evidence WHERE id=? AND case_id=?", (evidence_id, case_id)
            ).fetchone()
            if not ev:
                raise ValueError("支撑证据不属于该案件")
        existing = conn.execute("SELECT * FROM verifications WHERE case_id=?", (case_id,)).fetchone()
        vid = existing["id"] if existing else f"VER-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            """INSERT INTO verifications(id, case_id, verdict, note, evidence_id, verified_by,
                                         verified_by_name, verified_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(case_id) DO UPDATE SET
                 verdict=excluded.verdict, note=excluded.note, evidence_id=excluded.evidence_id,
                 verified_by=excluded.verified_by, verified_by_name=excluded.verified_by_name,
                 verified_at=excluded.verified_at""",
            (vid, case_id, verdict, (note or "")[:1000], evidence_id, actor["id"],
             actor["display_name"], now_iso()),
        )
        conn.execute("UPDATE cases SET updated_at=? WHERE id=?", (now_iso(), case_id))
        audit(conn, actor, "verification", "case", case_id,
              {"verdict": verdict, "evidence_id": evidence_id})
    return get_case(case_id, actor)


def list_verifications(user: dict, limit: int = 500) -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT v.*, c.case_no, c.title FROM verifications v "
            "JOIN cases c ON c.id=v.case_id ORDER BY v.verified_at DESC LIMIT ?",
            (max(1, min(int(limit), 2000)),),
        ).fetchall()
    return [dict(r) for r in rows]


def verification_stats(user: dict | None = None) -> dict:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT verdict, COUNT(*) AS n FROM verifications GROUP BY verdict"
        ).fetchall()
        total_cases = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
    counts = {r["verdict"]: int(r["n"]) for r in rows}
    confirmed = counts.get("confirmed", 0)
    excluded = counts.get("excluded", 0)
    inconclusive = counts.get("inconclusive", 0)
    labelled = confirmed + excluded
    return {
        "total_cases": int(total_cases),
        "confirmed": confirmed,
        "excluded": excluded,
        "inconclusive": inconclusive,
        "labelled": labelled,
        "confirmed_rate": round(confirmed / labelled, 4) if labelled else None,
        "note": "只统计已明确 confirmed/excluded 的案件；inconclusive 不计入分母。样本不足时不解释为模型精度。",
    }


def evidence_chain_report(case_id: str, user: dict, deep: bool = False) -> dict:
    with read_conn() as conn:
        case = _case_row(conn, case_id)
        if not case or not can_view(user, case):
            raise PermissionError("无权查看该案件")
        links = _chain_links(conn, case_id)
        summary = _chain_summary(conn, case_id)
        rows = {
            r["id"]: r for r in conn.execute(
                "SELECT id, filename, stored_path, sha256 FROM evidence WHERE case_id=?",
                (case_id,),
            ).fetchall()
        }
    items = []
    for link in links:
        item = dict(link)
        row = rows.get(item["evidence_id"])
        if not row:
            item["file_ok"] = False
            item["file_error"] = "证据元数据缺失"
            item["actual_sha256"] = None
            items.append(item)
            continue
        if not deep:
            item["file_ok"] = None
            item["actual_sha256"] = None
            item["file_error"] = None
            items.append(item)
            continue
        try:
            path = _controlled_evidence_path(row["stored_path"])
            if not path.is_file():
                raise LookupError("证据文件缺失")
            with path.open("rb") as fh:
                actual = hashlib.file_digest(fh, "sha256").hexdigest()
            item["actual_sha256"] = actual
            item["file_ok"] = hmac.compare_digest(actual, row["sha256"])
            item["file_error"] = None if item["file_ok"] else "文件字节与登记哈希不一致"
        except (LookupError, PermissionError, OSError) as exc:
            item["actual_sha256"] = None
            item["file_ok"] = False
            item["file_error"] = str(exc)
        items.append(item)
    valid = bool(summary.get("valid")) and all(i.get("file_ok") is not False for i in items)
    with write_conn() as conn:
        audit(conn, user, "verify_evidence_chain", "case", case_id,
              {"deep": deep, "valid": valid, "count": len(items)})
    return {
        "case_id": case_id,
        "valid": valid,
        "deep": deep,
        "chain": summary,
        "items": items,
        "checked_at": now_iso(),
        "note": "链头校验能发现删除/替换/重排；深度校验会重算文件 SHA-256。它不是法定电子签章。",
    }


def evidence_for_download(evidence_id: str, user: dict) -> tuple[dict, Path]:
    with read_conn() as conn:
        ev = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if not ev:
            raise LookupError("证据不存在")
        case = _case_row(conn, ev["case_id"])
        if not case or not can_view(user, case):
            raise PermissionError("无权下载该证据")
    path = Path(ev["stored_path"])
    if not path.is_absolute():
        path = (config.ROOT / path).resolve()
    if not path.is_file():
        raise LookupError("证据文件缺失")
    if EVIDENCE_DIR.resolve() not in path.parents and path != EVIDENCE_DIR.resolve():
        raise PermissionError("证据路径不在受控目录内")
    with write_conn() as conn:
        audit(conn, user, "download_evidence", "case", ev["case_id"], {"evidence_id": evidence_id})
    return dict(ev), path


def add_review(actor: dict, case_id: str, decision: str, comment: str | None) -> dict:
    if decision not in ("通过", "退回", "补充核查"):
        raise ValueError("复核结论不合法")
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        if case["status"] in ("已归档", "已复核"):
            raise ValueError("案件已复核或已归档，不能重复复核")
        if case["status"] != "待复核":
            raise ValueError("只有“待复核”状态可以提交复核")
        if decision != "通过" and not (comment or "").strip():
            raise ValueError("退回/补充核查必须填写意见")
        review_id = f"REV-{uuid.uuid4().hex[:10].upper()}"
        conn.execute(
            "INSERT INTO reviews(id, case_id, reviewer_id, reviewer_name, decision, comment, created_at) VALUES(?,?,?,?,?,?,?)",
            (review_id, case_id, actor["id"], actor["display_name"], decision,
             (comment or "")[:2000], now_iso()),
        )
        new_status = "已复核" if decision == "通过" else "核查中"
        conn.execute("UPDATE cases SET status=?, updated_at=? WHERE id=?", (new_status, now_iso(), case_id))
        audit(conn, actor, "review_case", "case", case_id,
              {"review_id": review_id, "decision": decision, "comment": comment or ""})
        row = conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
    return dict(row)


def archive_case(actor: dict, case_id: str) -> dict:
    if actor["role"] not in ("admin", "commander"):
        raise PermissionError("只有指挥员/管理员可以归档案件")
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        if case["status"] != "已复核":
            raise ValueError("只有“已复核”案件可以归档")
        conn.execute(
            "UPDATE cases SET status='已归档', closed_at=?, updated_at=? WHERE id=?",
            (now_iso(), now_iso(), case_id),
        )
        audit(conn, actor, "archive_case", "case", case_id, {"case_no": case["case_no"]})
    return get_case(case_id, actor)


def case_stats(user: dict) -> dict:
    where = ""
    args: list[Any] = []
    if user["role"] == "officer":
        where = " WHERE (assignee_id=? OR created_by=?)"
        args = [user["id"], user["id"]]
    with read_conn() as conn:
        rows = conn.execute(f"SELECT status, COUNT(*) AS n FROM cases{where} GROUP BY status", args).fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        total = sum(by_status.values())
        ev = conn.execute(
            "SELECT COUNT(*) AS n FROM evidence e JOIN cases c ON c.id=e.case_id" + where.replace("assignee_id", "c.assignee_id").replace("created_by", "c.created_by"),
            args,
        ).fetchone()["n"] if where else conn.execute("SELECT COUNT(*) AS n FROM evidence").fetchone()["n"]
        overdue = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks t JOIN cases c ON c.id=t.case_id"
            + (" WHERE (c.assignee_id=? OR c.created_by=?) AND " if where else " WHERE ")
            + "t.due_at IS NOT NULL AND t.due_at < ? AND t.status!='已完成'",
            (args + [now_iso()]) if where else [now_iso()],
        ).fetchone()["n"]
    return {"total": total, "by_status": {s: by_status.get(s, 0) for s in CASE_STATUSES},
            "evidence": ev, "overdue_tasks": overdue}


def _referral_row(conn: sqlite3.Connection, referral_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT r.*, c.case_no, c.title AS case_title, c.status AS case_status, c.lon, c.lat "
        "FROM case_referrals r JOIN cases c ON c.id=r.case_id WHERE r.id=?",
        (referral_id,),
    ).fetchone()


def get_referral(actor: dict, referral_id: str) -> dict:
    with read_conn() as conn:
        row = _referral_row(conn, referral_id)
    if not row:
        raise LookupError("移送记录不存在")
    case = {"assignee_id": None, "created_by": None}
    with read_conn() as conn:
        case_row = conn.execute("SELECT assignee_id, created_by FROM cases WHERE id=?", (row["case_id"],)).fetchone()
        if case_row:
            case = dict(case_row)
    if not can_view(actor, case):
        raise PermissionError("无权查看该移送记录")
    return dict(row)


def create_referral(
    actor: dict,
    case_id: str,
    department: str,
    contact: str | None = None,
    note: str | None = None,
    due_at: str | None = None,
) -> dict:
    """把已确认问题的案件移送给协同部门，保留原案件和证据链。"""
    if not can_manage(actor):
        raise PermissionError("只有指挥员/管理员可以发起跨部门移送")
    department = (department or "").strip()
    if len(department) < 2 or len(department) > 60:
        raise ValueError("协同部门名称需为 2–60 个字符")
    contact = (contact or "").strip()[:120] or None
    note = (note or "").strip()[:1000] or None
    due_at = (due_at or "").strip()[:40] or None
    with write_conn() as conn:
        case = _case_row(conn, case_id)
        if not case:
            raise LookupError("案件不存在")
        if not can_view(actor, case):
            raise PermissionError("无权操作该案件")
        verification = conn.execute(
            "SELECT verdict FROM verifications WHERE case_id=?", (case_id,)
        ).fetchone()
        confirmed = bool(verification and verification["verdict"] == "confirmed")
        if not confirmed and case["status"] not in ("已复核", "已归档"):
            raise ValueError("只有已确认问题（confirmed）并完成复核的案件才能移送")
        referral_id = "REF-" + uuid.uuid4().hex[:10].upper()
        created = now_iso()
        conn.execute(
            "INSERT INTO case_referrals(id, case_id, department, contact, note, status, "
            "created_by, created_by_name, created_at, updated_at, due_at, feedback) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                referral_id,
                case_id,
                department,
                contact,
                note,
                "待接收",
                actor["id"],
                actor["display_name"],
                created,
                created,
                due_at,
            ),
        )
        audit(
            conn,
            actor,
            "create_referral",
            "case",
            case_id,
            {"referral_id": referral_id, "department": department, "due_at": due_at},
        )
        row = _referral_row(conn, referral_id)
    return dict(row)


def list_referrals(
    user: dict,
    case_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    where: list[str] = []
    args: list[Any] = []
    if user["role"] == "officer":
        where.append("(c.assignee_id=? OR c.created_by=?)")
        args.extend([user["id"], user["id"]])
    if case_id:
        where.append("r.case_id=?")
        args.append(case_id)
    if status:
        where.append("r.status=?")
        args.append(status)
    sql = (
        "SELECT r.*, c.case_no, c.title AS case_title, c.status AS case_status, c.lon, c.lat "
        "FROM case_referrals r JOIN cases c ON c.id=r.case_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY r.created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    with read_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def update_referral(
    actor: dict,
    referral_id: str,
    status: str,
    feedback: str | None = None,
) -> dict:
    if not can_manage(actor):
        raise PermissionError("只有指挥员/管理员可以更新移送状态")
    if status not in REFERRAL_STATUSES:
        raise ValueError("移送状态只能是：" + "、".join(REFERRAL_STATUSES))
    feedback = (feedback or "").strip()[:2000] or None
    if status in ("已反馈", "已退回") and not feedback:
        raise ValueError("已反馈/已退回必须填写反馈或退回原因")
    with write_conn() as conn:
        row = _referral_row(conn, referral_id)
        if not row:
            raise LookupError("移送记录不存在")
        conn.execute(
            "UPDATE case_referrals SET status=?, feedback=?, updated_at=? WHERE id=?",
            (status, feedback, now_iso(), referral_id),
        )
        audit(
            conn,
            actor,
            "update_referral",
            "case",
            row["case_id"],
            {"referral_id": referral_id, "status": status},
        )
        updated = _referral_row(conn, referral_id)
    return dict(updated)


def get_audit(user: dict, limit: int = 100, object_type: str | None = None, object_id: str | None = None) -> list[dict]:
    where = []
    args: list[Any] = []
    if object_type:
        where.append("object_type=?")
        args.append(object_type)
    if object_id:
        where.append("object_id=?")
        args.append(object_id)
    sql = "SELECT * FROM audit_logs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    with read_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
