#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
造价个人助手 —— 本地/云端通用个人数据记录后端
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
VERSION = '1.5.3'  # +规格型号识别增强(电压等级/公称直径/复合截面/强度等级/三维尺寸，参考广材网)

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
            feature TEXT,
            note TEXT,
            tax_mode TEXT,
            photo TEXT,
            amount REAL,
            unit TEXT,
            quantity REAL,
            total REAL,
            items TEXT,
            price_src TEXT,
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
            feature TEXT,
            note TEXT,
            tax_mode TEXT,
            photo TEXT,
            amount REAL,
            unit TEXT,
            quantity REAL,
            total REAL,
            items TEXT,
            price_src TEXT,
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
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='feature'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN feature TEXT")
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='note'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN note TEXT")
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='tax_mode'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN tax_mode TEXT")
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='photo'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN photo TEXT")
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='quantity'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN quantity REAL")
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='total'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN total REAL")
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='items'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN items TEXT")
            cur.execute("SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='records' AND column_name='price_src'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE records ADD COLUMN price_src TEXT")
        else:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(records)")]
            if 'cost_type' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN cost_type TEXT")
            if 'feature' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN feature TEXT")
            if 'note' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN note TEXT")
            if 'tax_mode' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN tax_mode TEXT")
            if 'photo' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN photo TEXT")
            if 'quantity' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN quantity REAL")
            if 'total' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN total REAL")
            if 'items' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN items TEXT")
            if 'price_src' not in cols:
                conn.execute("ALTER TABLE records ADD COLUMN price_src TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()


def row_to_dict(r):
    """行 -> dict。photo 体积大，不进普通响应，单独走 /api/photo/<id> 取。"""
    d = dict(r)
    for k in ('content', 'category', 'project', 'feature', 'note', 'tax_mode',
              'amount', 'unit', 'quantity', 'total', 'items', 'price_src', 'cost_type',
              'source', 'tags', 'created_at', 'updated_at'):
        d.setdefault(k, None)
    # items 存的是 JSON 文本，这里反序列化成数组，方便前端直接渲染
    raw_items = d.get('items')
    if isinstance(raw_items, str) and raw_items.strip():
        try:
            d['items'] = json.loads(raw_items)
        except Exception:
            d['items'] = None
    elif raw_items is None:
        d['items'] = None
    d.pop('photo', None)
    return d


# ------------------------------------------------------------------ 解析逻辑
UNITS = ['立方米', '立方', 'm³', 'm3', '平方米', '平米', 'm²', 'm2', '平方', '平',
         '吨', 't', '公斤', '千克', 'kg', '克', 'g',
         '工日', '工时', '台班', '班次', '小时', '分钟', '天', '日', '周', '月', '年',
         '延米', '千米', '公里', 'km', '米', 'm', '厘米', 'cm', '毫米', 'mm',
         '个', '根', '条', '块', '张', '片', '只', '把', '件', '套', '组', '台',
         '樘', '扇', '座', '处', '项', '次', '遍', '道', '层', '系统', '单元', '标段',
         '桶', '袋', '卷', '箱', '捆', '车', '罐', '瓶', '圈', '方']
_UNIT_ALT = '|'.join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
# 价格识别：带货币(元/块/￥)，允许数字与货币之间夹"括号注释"如 20（科顺）元；
# 支持区间价 8-10（取下限数值）。也支持"每/单价单位(/m、/卷)"。
# 排除型号号(1.5mm、5*6、3.2MPa、m3里的3)。
_PRICE_CORE = r'(?<![\d.])(?P<num>\d+(?:\.\d+)?)(?:\s*-\s*\d+(?:\.\d+)?)?'  # 区间价取首值
_PRICE_CORE2 = r'(?<![\d.])(?P<num2>\d+(?:\.\d+)?)(?:\s*-\s*\d+(?:\.\d+)?)?'
_AMOUNT_RE = re.compile(
    _PRICE_CORE + r'\s*(?:[（(][^）)]*[）)])?\s*(?P<cur>元|块|￥|人民币)\b'
    r'|'
    + _PRICE_CORE2 + r'\s*(?:[（(][^）)]*[）)])?\s*(?P<per>/|每|单价|per|一)\s*(?P<unit>' + _UNIT_ALT + r')'
)
# 规格识别（参考广材网材料规格写法）：
#  - 电压等级 0.6/1kV / 450/750V / 8.7/15
#  - 公称直径 DN100 / SC100 / de110
#  - 直径/厚度符号 Φ100 / δ3.0
#  - 厚20mm
#  - 强度等级 C30 / M10 / M32.5
#  - 复合截面 5×16 / 4×70+1×25 / 3×10+1×6（芯数×截面，可多段）
#  - 尺寸组合 5*6 / 10m*1m / 1.5mm×2m×20m（二维或三维）
#  - 型号后数字 BYJ2.5
#  - 单独带单位 1.5mm
_SPEC_RE = re.compile(
    r'(?:'
    r'\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*k?V'                              # 电压等级
    r'|(?:DN|SC|de|De)\d+(?:\.\d+)?'                                       # 公称直径
    r'|[Φφδ]\s*\d+(?:\.\d+)?'                                              # 直径/厚度符号
    r'|厚\s*\d+(?:\.\d+)?\s*(?:mm|cm|m)?'                                  # 厚20mm
    r'|[CM]\d+(?:\.\d+)?'                                                  # 强度等级 C30/M10
    r'|\d+\s*[×x*]\s*\d+(?:\s*[+]\s*\d+\s*[×x*]\s*\d+)*'                  # 复合截面
    r'|[\d.]+\s*(?:mm|cm|m[²³]?|平方)?\s*[×x*]\s*[\d.]+\s*(?:mm|cm|m|平方)?'
    r'(?:\s*[×x*]\s*[\d.]+\s*(?:mm|cm|m|平方)?)?'                          # 尺寸组合(二维/三维)
    r'|(?<=[^\s\d.])\d{1,4}(?:\.\d{1,2})?(?![\w.])'                        # 型号后数字 BYJ2.5
    r'|[\d.]+\s*(?:mm|cm|m[²³]?|平方)\s*厚?'                              # 单独带单位(可后缀"厚")
    r')'
)
# 量词（名称末尾的容器/单位词，属于"每X"的X，不是材料名）
_MEASURE_KW = ['一卷', '一包', '一桶', '一根', '一袋', '一箱', '一捆', '一车', '一台',
               '一套', '一组', '一张', '一块', '一件', '一只', '一个', '一卷', '一樘',
               '一扇', '一座', '一处', '一项', '一次', '一遍', '一道', '一层']
# 名称/备注里常见的"价格上下文"噪声词（会连续清理）
_PRICE_NOISE = r'(?:单价|价格|报价|合价|成本|每|一|含税|不含税|国标|非标|税率|税|元|块|人民币|￥)'

# ---- 中文数字 / 大写金额 支持 ----
_CN_DIGITS = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '两': 2,
    '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9,
}
_CN_UNITS = {'十': 10, '拾': 10, '百': 100, '佰': 100, '千': 1000, '仟': 1000,
             '万': 10000, '亿': 100000000}
