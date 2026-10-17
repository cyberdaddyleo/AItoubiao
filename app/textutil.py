"""文本工具：HTML 转文本、中文日期/金额解析、大写金额、分词、SimHash、BM25、相似度。"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from datetime import datetime
from html import unescape

from bs4 import BeautifulSoup

logging.getLogger("jieba").setLevel(logging.WARNING)
import jieba  # noqa: E402

jieba.setLogLevel(logging.WARNING)

# ---------------------------------------------------------------- HTML → 文本

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "section", "article"}


def html_to_text(html_or_node) -> str:
    """把 HTML 转成保留段落结构的纯文本；表格行转成「单元格 | 单元格」。"""
    if isinstance(html_or_node, str):
        soup = BeautifulSoup(html_or_node, "lxml")
    else:
        soup = html_or_node
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        tr.replace_with(soup.new_string("\n" + " | ".join(c for c in cells if c) + "\n"))
    for br in soup.find_all("br"):
        br.replace_with(soup.new_string("\n"))
    for tag in soup.find_all(list(_BLOCK_TAGS)):
        tag.insert_before(soup.new_string("\n"))
        tag.insert_after(soup.new_string("\n"))
    text = soup.get_text("")
    return normalize_text(text)


def normalize_text(text: str) -> str:
    text = unescape(text or "")
    text = text.replace("\xa0", " ").replace("　", " ").replace("\r", "")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------- 日期

_CN_DT = re.compile(
    r"(\d{4})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*日?"
    r"(?:\s*[（(]?[^0-9:：点时）)]{0,6}[）)]?)?"
    r"(?:\s*(\d{1,2})\s*[:：时点]\s*(\d{1,2})?\s*分?(?:\s*[:：]\s*(\d{1,2}))?)?"
)


def parse_datetime_cn(text: str | None) -> str:
    """把「2026年11月05日 09点30分」「2026.10.15 14:16:39」等解析为 'YYYY-MM-DD HH:MM'。

    只有日期没有时间时返回 'YYYY-MM-DD'；解析失败返回空串。
    """
    if not text:
        return ""
    m = _CN_DT.search(text)
    if not m:
        return ""
    y, mo, d, hh, mm = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    try:
        if hh is not None:
            h = int(hh)
            # 「下午3点」之类
            prefix = text[max(0, m.start(4) - 4): m.start(4)] if m.start(4) >= 0 else ""
            if "下午" in prefix and h < 12:
                h += 12
            dt = datetime(int(y), int(mo), int(d), h, int(mm or 0))
            return dt.strftime("%Y-%m-%d %H:%M")
        dt = datetime(int(y), int(mo), int(d))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def find_all_dates(text: str) -> list[str]:
    out = []
    for m in _CN_DT.finditer(text or ""):
        v = parse_datetime_cn(m.group(0))
        if v:
            out.append(v)
    return out


def to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    v = parse_datetime_cn(value)
    if v and v != value:
        return to_dt(v)
    return None


# ---------------------------------------------------------------- 金额

_AMOUNT = re.compile(
    r"([￥¥]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)?)"
)


def parse_amount_cn(text: str | None, default_unit: str = "元") -> float | None:
    """解析「￥255.000000万元」「478029.00(元)」「人民币壹佰万元整」等，统一返回「元」。"""
    if not text:
        return None
    t = text.replace("（", "(").replace("）", ")")
    m = _AMOUNT.search(t)
    if m:
        num = float(m.group(2).replace(",", ""))
        unit = m.group(3)
        if not unit:
            tail = t[m.end(): m.end() + 6]
            if "万元" in tail or tail.startswith("万"):
                unit = "万元"
            elif "元" in tail:
                unit = "元"
            else:
                unit = default_unit
        if unit in ("亿元", "亿"):
            num *= 1e8
        elif unit in ("万元", "万"):
            num *= 1e4
        return round(num, 2)
    up = re.search(r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆元角分整]{2,}", t)
    if up:
        return cn_upper_to_number(up.group(0))
    return None


def fmt_amount(yuan: float | None) -> str:
    if yuan is None:
        return ""
    if abs(yuan) >= 1e4:
        return f"{yuan / 1e4:,.2f} 万元"
    return f"{yuan:,.2f} 元"


_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ["", "拾", "佰", "仟"]
_BIG = ["", "万", "亿", "兆"]


def number_to_cn_upper(amount: float) -> str:
    """金额小写转大写，如 1234567.89 → 壹佰贰拾叁万肆仟伍佰陆拾柒元捌角玖分。"""
    if amount is None:
        return ""
    if amount < 0:
        return "负" + number_to_cn_upper(-amount)
    fen_total = int(round(amount * 100))
    integer, frac = divmod(fen_total, 100)
    jiao, fen = divmod(frac, 10)
    if integer == 0:
        int_str = ""
    else:
        s = str(integer)
        out = ""
        zero_flag = False
        for i, ch in enumerate(s):
            pos = len(s) - i - 1
            d = int(ch)
            unit_pos, big_pos = pos % 4, pos // 4
            if d == 0:
                zero_flag = True
            else:
                if zero_flag:
                    out += "零"
                    zero_flag = False
                out += _DIGITS[d] + _UNITS[unit_pos]
            if unit_pos == 0 and big_pos > 0:
                group = s[max(0, i - 3): i + 1]
                if int(group) != 0:
                    out += _BIG[big_pos]
        int_str = out + "元"
    if jiao == 0 and fen == 0:
        return (int_str or "零元") + "整"
    s = int_str
    if jiao:
        s += _DIGITS[jiao] + "角"
    elif integer:
        s += "零"
    if fen:
        s += _DIGITS[fen] + "分"
    return s


def cn_upper_to_number(text: str) -> float | None:
    """大写金额转小写（用于核对大小写是否一致）。"""
    t = text.replace("圆", "元").replace("整", "").replace("正", "").strip()
    if not t:
        return None
    digit = {c: i for i, c in enumerate(_DIGITS)}
    digit.update({"两": 2, "〇": 0})
    small = {"拾": 10, "佰": 100, "仟": 1000}
    big = {"万": 10 ** 4, "亿": 10 ** 8}
    total = 0.0
    section = 0
    number = 0
    yuan_part, _, rest = t.partition("元")
    if "元" not in t:
        if "角" in t or "分" in t:
            yuan_part, rest = "", t
        else:
            yuan_part, rest = t, ""
    for ch in yuan_part:
        if ch in digit:
            number = digit[ch]
        elif ch in small:
            section += (number or 1) * small[ch]
            number = 0
        elif ch in big:
            section += number
            total += section * big[ch]
            section = 0
            number = 0
        else:
            return None
    total += section + number
    jiao = re.search(r"([零壹贰叁肆伍陆柒捌玖])角", rest)
    fen = re.search(r"([零壹贰叁肆伍陆柒捌玖])分", rest)
    if jiao:
        total += digit[jiao.group(1)] / 10
    if fen:
        total += digit[fen.group(1)] / 100
    return round(total, 2)


# ---------------------------------------------------------------- 分词与相似度

_STOP = set(
    "的 了 和 与 及 或 等 对 在 为 是 将 由 于 并 其 之 以 按 向 本 该 此 有 无 应 须 可 不 中 上 下 内 外 "
    "项目 公告 采购 招标 公开 年度 一批 服务 工程 关于 进行 相关 要求".split()
)
_PUNCT = re.compile(r"^[\W_]+$", re.UNICODE)


def tokenize(text: str, *, keep_stop: bool = False) -> list[str]:
    out = []
    for w in jieba.lcut(text or ""):
        w = w.strip().lower()
        if not w or _PUNCT.match(w):
            continue
        if len(w) == 1 and not w.isalnum():
            continue
        if not keep_stop and w in _STOP:
            continue
        out.append(w)
    return out


def simhash64(text: str) -> int:
    weights = Counter(tokenize(text))
    if not weights:
        return 0
    v = [0] * 64
    for tok, w in weights.items():
        h = int.from_bytes(hashlib.md5(tok.encode("utf-8")).digest()[:8], "big")
        for i in range(64):
            v[i] += w if (h >> i) & 1 else -w
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def shingles(text: str, k: int = 8) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    t = re.sub(r"[【】\[\]（）()：:，,。.；;、|#\-*]", "", t)
    if len(t) <= k:
        return {t} if t else set()
    return {t[i: i + k] for i in range(0, len(t) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set, b: set) -> float:
    """a 中有多大比例出现在 b 里（用于判断「抄了多少」）。"""
    if not a:
        return 0.0
    return len(a & b) / len(a)


def split_keywords(text: str | None) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,，;；、\s\n]+", text)
    return [p.strip() for p in parts if p.strip()]


class BM25:
    """小规模语料的 BM25 检索（资料库方案素材、历史标书段落）。"""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.toks = [tokenize(d) for d in docs]
        self.k1, self.b = k1, b
        self.avgdl = (sum(len(t) for t in self.toks) / len(self.toks)) if self.toks else 0
        df: Counter = Counter()
        for t in self.toks:
            df.update(set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.tf = [Counter(t) for t in self.toks]

    def scores(self, query: str) -> list[float]:
        qt = tokenize(query)
        out = []
        for i, tf in enumerate(self.tf):
            dl = len(self.toks[i]) or 1
            s = 0.0
            for w in qt:
                if w not in tf:
                    continue
                f = tf[w]
                s += self.idf.get(w, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
            out.append(s)
        return out

    def top(self, query: str, k: int = 5, min_score: float = 0.1) -> list[tuple[int, float]]:
        sc = self.scores(query)
        ranked = sorted(range(len(sc)), key=lambda i: sc[i], reverse=True)
        return [(i, sc[i]) for i in ranked[:k] if sc[i] >= min_score]


def chunk_text(text: str, max_chars: int = 600) -> list[str]:
    """按段落切块，过长段落再按句号切。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#)", text or "") if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            sents = re.split(r"(?<=[。；！？])", p)
            cur = ""
            for s in sents:
                if len(cur) + len(s) > max_chars and cur:
                    out.append(cur)
                    cur = ""
                cur += s
            if cur:
                out.append(cur)
            continue
        if len(buf) + len(p) + 1 > max_chars and buf:
            out.append(buf)
            buf = ""
        buf = (buf + "\n" + p).strip()
    if buf:
        out.append(buf)
    return out
