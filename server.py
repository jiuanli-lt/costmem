#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
造价记忆助手 —— 本地/云端通用个人数据记录后端
纯 Python 标准库实现（仅云端 Postgres 时依赖 psycopg2）。
- 本地：SQLite（data/assistant.db），零依赖
- 云端：设置 DATABASE_URL 自动切到 PostgreSQL（如 Render 免费 Postgres）
- 公网访问需设置 ADMIN_USER / ADMIN_PASS / SECRET 环境变量
"""
import http.server
import socketserver
import sqlite3
import json
import re
import os
import socket
import hashlib
import hmac
import secrets
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs, quote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "assistant.db")
PORT = int(os.environ.get("PORT", "8000"))
VERSION = '1.2.1'  # 1.2.0 + 部署命门修复(health公开/Secure Cookie/RFC5987导出名) + 一键导出备份

# ---- 鉴权配置（云端务必通过环境变量设置）----
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin123')
SECRET = os.environ.get('SECRET') or secrets.token_hex(16)
if not os.environ.get('SECRET'):
    print('[警告] 未设置 SECRET，使用临时随机值；云端部署请设置环境变量 SECRET')
if not os.environ.get('ADMIN_PASS'):
    print('[警告] 未设置 ADMIN_PASS，使用默认值 admin123；云端务必修改！')

# ---- 数据库切换 ----
DATABASE_URL = os.environ.get('DATABASE_URL')
USE_PG = bool(DATABASE_URL)
PH = '%s' if USE_PG else '?'  # 占位符：pg 用 %s，sqlite 用 ?


def get_conn():
    if USE_PG:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10,
                                cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""CREATE TABLE IF NOT EXISTS records (
            id SERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            category TEXT DEFAULT '备忘',
            project TEXT,
            amount REAL,
            unit TEXT,
            cost_type TEXT,
            source TEXT DEFAULT '文字',
            tags TEXT,
            created_at TEXT,
            updated_at TEXT
        )""")
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            category TEXT DEFAULT '备忘',
            project TEXT,
            amount REAL,
            unit TEXT,
            cost_type TEXT,
            source TEXT DEFAULT '文字',
            tags TEXT,
            created_at TEXT,
            updated_at TEXT
        )""")
    conn.commit()
    # 兼容旧库：缺 cost_type 列则补
    try:
        if USE_PG:
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='cost_type'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN cost_type TEXT")
        else:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(records)")]
            if 'cost_type' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN cost_type TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()


def row_to_dict(r):
    return {
        'id': r['id'],
        'content': r['content'],
        'category': r['category'],
        'project': r['project'],
        'amount': r['amount'],
        'unit': r['unit'],
        'cost_type': r['cost_type'],
        'source': r['source'],
        'tags': r['tags'],
        'created_at': r['created_at'],
        'updated_at': r['updated_at'],
    }


# ------------------------------------------------------------------ 解析逻辑
UNITS = ['立方米', '立方', 'm³', 'm3', '平方米', '平米', 'm²', 'm2', '平方',
         '吨', 't', '公斤', 'kg', '工日', '延米', '米', 'm', '公里', 'km',
         '个', '根', '条', '项', '次', '天', '日', '月', '年', '小时', '台班', '班次']
_UNIT_ALT = '|'.join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
_AMOUNT_RE = re.compile(
    r'(?<![A-Za-z0-9])(?P<num>\d+(?:\.\d+)?)\s*'
    r'(?P<cur>元|块|￥|人民币)?\s*'
    r'(?P<per>/|每|per)?\s*'
    r'(?P<unit>' + _UNIT_ALT + r')?'
)

PRICE_KW = ['单价', '人工', '人工费', '价格', '报价', '多少钱', '费用', '成本',
            '材料', '机械', '管理费', '规费', '税金', '信息价', '市场价', '劳务费',
            '工资', '工价', '台班', '租赁']
# 类型指示词（显式声明，优先级最高）。具体条目名走 cost_items.json 数据包。
COST_KW = {
    '人工': ['人工', '人工费', '工资', '劳务', '工价', '工日'],
    '材料': ['材料'],
    '机械': ['机械', '台班', '租赁', '设备'],
}

# 建筑业人材机常备数据包（可编辑 cost_items.json 自行扩充）
COST_ITEMS_PATH = os.path.join(BASE_DIR, 'cost_items.json')
COST_ITEMS = {'人工': [], '材料': [], '机械': []}
try:
    with open(COST_ITEMS_PATH, 'r', encoding='utf-8') as _f:
        _data = json.load(_f)
    for _t in ('人工', '材料', '机械'):
        COST_ITEMS[_t] = list(_data.get(_t, []))
except Exception:
    pass  # 数据包缺失时退化为仅关键词识别
COST_ITEMS_COUNT = sum(len(v) for v in COST_ITEMS.values())
PROJECT_RES = [
    re.compile(r'项目[：:是为]?\s*([^\s，。,.;；、]+)'),
    re.compile(r'(?:在|于)\s*([^\s，。,.;；、]+?)\s*(?:项目|工地|工程)'),
    re.compile(r'(?:工地|工程)[：:是为]?\s*([^\s，。,.;；、]+)'),
]


def detect_category(text):
    if any(k in text for k in PRICE_KW):
        return '单价费用'
    if any(k in text for k in ['提醒', '记得', '别忘', '别漏', '待办', '跟进', '对账',
                               '约', '别忘了', '要记', '回访', '催', '落实', '确认']):
        return '事项'
    return '备忘'


def detect_cost_type(text):
    # 1) 显式类型词（人工/材料/机械/台班/租赁…）优先级最高
    for t, kws in COST_KW.items():
        if any(k in text for k in kws):
            return t
    # 2) 建筑业数据包：人工优先于材料（处理"混凝土工"等前缀冲突），机械最后
    if any(w in text for w in COST_ITEMS['人工']):
        return '人工'
    if any(w in text for w in COST_ITEMS['材料']):
        return '材料'
    if any(w in text for w in COST_ITEMS['机械']):
        return '机械'
    return None


def parse_content(content):
    amount = None
    unit = None
    matches = list(_AMOUNT_RE.finditer(content))
    if not matches:
        return amount, unit
    # 优先取"带货币(元/块/￥)"的，那才是价格；型号里的数字(如 5*6)不带元，会被忽略。
    # 若都没有货币，取最后一个数字（如"杂工220"）。
    best = None
    for m in matches:
        if m.group('cur'):
            best = m
    if best is None:
        best = matches[-1]
    try:
        amount = float(best.group('num'))
    except (TypeError, ValueError):
        return None, None
    cur = best.group('cur')
    per = best.group('per')
    u = best.group('unit')
    parts = []
    if cur:
        parts.append(cur)
    if per:
        parts.append('/')
    if u:
        parts.append(u)
    unit = ''.join(parts) if parts else None
    return amount, unit


def find_project(text):
    for rx in PROJECT_RES:
        m = rx.search(text)
        if m:
            return m.group(1).strip()
    return None


def build_record(content, project=None, category=None, cost_type=None, source='文字'):
    content = (content or '').strip()
    amount, unit = parse_content(content)
    if category is None or category not in ('单价费用', '事项', '备忘'):
        category = detect_category(content)
    if not project:
        project = find_project(content)
    if not cost_type or cost_type not in ('人工', '材料', '机械'):
        cost_type = detect_cost_type(content)
    return {
        'content': content,
        'category': category,
        'project': project,
        'amount': amount,
        'unit': unit,
        'cost_type': cost_type,
        'source': source,
        'tags': project or '',
    }


# ------------------------------------------------------------------ 鉴权
def make_token(user):
    exp = int(time.time()) + 60 * 60 * 24 * 30
    body = f"{user}.{exp}"
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def check_token(tok):
    if not tok:
        return None
    try:
        user, exp, sig = tok.split('.')
        exp = int(exp)
        body = f"{user}.{exp}"
        expect = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expect, sig):
            return None
        if int(time.time()) > exp:
            return None
        return user
    except Exception:
        return None


# ------------------------------------------------------------------ API 服务
class Handler(http.server.BaseHTTPRequestHandler):
    def _cookies(self):
        out = {}
        c = self.headers.get('Cookie')
        if c:
            for part in c.split(';'):
                if '=' in part:
                    k, v = part.strip().split('=', 1)
                    out[k] = v
        return out

    def _auth_user(self):
        return check_token(self._cookies().get('sid'))

    def _set_cookie(self, name, value, max_age=None, secure=False):
        s = f'{name}={value}; Path=/; HttpOnly; SameSite=Lax'
        if max_age:
            s += f'; Max-Age={max_age}'
        if secure:
            s += '; Secure'
        self.send_header('Set-Cookie', s)

    def _send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text, status=200, ctype='text/plain; charset=utf-8'):
        data = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return {}

    def _serve_static(self, path):
        if path in ('/', ''):
            path = '/index.html'
        safe = os.path.normpath(path).lstrip('/\\')
        full = os.path.join(PUBLIC_DIR, safe)
        if not full.startswith(PUBLIC_DIR) or not os.path.isfile(full):
            self._send_text('Not Found', status=404)
            return
        ctype = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.json': 'application/manifest+json; charset=utf-8',
            '.svg': 'image/svg+xml',
        }.get(os.path.splitext(full)[1], 'application/octet-stream')
        with open(full, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        p, qs = parsed.path, parse_qs(parsed.query)
        if p.startswith('/api/'):
            # 健康检查必须公开：云平台的存活探针不带登录 Cookie，返回 401 会被判为不健康
            if p == '/api/health':
                u = self._auth_user()
                return self._send_json({'ok': True, 'version': VERSION,
                                       'authed': bool(u), 'user': u or ''})
            if p == '/api/version':
                return self._send_json({'version': VERSION})
            if not self._auth_user():
                return self._send_json({'error': '未登录'}, status=401)
            if p == '/api/export':
                return self._api_export()
            return self._api_get(p, qs)
        return self._serve_static(p)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/login':
            return self._api_login()
        if parsed.path == '/api/logout':
            self._set_cookie('sid', '', max_age=0)
            return self._send_json({'ok': True})
        if not self._auth_user():
            return self._send_json({'error': '未登录'}, status=401)
        if parsed.path == '/api/parse':
            return self._api_parse()
        if parsed.path == '/api/records':
            return self._api_create()
        self._send_json({'error': 'not found'}, status=404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        m = re.match(r'/api/records/(\d+)', parsed.path)
        if not self._auth_user():
            return self._send_json({'error': '未登录'}, status=401)
        if m:
            return self._api_update(int(m.group(1)))
        self._send_json({'error': 'not found'}, status=404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        m = re.match(r'/api/records/(\d+)', parsed.path)
        if not self._auth_user():
            return self._send_json({'error': '未登录'}, status=401)
        if m:
            return self._api_delete(int(m.group(1)))
        self._send_json({'error': 'not found'}, status=401)

    # ---- 鉴权接口
    def _api_login(self):
        body = self._read_body()
        u = body.get('user', '')
        p = body.get('pass', '')
        ok = (u == ADMIN_USER and
              hashlib.sha256(p.encode()).hexdigest() ==
              hashlib.sha256(ADMIN_PASS.encode()).hexdigest())
        if not ok:
            return self._send_json({'error': '用户名或密码错误'}, status=401)
        tok = make_token(u)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        # 云端走 HTTPS 反向代理时，用 X-Forwarded-Proto 判断，确保 Cookie 带 Secure
        secure = self.headers.get('X-Forwarded-Proto', '').lower() == 'https' or \
                 self.client_address[0] not in ('127.0.0.1', '::1')
        self._set_cookie('sid', tok, max_age=60 * 60 * 24 * 30, secure=secure)
        self.end_headers()
        self.wfile.write(json.dumps({'ok': True}).encode('utf-8'))

    # ---- 业务接口
    def _api_get(self, p, qs):
        if p == '/api/records':
            q = (qs.get('q', [''])[0] or '').strip()
            project = (qs.get('project', [''])[0] or '').strip()
            category = (qs.get('category', [''])[0] or '').strip()
            cost_type = (qs.get('cost_type', [''])[0] or '').strip()
            conn = get_conn()
            cur = conn.cursor()
            sql = "SELECT * FROM records WHERE 1=1"
            args = []
            if q:
                like = f'%{q}%'
                amt_col = "CAST(amount AS TEXT)" if not USE_PG else "amount::text"
                sql += (f" AND (content LIKE {PH} OR project LIKE {PH} OR tags LIKE {PH}"
                        f" OR unit LIKE {PH} OR cost_type LIKE {PH} OR category LIKE {PH}"
                        f" OR {amt_col} LIKE {PH})")
                args += [like, like, like, like, like, like, like]
            if project:
                sql += f" AND project = {PH}"
                args.append(project)
            if category:
                sql += f" AND category = {PH}"
                args.append(category)
            if cost_type in ('人工', '材料', '机械'):
                sql += f" AND cost_type = {PH}"
                args.append(cost_type)
            sql += " ORDER BY id DESC LIMIT 500"
            cur.execute(sql, args)
            rows = [row_to_dict(r) for r in cur.fetchall()]
            conn.close()
            return self._send_json(rows)

        if p == '/api/projects':
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT project FROM records WHERE project IS NOT NULL AND project != '' ORDER BY project")
            rows = [r['project'] for r in cur.fetchall()]
            conn.close()
            return self._send_json(rows)

        if p == '/api/stats':
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) c FROM records")
            total = cur.fetchone()['c']
            cats = {}
            cur.execute("SELECT category, COUNT(*) c FROM records GROUP BY category")
            for r in cur.fetchall():
                cats[r['category']] = r['c']
            conn.close()
            return self._send_json({'total': total, 'by_category': cats})

        self._send_json({'error': 'not found'}, status=404)

    def _api_export(self):
        """导出全部记录为 JSON 文件下载（隐私数据，需登录）"""
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM records ORDER BY id DESC")
        rows = [row_to_dict(r) for r in cur.fetchall()]
        conn.close()
        body = {
            'version': VERSION,
            'exported_at': now_iso(),
            'count': len(rows),
            'records': rows,
        }
        data = json.dumps(body, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        # HTTP 头只能 latin-1，中文文件名用 RFC5987 编码（浏览器会显示成中文）
        self.send_header('Content-Disposition',
                         "attachment; filename=\"costmem-backup.json\"; "
                         "filename*=UTF-8''" + quote('造价小本本备份.json'))
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api_parse(self):
        """只识别不入库，供前端实时预览"我帮你认成什么" """
        body = self._read_body()
        rec = build_record(
            body.get('content', ''),
            project=body.get('project') or None,
            category=body.get('category') or None,
            cost_type=body.get('cost_type') or None,
            source=body.get('source') or '文字',
        )
        return self._send_json(rec)

    def _api_create(self):
        body = self._read_body()
        rec = build_record(
            body.get('content', ''),
            project=body.get('project') or None,
            category=body.get('category') or None,
            cost_type=body.get('cost_type') or None,
            source=body.get('source') or '文字',
        )
        if not rec['content']:
            return self._send_json({'error': '内容不能为空'}, status=400)
        ts = now_iso()
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO records (content, category, project, amount, unit, cost_type, source, tags, created_at, updated_at)
               VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})""",
            (rec['content'], rec['category'], rec['project'], rec['amount'],
             rec['unit'], rec['cost_type'], rec['source'], rec['tags'], ts, ts),
        )
        if USE_PG:
            rid = cur.fetchone()['id']
        else:
            rid = cur.lastrowid
        cur.execute(f"SELECT * FROM records WHERE id={PH}", (rid,))
        row = row_to_dict(cur.fetchone())
        conn.commit()
        conn.close()
        return self._send_json(row, status=201)

    def _api_update(self, rid):
        body = self._read_body()
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM records WHERE id={PH}", (rid,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return self._send_json({'error': '不存在'}, status=404)
        content = body.get('content', row['content'])
        project = body.get('project', row['project'])
        category = body.get('category', row['category'])
        cost_type = body.get('cost_type', row['cost_type'])
        rec = build_record(content, project=project, category=category,
                           cost_type=cost_type, source=row['source'])
        ts = now_iso()
        cur.execute(
            f"""UPDATE records SET content={PH}, category={PH}, project={PH}, amount={PH},
               unit={PH}, cost_type={PH}, tags={PH}, updated_at={PH} WHERE id={PH}""",
            (rec['content'], rec['category'], rec['project'], rec['amount'],
             rec['unit'], rec['cost_type'], rec['tags'], ts, rid),
        )
        cur.execute(f"SELECT * FROM records WHERE id={PH}", (rid,))
        row = row_to_dict(cur.fetchone())
        conn.commit()
        conn.close()
        return self._send_json(row)

    def _api_delete(self, rid):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"DELETE FROM records WHERE id={PH}", (rid,))
        conn.commit()
        conn.close()
        return self._send_json({'ok': True, 'id': rid})

    def log_message(self, fmt, *args):
        return


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return '127.0.0.1'


def main():
    init_db()
    ip = lan_ip()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', PORT), Handler) as httpd:
        mode = '云端(Postgres)' if USE_PG else '本地(SQLite)'
        print('=' * 56)
        print('  造价记忆助手 已启动  v%s  [%s]' % (VERSION, mode))
        if USE_PG:
            print('  访问地址由云平台分配（请见平台控制台）')
        else:
            print('  电脑访问 :  http://localhost:%d' % PORT)
            print('  手机访问 :  http://%s:%d  （同 WiFi）' % (ip, PORT))
        print('  数据文件 :  %s' % DB_PATH)
        print('  人材机数据包 :  %d 条（cost_items.json）' % COST_ITEMS_COUNT)
        print('  按 Ctrl+C 停止')
        print('=' * 56)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n已停止。')


if __name__ == '__main__':
    main()