_CN_NUM_RE = re.compile(r'[零一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟万亿]{2,}')
_MIX_WAN_RE = re.compile(r'(\d+(?:\.\d+)?)\s*([万亿])')
_MIX_QIAN_RE = re.compile(r'(\d+)\s*([千百])\s*(\d+)?')


def cn_to_int(s):
    """中文/大写数字串转整数：三百五十→350、叁佰伍拾→350。含非数字字则返回 None"""
    total = 0
    section = 0
    num = 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            u = _CN_UNITS[ch]
            if u >= 10000:
                section = (section + (num if num else 1)) * u
                total += section
                section = 0
            else:
                section += (num if num else 1) * u
            num = 0
        else:
            return None
    return total + section + num


def normalize_numbers(text):
    """口语/中文/大写金额统一转阿拉伯数字：3.5万→35000、2千5→2500、3百5→350、
    三百五十→350、叁佰伍拾→350。无法解析的原文保留。"""

    def _wan(m):
        v = float(m.group(1)) * (10000 if m.group(2) == '万' else 100000000)
        return str(int(v)) if float(v).is_integer() else str(v)
    text = _MIX_WAN_RE.sub(_wan, text)

    def _qian(m):
        base = int(m.group(1)) * (1000 if m.group(2) == '千' else 100)
        if m.group(3):
            base += int(m.group(3)) * (100 if m.group(2) == '千' else 10)
        return str(base)
    text = _MIX_QIAN_RE.sub(_qian, text)

    def _cn(m):
        """中文/大写数字串转阿拉伯数字。
        口语里\"三十五一平\"的\"一\"是\"每一\"的意思，不计入数值（否则变成31）。
        仅当\"一\"后紧跟计价单位、且\"一\"前是数字时才剔除，避免误伤\"三十一米\"。"""
        s = m.group(0)
        if s.endswith('一') and len(s) >= 2 and s[-2] in _CN_DIGITS:
            nxt = text[m.end():m.end() + 4]
            if any(nxt.startswith(u) for u in UNITS):
                s = s[:-1]
        v = cn_to_int(s)
        return str(v) if v else m.group(0)
    text = _CN_NUM_RE.sub(_cn, text)
    return text

PRICE_KW = ['单价', '人工', '人工费', '价格', '报价', '多少钱', '费用', '成本',
            '材料', '机械', '管理费', '规费', '税金', '信息价', '市场价', '劳务费',
            '工资', '工价', '台班', '租赁']
# 类型指示词（显式声明，优先级最高）。具体条目名走 cost_items.json 数据包。
COST_KW = {
    '人工': ['人工', '人工费', '工资', '劳务', '工价', '工日', '砌筑', '砌砖', '抹灰'],
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
    # 名称后置："外滩项目"/"丹江工地"（黑名单防误抓，见 find_project）
    # lookbehind 排除数字/"一"，避免把"350一天外滩项目"的"一天外滩"当名称
    re.compile(r'(?<![0-9一])([\u4e00-\u9fa5]{2,6}?)(?:项目|工地|工程)(?![：:是为])'),
]
_PROJECT_BLACKLIST = {'记事本', '小本', '本', '该', '此', '整', '事项', '备忘', '记账'}


def detect_category(text, amount=None):
    # 有金额的一律算单价（不管有没有写"人工/价格"等词），保证"事情"筛选里不混进单价
    if amount is not None:
        return '单价费用'
    if any(k in text for k in PRICE_KW):
        return '单价费用'
    if any(k in text for k in ['提醒', '记得', '别忘', '别漏', '待办', '跟进', '对账',
                               '约', '别忘了', '要记', '回访', '催', '落实', '确认',
                               '下周', '明天', '后天', '本周', '下月', '到时候', '记得问']):
        return '事项'
    return '备忘'


STRONG_TYPE_KW = {'材料': '材料', '机械': '机械', '人工': '人工', '人工费': '人工', '工资': '人工'}


def detect_cost_type(text):
    # 1) 用户显式写的类型词（材料/机械/人工）最优先，尊重明确声明
    #    否则"砌砖，材料230"会被工种词"砌砖"误判为人工
    for kw, t in STRONG_TYPE_KW.items():
        if kw in text:
            return t
    # 2) 建筑业数据包：人工优先于材料（处理"混凝土工"等前缀冲突），机械最后
    if any(w in text for w in COST_ITEMS['人工']):
        return '人工'
    if any(w in text for w in COST_ITEMS['材料']):
        return '材料'
    if any(w in text for w in COST_ITEMS['机械']):
        return '机械'
    # 3) 其余弱指示词（台班/租赁/工日…）
    for t, kws in COST_KW.items():
        if any(k in text for k in kws):
            return t
    return None


def parse_content(content):
    """解析主单价（兼容旧接口）：返回 (amount, unit)。优先真价格，兜底裸数字。"""
    norm = normalize_numbers(content or '')
    m = _AMOUNT_RE.search(norm)
    if not m:  # 兜底：末尾裸数字（杂工220）
        m = re.search(r'(?<![\d.*/])(?P<num>\d+(?:\.\d+)?)(?![\d.])(?=[\s，,；;。]|$)', norm)
    if not m:
        return None, None
    num = m.group('num') or m.group('num2')
    try:
        amount = float(num)
    except (TypeError, ValueError):
        return None, None
    cur = m.groupdict().get('cur')
    u = m.groupdict().get('unit')
    if cur and u:
        unit = cur + '/' + u
    elif cur:
        unit = cur
    elif u:
        unit = '元/' + u
    else:
        unit = None
    return amount, unit


def parse_items(content):
    """把一条内容拆成多个价格项，尽量拆出 材料名 / 规格 / 单位 / 备注。
    例：'双面防皱自粘聚合物改性沥青防水卷材1.5mm，一卷，145.8元；
         防水胶一包7.5元，聚合物沥青防水涂料一桶302.4元'
    → [{name:'双面防皱自粘聚合物改性沥青防水卷材', spec:'1.5mm',
        amount:145.8, unit:'元/卷', note:''}, ...]
    价格优先认：带货币(元/块/￥) / 每+单价单位(/m、/卷)。
    若整段无任何带货币价格，才退回取末尾裸数字(如 杂工220)。
    坚决排除型号号(1.5mm、5*6、3.2MPa、m3里的3)。
    """
    if not content:
        return []
    norm = normalize_numbers(content)
    _BARE = re.compile(r'(?<![\d.*/])(?P<num>\d+(?:\.\d+)?)(?![\d.])(?=[\s，,；;。]|$)')
    items = []
    segs = re.split(r'[；;。、\n]', norm)
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        ms = list(_AMOUNT_RE.finditer(seg))
        if not ms:
            # 兜底：本段末尾裸数字（杂工220）
            bm = _BARE.search(seg)
            if not bm:
                continue
            ms = [bm]
        seg_prev = 0
        for idx, m in enumerate(ms):
            gd = m.groupdict()
            num = gd.get('num') or gd.get('num2')
            cur = gd.get('cur')
            u = gd.get('unit')
            try:
                amount = float(num)
            except (TypeError, ValueError):
                seg_prev = m.end()
                continue
            if cur and u:
                unit = cur + '/' + u
            elif cur:
                unit = cur
            elif u:
                unit = '元/' + u
            else:
                unit = None
            before = seg[seg_prev:m.start()].strip().lstrip('，,、：:；;').strip()
            # 量词(一卷/一包) → 并入单位
            mq = re.search(r'(一卷|一包|一桶|一根|一袋|一箱|一捆|一车|一台|一套|一组|一张|一块|一件|一只|一个|一樘|一扇|一座|一处|一项|一次|一遍|一道|一层)\s*[，,、：:]?', before)
            if mq:
                measure = mq.group(1)[1:]
                unit = (unit.split('/')[0] if unit else '元') + '/' + measure
                before = before[:mq.start()] + before[mq.end():]
            specs = _SPEC_RE.findall(before)
            spec = ' '.join(specs).strip() if specs else ''
            name = _SPEC_RE.sub(' ', before).strip().rstrip('，,、：:').strip()
            name = re.sub(r'[的一]$', '', name).strip()
            # 连续清理名称末尾的价格上下文噪声词（允许 - / 空格等连接符一起去掉）
            for _ in range(10):
                new_name = re.sub(r'[-/\s，,、：:]*' + _PRICE_NOISE + r'[\s，,、：:]*$', '', name).strip()
                if new_name == name:
                    break
                name = new_name
            name = re.sub(r'^[\s，,、：:]*' + _PRICE_NOISE, '', name).strip()
            # 备注：价格之后到下一个价格之前（若段内多个价格用逗号连接）
            next_start = ms[idx + 1].start() if idx + 1 < len(ms) else len(seg)
            after = seg[m.end():next_start].strip()
            # 如果 after 去掉标点后是下一个 item 的"名称+量词"，不当作本条备注
            _tail = after.lstrip('，,、：:。） ').strip()
            if re.search(r'(?:一卷|一包|一桶|一根|一袋|一箱|一捆|一车|一台|一套|一组|一张|一块|一件|一只|一个|一樘|一扇|一座|一处|一项|一次|一遍|一道|一层)$', _tail):
                after = ''
            else:
                after = after.lstrip('，,、：:）。').strip()
                # "300元/天" → 单位合并为元/天
                _ut = re.match(r'^/(?P<u>' + _UNIT_ALT + r')\b', after)
                if not _ut:
                    _ut = re.match(r'^每(?P<u>' + _UNIT_ALT + r')\b', after)
                if _ut:
                    unit = (unit or '元') + '/' + _ut.group('u')
                    after = after[_ut.end():].strip().lstrip('，,、：:').strip()
                after = re.sub(r'^(一卷|一包|一桶|一根|一袋|一箱|一捆|一车|一台|一套|一组|一张|一块|一件|一只|一个|一樘|一扇|一座|一处|一项|一次|一遍|一道|一层)[，,、：:]?', '', after).strip()
                after = re.sub(r'^' + _PRICE_NOISE + r'[\s，,、：:]*', '', after).strip()
            if not name and not unit:
                seg_prev = m.end()
                continue
            items.append({
                'name': name,
                'spec': spec,
                'amount': amount,
                'unit': unit,
                'note': after,
                'has_cur': bool(cur),
            })
            seg_prev = m.end()
    return items


def find_project(text):
    for rx in PROJECT_RES:
        m = rx.search(text)
        if m:
            name = m.group(1).strip()
            if name and name not in _PROJECT_BLACKLIST:
                return name
    return None


def build_record(content, project=None, category=None, cost_type=None, source='文字',
                 quantity=None, total=None):
    content = (content or '').strip()
    amount, unit = parse_content(content)
    if category is None or category not in ('单价费用', '事项', '备忘'):
        category = detect_category(content, amount)
    if not project:
        project = find_project(content)
    if not cost_type or cost_type not in ('人工', '材料', '机械', '事情'):
        cost_type = detect_cost_type(content)
    # 合价：已显式给则用之；否则工程量×单价自动算（向下取整2位）
    computed_total = None
    try:
        q = float(quantity) if quantity not in (None, '') else None
    except (TypeError, ValueError):
        q = None
    if total not in (None, ''):
        try:
            computed_total = round(float(total), 2)
        except (TypeError, ValueError):
            computed_total = None
    elif q is not None and amount is not None:
        computed_total = round(q * amount, 2)
    items = parse_items(content) if content else None
    return {
        'content': content,
        'category': category,
        'project': project,
        'amount': amount,
        'unit': unit,
        'quantity': q,
        'total': computed_total,
        'items': json.dumps(items, ensure_ascii=False) if items else None,
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
            if p == '/api/export.csv':
                return self._api_export_csv()
            if p.startswith('/api/photo/'):
                return self._api_photo(p)
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
        if parsed.path == '/api/import':
            return self._api_import()
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
            sql = ("SELECT id, content, category, project, feature, note, tax_mode, amount, unit, "
                   "quantity, total, items, price_src, cost_type, source, tags, created_at, "
                   "updated_at, (photo IS NOT NULL AND photo != '') AS has_photo FROM records WHERE 1=1")
            args = []
            if q:
                like = f'%{q}%'
                amt_col = "CAST(amount AS TEXT)" if not USE_PG else "amount::text"
                sql += (f" AND (content LIKE {PH} OR project LIKE {PH} OR tags LIKE {PH}"
                        f" OR unit LIKE {PH} OR cost_type LIKE {PH} OR category LIKE {PH}"
                        f" OR feature LIKE {PH} OR note LIKE {PH} OR {amt_col} LIKE {PH})")
                args += [like] * 9
            if project:
                sql += f" AND project = {PH}"
                args.append(project)
            if category:
                cats = [c for c in category.split(',') if c in ('单价费用', '事项', '备忘')]
                if len(cats) == 1:
                    sql += f" AND category = {PH}"
                    args.append(cats[0])
                elif len(cats) > 1:
                    sql += f" AND category IN ({','.join([PH] * len(cats))})"
                    args += cats
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

        if p == '/api/summary':
            # 按 项目 × 人材机 汇总金额（P2，仅统计有金额的单价记录）
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT project, cost_type, COUNT(*) n, SUM(amount) s, "
                        "SUM(quantity) q, SUM(total) t "
                        "FROM records WHERE amount IS NOT NULL "
                        "GROUP BY project, cost_type ORDER BY project")
            out = {}
            for r in cur.fetchall():
                proj = r['project'] or '未填项目'
                ct = r['cost_type'] or '未分类'
                out.setdefault(proj, {})[ct] = {
                    'count': r['n'], 'total': round(float(r['s'] or 0), 2),
                    'qty': round(float(r['q'] or 0), 2),
                    'sum_total': round(float(r['t'] or 0), 2)}
            conn.close()
            return self._send_json(out)

        if p == '/api/history':
            # 同一材料的历史价格（P1）：按关键词模糊匹配、时间正序
            q = (qs.get('q', [''])[0] or '').strip()
            if not q:
                return self._send_json([])
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT id, content, cost_type, amount, unit, project, tax_mode, "
                        "created_at FROM records WHERE content LIKE " + PH +
                        " AND amount IS NOT NULL ORDER BY id ASC LIMIT 100", (f'%{q}%',))
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return self._send_json(rows)

        self._send_json({'error': 'not found'}, status=404)

    def _api_photo(self, path):
        """返回某条记录的图片（P5）：库里存 dataURL，这里解析成图片二进制。"""
        import base64
        m = re.match(r'/api/photo/(\d+)', path)
        if not m:
            return self._send_text('Not Found', status=404)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT photo FROM records WHERE id={PH}", (int(m.group(1)),))
        row = cur.fetchone()
        conn.close()
        data_url = (dict(row).get('photo') if row else None) or ''
        mt = re.match(r'data:([^;]+);base64,(.*)$', data_url, re.S)
        if not mt:
            return self._send_text('No Image', status=404)
        try:
            raw = base64.b64decode(mt.group(2))
        except Exception:
            return self._send_text('Bad Image', status=500)
        self.send_response(200)
        self.send_header('Content-Type', mt.group(1))
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'private, max-age=86400')
        self.end_headers()
        self.wfile.write(raw)

    def _api_export_csv(self):
        """导出全部记录为 CSV（Excel 直接打开、中文不乱码）——P3"""
        import csv
        import io
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM records ORDER BY id DESC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['序号', '内容', '分类', '项目', '项目特征', '备注', '价格来源', '价格口径',
                    '单价', '单位', '工程量', '合价', '明细(多项)', '人材机', '来源', '记录时间'])
        for r in rows:
            items = r.get('items')
            if isinstance(items, str) and items.strip():
                try:
                    items = json.loads(items)
                except Exception:
                    items = None
            items_txt = ''
            if isinstance(items, list):
                items_txt = ' | '.join(
                    f"{it.get('name','')}{it.get('amount','')}{it.get('unit','')}"
                    for it in items if isinstance(it, dict))
            w.writerow([
                r.get('id'), r.get('content') or '', r.get('category') or '',
                r.get('project') or '', r.get('feature') or '', r.get('note') or '',
                r.get('tax_mode') or '', r.get('price_src') or '',
                '' if r.get('amount') is None else r.get('amount'),
                r.get('unit') or '',
                '' if r.get('quantity') is None else r.get('quantity'),
                '' if r.get('total') is None else r.get('total'),
                items_txt,
                r.get('cost_type') or '', r.get('source') or '',
                r.get('created_at') or '',
            ])
        data = ('\ufeff' + buf.getvalue()).encode('utf-8')  # BOM：让 Excel 正确识别中文
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition',
                         "attachment; filename=\"costmem.csv\"; " +
                         "filename*=UTF-8''" + quote('造价个人助手导出.csv'))
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
                         "filename*=UTF-8''" + quote('造价个人助手备份.json'))
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
            quantity=body.get('quantity'),
            total=body.get('total'),
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
            quantity=body.get('quantity'),
            total=body.get('total'),
        )
        feature = (body.get('feature') or '').strip() or None
        note = (body.get('note') or '').strip() or None
        tax_mode = body.get('tax_mode') or None
        price_src = (body.get('price_src') or '').strip() or None
        photo = body.get('photo') or None
        if not rec['content'] and not photo:
            return self._send_json({'error': '内容和照片不能都为空'}, status=400)
        ts = now_iso()
        conn = get_conn()
        cur = conn.cursor()
        col_list = ["content", "category", "project", "feature", "note", "tax_mode",
                    "amount", "unit", "quantity", "total", "items", "price_src",
                    "cost_type", "source", "tags", "photo", "created_at", "updated_at"]
        cols = ', '.join(col_list)
        phs = ', '.join([PH] * len(col_list))  # 占位符数量随列数自动生成，防个数不匹配
        vals = (rec['content'], rec['category'], rec['project'], feature, note, tax_mode,
                rec['amount'], rec['unit'], rec['quantity'], rec['total'], rec['items'],
                price_src, rec['cost_type'], rec['source'], rec['tags'], photo, ts, ts)
        if USE_PG:
            cur.execute(f"""INSERT INTO records ({cols}) VALUES ({phs}) RETURNING id""", vals)
            rid = cur.fetchone()['id']
        else:
            cur.execute(f"""INSERT INTO records ({cols}) VALUES ({phs})""", vals)
            rid = cur.lastrowid
        cur.execute(f"SELECT * FROM records WHERE id={PH}", (rid,))
        row = row_to_dict(cur.fetchone())
        conn.commit()
        conn.close()
        return self._send_json(row, status=201)

    def _api_import(self):
        """批量导入：body = {records:[{content, project, feature, note, tax_mode, cost_type,...}]}"""
        body = self._read_body()
        rows = body.get('records') or []
        if not isinstance(rows, list) or not rows:
            return self._send_json({'error': '没有可导入的数据'}, status=400)
        col_list = ["content", "category", "project", "feature", "note", "tax_mode",
                    "amount", "unit", "quantity", "total", "items", "price_src",
                    "cost_type", "source", "tags", "created_at", "updated_at"]
        cols = ', '.join(col_list)
        phs = ', '.join([PH] * len(col_list))
        conn = get_conn()
        cur = conn.cursor()
        ts = now_iso()
        ok = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            content = (r.get('content') or '').strip()
            if not content:
                continue
            rec = build_record(
                content,
                project=r.get('project') or None,
                category=r.get('category') or None,
                cost_type=r.get('cost_type') or None,
                source=r.get('source') or '导入',
                quantity=r.get('quantity'),
                total=r.get('total'),
            )
            # 导入时可直接指定金额/单位（区间价如"8-10"更准），没给就用自动解析值
            if r.get('amount') is not None:
                try:
                    rec['amount'] = float(r['amount'])
                except (TypeError, ValueError):
                    pass
            if (r.get('unit') or '').strip():
                rec['unit'] = r['unit'].strip()
            ps = (r.get('price_src') or '').strip() or None
            cur.execute(f"INSERT INTO records ({cols}) VALUES ({phs})", (
                rec['content'], rec['category'], rec['project'],
                (r.get('feature') or '').strip() or None,
                (r.get('note') or '').strip() or None,
                r.get('tax_mode') or None,
                rec['amount'], rec['unit'], rec['quantity'], rec['total'], rec['items'], ps,
                rec['cost_type'], rec['source'], rec['tags'], ts, ts,
            ))
            ok += 1
        conn.commit()
        conn.close()
        return self._send_json({'ok': True, 'imported': ok}, status=201)

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
        feature = body.get('feature', row['feature'])
        note = body.get('note', row['note'])
        tax_mode = body.get('tax_mode', row['tax_mode'])
        photo = body.get('photo', row['photo'])
        price_src = body.get('price_src', row['price_src'] if 'price_src' in dict(row) else None)
        quantity = body.get('quantity', row['quantity'] if 'quantity' in dict(row) else None)
        total = body.get('total', row['total'] if 'total' in dict(row) else None)
        rec = build_record(content, project=project, category=category,
                           cost_type=cost_type, source=row['source'],
                           quantity=quantity, total=total)
        # 若前端显式传了 items（如手动改明细），以之覆盖自动解析
        items_in = body.get('items')
        if isinstance(items_in, list):
            rec['items'] = json.dumps(items_in, ensure_ascii=False)
        elif 'items' in dict(row):
            rec['items'] = row['items']
        ts = now_iso()
        cur.execute(
            f"""UPDATE records SET content={PH}, category={PH}, project={PH}, feature={PH},
               note={PH}, tax_mode={PH}, photo={PH}, amount={PH}, unit={PH}, quantity={PH},
               total={PH}, items={PH}, price_src={PH}, cost_type={PH}, tags={PH}, updated_at={PH} WHERE id={PH}""",
            (rec['content'], rec['category'], rec['project'], feature, note, tax_mode, photo,
             rec['amount'], rec['unit'], rec['quantity'], rec['total'], rec['items'],
             price_src, rec['cost_type'], rec['tags'], ts, rid),
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
        print('  造价个人助手 已启动  v%s  [%s]' % (VERSION, mode))
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
