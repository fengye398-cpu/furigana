# -*- coding: utf-8 -*-
"""kara-templater.lua + karaskel-auto4.lua 的 Python 复现（候选实现，尚未接入主程序）。

输入：带 `template ... syl/furi` 注释行和 {\\k} 卡拉OK标记的 .ass 文本；
输出：与 Aegisub 运行 Karaoke Templater 后等价的 .ass 文本（原行变 Comment/karaoke，新增 fx 行）。
字宽用 Windows GDI（与 Aegisub 同一套 API），非 Windows 下不可用。
只支持 syl / furi 两类模板和 noblank、all、repeat、notext、keeptags、fx 修饰符；
遇到 line、pre-line、char、multi、code 等会记入 stats["unsupported"]，不静默忽略。
"""
import ast
import os
import ctypes
import json
import math
import re
import sys
from ctypes import wintypes
from types import SimpleNamespace as NS

GDI_SCALE = 64          # Aegisub 用 fontsize*64 创建字体再除回来，以获得亚像素精度
FURIGANA_SCALE = 0.5


class GdiMetrics:
    """aegisub.text_extents 的等价实现：返回 (宽, 高)。"""

    class _LOGFONT(ctypes.Structure):
        _fields_ = [("lfHeight", wintypes.LONG), ("lfWidth", wintypes.LONG), ("lfEscapement", wintypes.LONG),
                    ("lfOrientation", wintypes.LONG), ("lfWeight", wintypes.LONG), ("lfItalic", ctypes.c_ubyte),
                    ("lfUnderline", ctypes.c_ubyte), ("lfStrikeOut", ctypes.c_ubyte), ("lfCharSet", ctypes.c_ubyte),
                    ("lfOutPrecision", ctypes.c_ubyte), ("lfClipPrecision", ctypes.c_ubyte),
                    ("lfQuality", ctypes.c_ubyte), ("lfPitchAndFamily", ctypes.c_ubyte),
                    ("lfFaceName", ctypes.c_wchar * 32)]

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("kara_fx 需要 Windows GDI 计算字宽")
        g = self.gdi = ctypes.windll.gdi32
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateFontIndirectW.restype = wintypes.HFONT
        g.CreateFontIndirectW.argtypes = [ctypes.POINTER(self._LOGFONT)]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.SIZE)]
        g.SetMapMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.dc = g.CreateCompatibleDC(None)
        g.SetMapMode(self.dc, 1)                       # MM_TEXT
        self._fonts = {}

    def _font(self, st):
        key = (st.fontname, st.fontsize, st.bold, st.italic, st.underline, st.strikeout, st.encoding)
        if key not in self._fonts:
            lf = self._LOGFONT()
            lf.lfHeight = int(st.fontsize * GDI_SCALE)
            lf.lfWeight = 700 if st.bold else 400
            lf.lfItalic, lf.lfUnderline, lf.lfStrikeOut = int(st.italic), int(st.underline), int(st.strikeout)
            lf.lfCharSet = st.encoding & 0xFF
            lf.lfOutPrecision, lf.lfClipPrecision, lf.lfQuality = 4, 0, 4    # OUT_TT_PRECIS, DEFAULT, ANTIALIASED
            lf.lfFaceName = st.fontname
            self._fonts[key] = self.gdi.CreateFontIndirectW(ctypes.byref(lf))
        return self._fonts[key]

    def extents(self, st, text):
        if not text:
            return 0.0, 0.0
        self.gdi.SelectObject(self.dc, self._font(st))
        sz = wintypes.SIZE()
        self.gdi.GetTextExtentPoint32W(self.dc, text, len(text), ctypes.byref(sz))
        width = sz.cx * st.scalex / 100.0 / GDI_SCALE + st.spacing * len(text)
        return width, sz.cy / float(GDI_SCALE)


def app_base_dir():
    """程序所在目录：打包后是 exe 旁边（ass_style.json、字体\ 放在这里），源码运行时是本文件所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


FONT_DIR_NAME = "字体"          # 分发包里字体所在的子目录（与 kara_fx.py / 主程序同级）
_FONT_EXTS = (".otf", ".ttf", ".ttc")


class _ENUMLOGFONTEXW(ctypes.Structure):
    _fields_ = [("elfLogFont", GdiMetrics._LOGFONT), ("elfFullName", ctypes.c_wchar * 64),
                ("elfStyle", ctypes.c_wchar * 32), ("elfScript", ctypes.c_wchar * 32)]


_ENUM_CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(_ENUMLOGFONTEXW), ctypes.c_void_p,
                              wintypes.DWORD, wintypes.LPARAM)


def font_installed(name, gdi=None):
    """本机是否安装了名为 name 的字体（中文名、英文名、别名如 SimSun 都认）。

    不能用「CreateFont 后读回实际字体名」来判断：SimSun 会被读成「宋体」，造成误判（已实测）。
    EnumFontFamiliesEx 按名字枚举，找不到就返回空，不会静默回退。
    """
    if sys.platform != "win32" or not name:
        return True
    g = ctypes.windll.gdi32
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.EnumFontFamiliesExW.argtypes = [wintypes.HDC, ctypes.POINTER(GdiMetrics._LOGFONT), _ENUM_CB,
                                      wintypes.LPARAM, wintypes.DWORD]
    g.DeleteDC.argtypes = [wintypes.HDC]
    dc = g.CreateCompatibleDC(None)
    try:
        lf = GdiMetrics._LOGFONT()
        lf.lfCharSet = 1                                   # DEFAULT_CHARSET：不限字符集
        lf.lfFaceName = name[:31]
        hit = []

        def cb(elf, tm, ft, lp):
            hit.append(1)
            return 0                                       # 找到一个就停止枚举
        g.EnumFontFamiliesExW(dc, ctypes.byref(lf), _ENUM_CB(cb), 0, 0)
        return bool(hit)
    finally:
        g.DeleteDC(dc)


def find_bundled_fonts(base_dir=None):
    """返回随程序分发的字体文件列表（base_dir/字体/ 下的 .otf .ttf .ttc）。目录不存在返回空列表。"""
    base = base_dir or app_base_dir()
    d = os.path.join(base, FONT_DIR_NAME)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(_FONT_EXTS))


def check_fonts(styles, used_names, base_dir=None):
    """检查本次实际用到的样式里的字体是否已安装。返回 (信息行列表, 缺失字体名列表)。

    缺失时不中止（与 Aegisub 行为一致：GDI 会回退到系统默认字体，位置会与正确字体不符），只给出明确提示。
    """
    infos, missing, seen = [], [], set()
    for sname in used_names:
        st = styles.get(sname)
        if st is None or st.fontname in seen:
            continue
        seen.add(st.fontname)
        if font_installed(st.fontname):
            infos.append("字体 %s：已安装（样式 %s）" % (st.fontname, sname))
        else:
            missing.append(st.fontname)
            infos.append("!! 字体 %s：本机未安装（样式 %s）。GDI 会回退到系统默认字体，"
                         "字宽与播放效果不符，坐标可能错位" % (st.fontname, sname))
    if missing:
        bundled = find_bundled_fonts(base_dir)
        if bundled:
            infos.append("   随程序提供的字体文件：" + "；".join(bundled))
            infos.append("   安装方法：右键字体文件 → 安装（或“为所有用户安装”），装完后重新运行本程序")
        else:
            expect = os.path.join(base_dir or app_base_dir(), FONT_DIR_NAME)
            infos.append("   未找到随程序提供的字体（预期位置：%s）。请安装上述字体，或在样式里改用本机已有的字体" % expect)
    return infos, missing


STYLE_FILE = "ass_style.json"
# 配置块 -> 它改的 ASS 样式名。注音块默认继承原文块的字体，所以通常只需要写 original。
STYLE_BLOCKS = {"original": "Default", "furigana": "Default-furigana", "translation": "Default_ZH"}
_COLOR_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
TOP_LEVEL_KEYS = ("furigana_scale", "keep_merged")
# 字段 -> (ASS 样式字段下标, 类型, 合法范围)。下标对应 Style: 行逗号分隔后的位置
_STYLE_KEYS = {"font": (1, "str", None), "size": (2, "num", (1, 500)), "color": (3, "color", None),
               "outline_color": (5, "color", None), "bold": (7, "bool", None), "outline": (16, "num", (0, 50)),
               "shadow": (17, "num", (0, 50)), "margin_v": (21, "int", (0, 2000))}


def _strip_json_comments(text):
    """去掉 // 行注释（字符串内的 // 不受影响）。与 docx_ruby._strip_json_comments 同一约定。"""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch == "/" and text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def rgb_to_ass(color):
    """'#RRGGBB' / 'RRGGBB' -> '&H00BBGGRR'（ASS 的颜色是蓝绿红顺序，用户直接写会反）。"""
    c = color.lstrip("#")
    return "&H00%s%s%s" % (c[4:6].upper(), c[2:4].upper(), c[0:2].upper())


def load_style_overrides(path=None, base_dir=None):
    """读取 ass_style.json，返回 ({样式名: {字段下标: 值字符串}}, 提示列表)。

    文件缺失、损坏、字段不合法都不会抛异常：对应字段回退为 ASS 文件里的原值，并在提示里说明。
    只有出现在配置里的字段才会覆盖；furigana_scale 单独处理，见 resolve_furigana_scale。
    """
    notes, out = [], {}
    p = path or os.path.join(base_dir or app_base_dir(), STYLE_FILE)
    if not os.path.isfile(p):
        return out, notes
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.loads(_strip_json_comments(f.read()))
        if not isinstance(data, dict):
            raise ValueError("顶层应为对象")
    except Exception as e:
        return out, ["读取 %s 失败，已忽略（使用文件内样式）：%s" % (STYLE_FILE, e)]
    for block, style_name in STYLE_BLOCKS.items():
        user = data.get(block)
        if user is None:
            continue
        if not isinstance(user, dict):
            notes.append("%s：%s 应为对象，已忽略" % (STYLE_FILE, block))
            continue
        for key, value in user.items():
            spec = _STYLE_KEYS.get(key)
            if spec is None:
                notes.append("%s：忽略未知字段 %s.%s" % (STYLE_FILE, block, key))
                continue
            idx, kind, rng = spec
            ok = True
            if kind == "str":
                ok = isinstance(value, str) and value.strip() != "" and "," not in value
                v = value.strip() if ok else None
            elif kind == "color":
                ok = isinstance(value, str) and bool(_COLOR_RE.match(value))
                v = rgb_to_ass(value) if ok else None
            elif kind == "bool":
                ok = isinstance(value, bool)
                v = "-1" if value else "0"
            else:
                ok = isinstance(value, (int, float)) and not isinstance(value, bool) and rng[0] <= value <= rng[1]
                v = (str(int(value)) if kind == "int" or float(value) == int(value) else fmt_num(value)) if ok else None
            if not ok:
                hint = {"str": "字体名（不含逗号）", "color": "#RRGGBB 形式的颜色（如 #FFFFFF）", "bool": "true 或 false"}.get(kind)
                if hint is None:
                    hint = "%s 到 %s 之间的数字" % rng
                notes.append("%s：%s.%s 应为 %s，已忽略" % (STYLE_FILE, block, key, hint))
                continue
            out.setdefault(style_name, {})[idx] = v
    # 顶层只认三个块名和 furigana_scale；其余（最常见的是把 size、font 直接写在最外层）一律提示，不能悄悄忽略
    for key in data:
        if key in STYLE_BLOCKS or key in TOP_LEVEL_KEYS:
            continue
        if key in _STYLE_KEYS:
            notes.append("%s：“%s”写在了最外层，已忽略。它必须放在 original（原文）、furigana（注音）或 "
                         "translation（译文）块里，例如 \"translation\": { \"%s\": ... }" % (STYLE_FILE, key, key))
        else:
            notes.append("%s：忽略未知的顶层项“%s”（只认 original、furigana、translation、furigana_scale、keep_merged）" % (STYLE_FILE, key))
    scale = data.get("furigana_scale")
    if scale is not None:
        if isinstance(scale, (int, float)) and not isinstance(scale, bool) and 0.1 <= scale <= 1.5:
            out["__furigana_scale__"] = float(scale)
        else:
            notes.append("%s：furigana_scale 应为 0.1 到 1.5 之间的数字，已忽略" % STYLE_FILE)
    keep = data.get("keep_merged")
    if keep is not None:
        if isinstance(keep, bool):
            out["__keep_merged__"] = keep
        else:
            notes.append("%s：keep_merged 应为 true 或 false，已忽略（按默认 false：成功生成特效版后删除 _merged.ass）" % STYLE_FILE)
    return out, notes


def apply_style_overrides(lines, overrides):
    """把 overrides 写进 [V4+ Styles] 里同名样式的对应字段。返回 (新行列表, 实际改动的样式名列表)。"""
    changed, out = [], list(lines)
    for i, ln in enumerate(out):
        if not ln.startswith("Style:"):
            continue
        parts = [p.strip() for p in ln.split(":", 1)[1].split(",")]
        ov = overrides.get(parts[0])
        if not ov or len(parts) < 23:
            continue
        for idx, v in ov.items():
            parts[idx] = v
        out[i] = "Style: " + ",".join(parts)
        changed.append(parts[0])
    return out, changed


def parse_time(s):
    """'0:00:08.33' / '00:00:08.333' -> 毫秒。"""
    h, m, rest = s.strip().split(":")
    sec, _, frac = rest.partition(".")
    return int(h) * 3600000 + int(m) * 60000 + int(sec) * 1000 + int((frac + "000")[:3])


def fmt_time(ms):
    """毫秒 -> 'H:MM:SS.cc'。Aegisub 保存时四舍五入到厘秒。"""
    cs = (int(ms) + 5) // 10
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return "%d:%02d:%02d.%02d" % (h, m, s, cs)


STYLE_FIELDS = ["name", "fontname", "fontsize", "primary", "secondary", "outline_c", "back", "bold", "italic",
                "underline", "strikeout", "scalex", "scaley", "spacing", "angle", "borderstyle", "outline",
                "shadow", "align", "margin_l", "margin_r", "margin_v", "encoding"]


def _num(s):
    f = float(s)
    return int(f) if f == int(f) else f


def parse_style(line):
    """'Style: a,b,...' -> NS；保留原始字段串以便写回。"""
    parts = [p.strip() for p in line.split(":", 1)[1].split(",")]
    st = NS(raw=parts, name=parts[0], fontname=parts[1], fontsize=float(parts[2]),
            bold=_num(parts[7]) != 0, italic=_num(parts[8]) != 0, underline=_num(parts[9]) != 0,
            strikeout=_num(parts[10]) != 0, scalex=float(parts[11]), scaley=float(parts[12]),
            spacing=float(parts[13]), outline=float(parts[16]), shadow=float(parts[17]),
            align=int(parts[18]), margin_l=int(parts[19]), margin_r=int(parts[20]),
            margin_v=int(parts[21]), encoding=int(parts[22]))
    st.margin_t = st.margin_b = st.margin_v
    return st


def furigana_style(st, scale=None):
    """karaskel.collect_head 里为每个非 furigana 样式生成 -furigana 样式：字号、描边、阴影按比例缩小（默认 0.5）。"""
    scale = FURIGANA_SCALE if scale is None else scale
    p = list(st.raw)
    p[0] = st.name + "-furigana"
    p[2] = fmt_num(st.fontsize * scale)
    p[16] = fmt_num(st.outline * scale)
    p[17] = fmt_num(st.shadow * scale)
    return parse_style("Style: " + ",".join(p))


def fmt_num(x):
    """与 Aegisub 写样式时的数字格式一致：整数不带小数点，其余去掉末尾 0。"""
    if float(x) == int(x):
        return str(int(x))
    return ("%.6f" % x).rstrip("0").rstrip(".")


K_TAG = re.compile(r"\{[^}]*\}")
K_INNER = re.compile(r"\\([kK][fo]?)(-?\d+)")


def parse_karaoke(text):
    """aegisub.parse_karaoke_data 的等价实现：返回音节列表，下标 0 是第一个 \\k 之前的文本（时长 0）。
    每个音节：text（含标签）、text_stripped（去掉 {...}）、start_time（相对行首，毫秒）、duration（毫秒）。"""
    syls = [NS(text="", start_time=0, duration=0, tag="")]
    pos, cur = 0, syls[0]
    elapsed = 0
    for m in K_TAG.finditer(text):
        plain = text[pos:m.start()]
        cur.text += plain
        block = m.group(0)
        km = K_INNER.search(block)
        if km:
            dur = int(km.group(2)) * 10
            cur = NS(text=block, start_time=elapsed, duration=dur, tag=km.group(1))
            elapsed += dur
            syls.append(cur)
        else:
            cur.text += block
        pos = m.end()
    cur.text += text[pos:]
    for s in syls:
        s.text_stripped = K_TAG.sub("", s.text)
    return syls


def preproc_text(line):
    """karaskel.preproc_line_text：拆音节、拆注音（furigana）。line 需要 text/start/end。"""
    kara = parse_karaoke(line.text)
    line.kara, line.furi = [], []
    line.text_stripped = ""
    worksyl = NS(furi=[], highlights=[])
    for i, syl in enumerate(kara):
        m = re.match(r"^([ \t]*)(.*?)([ \t]*)$", syl.text_stripped, re.S)
        prespace, syltext, postspace = m.groups()
        prefix = syltext[:1]
        if prefix not in ("#", "＃") and i > 0:
            line.kara.append(worksyl)
            worksyl = NS(furi=[], highlights=[])
        worksyl.highlights.append(NS(start_time=syl.start_time, duration=syl.duration))
        if "|" in syltext or "｜" in syltext:
            syltext = syltext.replace("｜", "|")
            maintext, _, furitext = syltext.partition("|")
            syltext = maintext
            pre = furitext[:1]
            if pre in ("!", "！"):
                isbreak, spill = True, False
            elif pre in ("<", "＜"):
                isbreak, spill = True, True
            else:
                isbreak, spill = False, False
            if isbreak:
                furitext = furitext[1:]
            furi = NS(start_time=syl.start_time, duration=syl.duration, text=furitext, text_stripped=furitext,
                      isbreak=isbreak, spillback=spill, tag=syl.tag, isfuri=True, prespace="", postspace="",
                      inline_fx="", i=len(line.kara))
            line.furi.append(furi)
            worksyl.furi.append(furi)
        if not hasattr(worksyl, "text") or prefix not in ("#", "＃"):
            line.text_stripped += prespace + syltext + postspace
            worksyl.text = syl.text
            worksyl.duration = syl.duration
            worksyl.start_time = syl.start_time
            worksyl.end_time = syl.start_time + syl.duration
            worksyl.tag = syl.tag
            worksyl.i = len(line.kara)
            worksyl.text_stripped = prespace + syltext + postspace
            worksyl.inline_fx = ""
            worksyl.text_spacestripped = syltext
            worksyl.prespace, worksyl.postspace = prespace, postspace
        else:
            worksyl.duration += syl.duration
            worksyl.end_time = syl.start_time + syl.duration
    line.kara.append(worksyl)
    for s in line.kara:
        s.end_time = s.start_time + s.duration
    for f in line.furi:
        f.end_time = f.start_time + f.duration


def preproc_size(line, styles, gdi):
    line.styleref = styles.get(line.style) or styles["__first__"]
    w, h = gdi.extents(line.styleref, line.text_stripped)
    line.width, line.height = w, h
    for syl in line.kara:
        syl.style = line.styleref
        syl.width, syl.height = gdi.extents(syl.style, syl.text_spacestripped)
        syl.prespacewidth = gdi.extents(syl.style, syl.prespace)[0]
        syl.postspacewidth = gdi.extents(syl.style, syl.postspace)[0]
    line.furistyle = styles.get(line.style + "-furigana")
    if line.furistyle:
        for f in line.furi:
            f.style = line.furistyle
            f.width, f.height = gdi.extents(f.style, f.text)
            f.prespacewidth = f.postspacewidth = 0


def layout_furigana(line):
    """karaskel.do_furigana_layout 的逐句移植。"""
    sentinel = NS(basewidth=0, furiwidth=0, syls=[], furi=[], spillback=False, left=0, right=0, rightspill=None)

    def new_group():
        return NS(basewidth=0, furiwidth=0, syls=[], furi=[], spillback=False, rightspill=None)

    groups = [sentinel]
    last_had_furi = False
    lg = new_group()
    for syl in line.kara:
        if (not syl.furi or syl.furi[0].isbreak or not last_had_furi) and lg.basewidth > 0:
            groups.append(lg)
            lg = new_group()
            last_had_furi = False
        lg.basewidth += syl.prespacewidth + syl.width + syl.postspacewidth
        lg.syls.append(syl)
        for f in syl.furi:
            lg.furiwidth += f.width
            lg.spillback = lg.spillback or f.spillback
            lg.furi.append(f)
            last_had_furi = True
    groups.append(lg)
    groups.append(sentinel)

    curx = 0
    for i in range(1, len(groups) - 1):
        lg, prev = groups[i], groups[i - 1]
        if lg.furiwidth == 0:
            lg.left = curx
            lg.right = lg.left + lg.basewidth
            if prev.rightspill is not None and prev.rightspill > 0:
                lg.leftspill = 0
                lg.rightspill = prev.rightspill - lg.basewidth
                prev.rightspill = 0
            curx += lg.basewidth
        elif lg.furiwidth <= lg.basewidth:
            if prev.rightspill is not None and prev.rightspill > 0:
                curx += prev.rightspill
                prev.rightspill = 0
            lg.left = curx
            lg.right = lg.left + lg.basewidth
            curx += lg.basewidth
            lg.leftspill = (lg.furiwidth - lg.basewidth) / 2
            lg.rightspill = lg.leftspill
        else:
            if prev.rightspill is not None and prev.rightspill > 0:
                curx += prev.rightspill
                prev.rightspill = 0
            if lg.spillback:
                lg.leftspill = (lg.furiwidth - lg.basewidth) / 2
                lg.rightspill = lg.leftspill
                lg.left = curx + lg.leftspill if prev.rightspill is not None else curx
            else:
                lg.leftspill = 0
                lg.rightspill = lg.furiwidth - lg.basewidth
                lg.left = curx
            lg.right = lg.left + lg.basewidth
            curx = lg.right

    seen = set()
    for lg in groups:
        if id(lg) in seen:
            continue
        seen.add(id(lg))
        curx = lg.left
        for syl in lg.syls:
            syl.left = curx + syl.prespacewidth
            syl.center = syl.left + syl.width / 2
            syl.right = syl.left + syl.width
            curx = syl.right + syl.postspacewidth
        if curx > line.width:
            line.width = curx
        if lg.furiwidth < lg.basewidth or lg.spillback:
            curx = lg.left + (lg.basewidth - lg.furiwidth) / 2
        else:
            curx = lg.left
        for f in lg.furi:
            f.left = curx
            f.center = f.left + f.width / 2
            f.right = f.left + f.width
            curx = f.right


def layout_basic(line):
    curx = 0
    for syl in line.kara:
        syl.left = curx + syl.prespacewidth
        syl.center = syl.left + syl.width / 2
        syl.right = syl.left + syl.width
        curx += syl.prespacewidth + syl.width + syl.postspacewidth


def preproc_pos(line, meta):
    (layout_furigana if line.furistyle else layout_basic)(line)
    st = line.styleref
    line.eff_l = line.margin_l if line.margin_l > 0 else st.margin_l
    line.eff_r = line.margin_r if line.margin_r > 0 else st.margin_r
    line.eff_t = line.margin_t if line.margin_t > 0 else st.margin_t
    line.eff_b = line.margin_b if line.margin_b > 0 else st.margin_v
    a = st.align
    if a in (1, 4, 7):
        line.left, line.halign = line.eff_l, "left"
    elif a in (2, 5, 8):
        line.left = (meta.res_x - line.eff_l - line.eff_r - line.width) / 2 + line.eff_l
        line.halign = "center"
    else:
        line.left, line.halign = meta.res_x - line.eff_r - line.width, "right"
    line.center = line.left + line.width / 2
    line.right = line.left + line.width
    line.x = {"left": line.left, "center": line.center, "right": line.right}[line.halign]
    if 1 <= a <= 3:
        line.bottom = meta.res_y - line.eff_b
        line.middle, line.top = line.bottom - line.height / 2, line.bottom - line.height
        line.y, line.valign = line.bottom, "bottom"
    elif 4 <= a <= 6:
        line.top = (meta.res_y - line.eff_t - line.eff_b - line.height) / 2 + line.eff_t
        line.middle, line.bottom = line.top + line.height / 2, line.top + line.height
        line.y, line.valign = line.middle, "middle"
    else:
        line.top = line.eff_t
        line.middle, line.bottom = line.top + line.height / 2, line.top + line.height
        line.y, line.valign = line.top, "top"


_SAFE_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Attribute, ast.Load,
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub, ast.UAdd, ast.Call)
_SAFE_FUNCS = {"floor": math.floor, "ceil": math.ceil, "abs": abs, "round": lambda x: math.floor(x + 0.5),
               "max": max, "min": min}


def eval_expr(expr, env):
    """求值模板里 !...! 的算术表达式；只允许数字、变量、syl./line. 属性、+ - * / % 和少数数学函数。"""
    expr = expr.replace("math.", "")
    tree = ast.parse(expr.strip(), mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODES):
            raise ValueError("不支持的表达式: " + expr)
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS):
            raise ValueError("不支持的函数: " + expr)
    return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, dict(_SAFE_FUNCS, **env))


def _fmt(v):
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return ("%.6g" % v)
    return str(v)


def run_text_template(tmpl, varctx, env):
    def var(m):
        name = m.group(1).lower()
        return _fmt(varctx[name]) if name in varctx else "$" + name
    out = re.sub(r"\$([A-Za-z_]+)", var, tmpl)
    return re.sub(r"!(.*?)!", lambda m: _fmt(eval_expr(m.group(1), env)), out)


def syl_ctx(line, syl, v):
    """set_ctx_syl：音节级变量。"""
    v.update(sstart=syl.start_time, send=syl.start_time + syl.duration, sdur=syl.duration, skdur=syl.duration / 10)
    v["start"], v["end"], v["dur"], v["kdur"] = v["sstart"], v["send"], v["sdur"], v["skdur"]
    v["mid"] = v["smid"] = syl.start_time + syl.duration / 2
    v["si"] = v["i"] = syl.i
    fl = math.floor
    v["sleft"] = fl(line.left + syl.left + 0.5)
    v["scenter"] = fl(line.left + syl.center + 0.5)
    v["sright"] = fl(line.left + syl.right + 0.5)
    v["swidth"] = fl(syl.width + 0.5)
    if getattr(syl, "isfuri", False):
        v["sbottom"] = v["ltop"]
        v["stop"] = fl(v["ltop"] - syl.height + 0.5)
        v["smiddle"] = fl(v["ltop"] - syl.height / 2 + 0.5)
    else:
        v["stop"], v["smiddle"], v["sbottom"] = v["ltop"], v["lmiddle"], v["lbottom"]
    v["sheight"] = syl.height
    v["sx"] = {"left": v["sleft"], "center": v["scenter"], "right": v["sright"]}[line.halign]
    v["sy"] = {"top": v["stop"], "middle": v["smiddle"], "bottom": v["sbottom"]}[line.valign]
    for a, b in (("left", "sleft"), ("center", "scenter"), ("right", "sright"), ("width", "swidth"),
                 ("top", "stop"), ("middle", "smiddle"), ("bottom", "sbottom"), ("height", "sheight"),
                 ("x", "sx"), ("y", "sy")):
        v[a] = v[b]


def line_ctx(line):
    fl = math.floor
    st = line.styleref
    return dict(layer=line.layer, lstart=line.start, lend=line.end, ldur=line.end - line.start,
                lmid=line.start + (line.end - line.start) / 2, style=line.style, actor=line.actor,
                margin_l=line.margin_l if line.margin_l > 0 else st.margin_l,
                margin_r=line.margin_r if line.margin_r > 0 else st.margin_r,
                margin_t=line.margin_t if line.margin_t > 0 else st.margin_v,
                margin_b=line.margin_b if line.margin_b > 0 else st.margin_v,
                margin_v=line.margin_t if line.margin_t > 0 else st.margin_v,
                syln=len(line.kara) - 1, li=line.index,
                lleft=fl(line.left + 0.5), lcenter=fl(line.left + line.width / 2 + 0.5),
                lright=fl(line.left + line.width + 0.5), lwidth=fl(line.width + 0.5),
                ltop=fl(line.top + 0.5), lmiddle=fl(line.middle + 0.5), lbottom=fl(line.bottom + 0.5),
                lheight=fl(line.height + 0.5), lx=fl(line.x + 0.5), ly=fl(line.y + 0.5))


SUPPORTED = {"template", "syl", "furi", "noblank", "all", "notext", "keeptags", "repeat", "loop", "fx"}


def parse_templates(events, stats):
    """从 Comment 行读出 template 定义。返回 {'syl': [...], 'furi': [...]}。"""
    out = {"syl": [], "furi": []}
    for ev in events:
        if not ev.comment:
            continue
        words = ev.effect.split()
        if not words or words[0].lower() != "template":
            continue
        mods = [w.lower() for w in words[1:]]
        bad = [w for w in mods if w not in SUPPORTED and not w.isdigit()]
        if bad:
            stats["unsupported"].append("%s: %s" % (ev.effect, ",".join(bad)))
            continue
        kind = "furi" if "furi" in mods else "syl"
        t = NS(t=ev.text, style=None if "all" in mods else ev.style, layer=ev.layer, noblank="noblank" in mods,
               addtext="notext" not in mods, keeptags="keeptags" in mods, loops=1, fx=None)
        if "repeat" in mods or "loop" in mods:
            k = mods.index("repeat" if "repeat" in mods else "loop")
            if k + 1 < len(mods) and mods[k + 1].isdigit():
                t.loops = int(mods[k + 1])
        if "fx" in mods:
            k = mods.index("fx")
            t.fx = words[k + 2] if k + 2 < len(words) else None
        out[kind].append(t)
    return out


def is_blank(syl):
    if syl.duration <= 0:
        return True
    return len(re.sub(r"[ \t\n\r　]", "", syl.text_stripped)) == 0


def parse_event(raw):
    kind, _, rest = raw.partition(": ")
    f = rest.split(",", 9)
    ev = NS(raw=raw, kind=kind, comment=(kind == "Comment"), layer=int(f[0]),
            start_s=fmt_time(parse_time(f[1])), end_s=fmt_time(parse_time(f[2])),
            start=parse_time(f[1]), end=parse_time(f[2]), style=f[3], actor=f[4], margin_l=int(f[5]),
            margin_r=int(f[6]), margin_t=int(f[7]), margin_b=int(f[7]), effect=f[8], text=f[9])
    return ev


def event_str(ev, kind=None, effect=None, text=None, layer=None, style=None):
    return "%s: %d,%s,%s,%s,%s,%d,%d,%d,%s,%s" % (
        kind or ev.kind, ev.layer if layer is None else layer, ev.start_s, ev.end_s, style or ev.style, ev.actor,
        ev.margin_l, ev.margin_r, ev.margin_t, ev.effect if effect is None else effect,
        ev.text if text is None else text)


ZH_STYLE_DEFAULT = "Default_ZH"
K_MARK = chr(92) + "k"          # 反斜杠 + k：卡拉OK标记 \k \kf \ko 的共同前缀


def retag_translation(lines, ev_start, zh_style, notes):
    """把「译文行」的样式从 Default 改成 zh_style（默认 Default_ZH）。

    注音软件写 _merged.ass 时先写全部第一语言（注音）行、再写全部第二语言（译文）行，两段行数相同、
    时间逐行相同（phonetic_tool.merge_ass_files）。因此只有同时满足下面三条才改，任何一条不满足就原样保留并记入 notes：
      1. 候选行 = Effect 为空、样式为 Default 的 Dialogue；
      2. 候选行数为偶数，且后半段与前半段的起止时间逐行相同；
      3. 前半段每行都带 \\k 标记，后半段每行都不带（交叉验证）。
    用户已经手动改过样式、或带有特效的行不在候选里，不会被动到。
    """
    if not zh_style:
        return lines
    cand = []
    for i in range(ev_start, len(lines)):
        ln = lines[i]
        if not ln.startswith("Dialogue:"):
            continue
        f = ln.split(": ", 1)[1].split(",", 9)
        if len(f) == 10 and f[3] == "Default" and f[8] == "":
            cand.append((i, f))
    if not cand:
        return lines
    n = len(cand)
    half = n // 2
    if n % 2:
        notes.append("译文样式未改：候选对白行数为奇数（%d），无法按前后两段配对" % n)
        return lines
    if any(cand[k][1][1] != cand[k + half][1][1] or cand[k][1][2] != cand[k + half][1][2] for k in range(half)):
        notes.append("译文样式未改：后半段与前半段的时间不是逐行相同")
        return lines
    if not all(K_MARK in f[9] for _, f in cand[:half]) or any(K_MARK in f[9] for _, f in cand[half:]):
        notes.append("译文样式未改：前半段不是全部带 \\k，或后半段含 \\k")
        return lines
    out = list(lines)
    for i, f in cand[half:]:
        f = list(f)
        f[3] = zh_style
        out[i] = "Dialogue: " + ",".join(f)
    return out


def convert(ass_text, gdi=None, zh_style=ZH_STYLE_DEFAULT, check_font=True, style_file=None, base_dir=None):
    """主函数：返回 (新文本, stats)。原文件保持不变。
    zh_style：译文行改用的样式名，默认 Default_ZH；传空字符串表示不改。
    check_font：检查样式字体是否已安装，结果放进 stats["fonts"] / stats["missing_fonts"]，不改变输出内容。
    style_file：ass_style.json 路径；省略则找 base_dir（默认本文件所在目录）下的 ass_style.json，找不到就不覆盖任何样式。"""
    gdi = gdi or GdiMetrics()
    nl = "\r\n" if "\r\n" in ass_text else "\n"
    bom = ass_text.startswith("﻿")
    lines = ass_text.lstrip("﻿").splitlines()
    notes = []
    for i, ln in enumerate(lines):
        if ln.startswith("Format: Layer"):
            lines = retag_translation(lines, i + 1, zh_style, notes)
            break
    # 自定义样式：必须在读取样式、计算字宽之前生效，否则坐标会与字体对不上
    overrides, style_notes = load_style_overrides(style_file, base_dir)
    notes.extend(style_notes)
    fscale = overrides.pop("__furigana_scale__", None)
    keep_merged = overrides.pop("__keep_merged__", False)
    lines, changed_styles = apply_style_overrides(lines, overrides)

    info, styles, order = {}, {}, []
    style_idx = []
    ev_start = None
    for i, ln in enumerate(lines):
        if ln.startswith("Style:"):
            st = parse_style(ln)
            styles[st.name] = st
            order.append(st.name)
            style_idx.append(i)
        elif ln.startswith("PlayResX:"):
            info["x"] = int(ln.split(":")[1])
        elif ln.startswith("PlayResY:"):
            info["y"] = int(ln.split(":")[1])
        elif ln.startswith("Format: Layer"):
            ev_start = i + 1
    if ev_start is None or not style_idx:
        raise ValueError("不是有效的 ASS 文件：缺少 Styles 或 Events")
    styles["__first__"] = styles[order[0]]
    meta = NS(res_x=info.get("x", 384), res_y=info.get("y", 288))

    # 为每个非 furigana 样式补一个 -furigana 样式（已存在则不动）
    new_styles = []
    for name in order:
        if "furigana" not in name and name + "-furigana" not in styles:
            fs = furigana_style(styles[name], fscale)
            styles[fs.name] = fs
            new_styles.append("Style: " + ",".join(fs.raw))

    stats = {"unsupported": [], "lines": 0, "fx_lines": 0, "notes": notes, "fonts": [], "missing_fonts": [],
             "keep_merged": keep_merged}
    head = lines[:ev_start]
    raw_events = [ln for ln in lines[ev_start:] if ln.strip()]
    events = [parse_event(r) for r in raw_events if r.startswith(("Dialogue:", "Comment:"))]
    templates = parse_templates(events, stats)

    # 字体检查：只看本次真正会参与字宽计算的样式（可被处理的对白行用到的样式，及其 -furigana 样式）
    if check_font:
        used = []
        for ev in events:
            if ((not ev.comment and ev.effect == "") or ("karaoke" in ev.effect.lower())) and ev.kind == "Dialogue":
                for s in (ev.style, ev.style + "-furigana"):
                    if s in styles and s not in used:
                        used.append(s)
        stats["fonts"], stats["missing_fonts"] = check_fonts(styles, used)

    out_events = []
    for idx, ev in enumerate(events):
        # kara-templater.lua:432 —— 无 Effect 的普通对白，或 Effect 含 karaoke 的行（含上次生成的 Comment/karaoke）
        is_kara_target = (not ev.comment and ev.effect == "") or ("karaoke" in ev.effect.lower())
        if ev.comment and ev.effect.lower().split()[:1] == ["template"]:
            out_events.append(event_str(ev))
            continue
        if ev.kind == "Dialogue" and ev.effect == "fx":
            continue                                           # 旧的生成行：脚本会先删掉再重算
        if not is_kara_target:
            out_events.append(event_str(ev))
            continue
        line = NS(**ev.__dict__)
        line.index = idx + 1
        preproc_text(line)
        preproc_size(line, styles, gdi)
        preproc_pos(line, meta)
        varctx = line_ctx(line)
        env = {}
        made = 0
        for kind, items in (("syl", line.kara), ("furi", line.furi)):
            for k, syl in enumerate(items):
                if kind == "syl" and k == 0 and not syl.text_stripped and False:
                    continue
                for t in templates[kind]:
                    if t.style is not None and t.style != line.style:
                        continue
                    if t.fx and t.fx != syl.inline_fx:
                        continue
                    if t.noblank and is_blank(syl):
                        continue
                    syl_ctx(line, syl, varctx)
                    env = {"syl": NS(**{a: getattr(syl, a) for a in ("start_time", "end_time", "duration", "left",
                                                                     "center", "right", "width", "height", "i")}),
                           "line": NS(start_time=line.start, end_time=line.end)}
                    for _ in range(t.loops):
                        text = run_text_template(t.t, varctx, dict(env, **varctx))
                        if t.keeptags:
                            text += syl.text
                        elif t.addtext:
                            text += syl.text_stripped
                        style = syl.style.name
                        out_events.append(event_str(ev, kind="Dialogue", effect="fx", text=text,
                                                    layer=t.layer, style=style))
                        made += 1
        if made:
            out_events.append(event_str(ev, kind="Comment", effect="karaoke"))
            # fx 行追加在最后，与 Aegisub 的 subs.append 行为一致：先收集再统一放末尾
            stats["lines"] += 1
            stats["fx_lines"] += made
        else:
            out_events.append(event_str(ev))
    # Aegisub 把所有生成的 fx 行追加到文件末尾，原行原地变 Comment：这里按同样顺序重排
    keep = [e for e in out_events if not (e.startswith("Dialogue:") and e.split(",", 9)[8] == "fx")]
    fx = [e for e in out_events if e.startswith("Dialogue:") and e.split(",", 9)[8] == "fx"]
    new_head = list(head)
    insert_at = style_idx[0]                              # Aegisub 把新生成的 -furigana 样式插在样式区最前面
    new_head[insert_at:insert_at] = new_styles
    text = nl.join(new_head + keep + fx) + nl
    return ("﻿" if bom else "") + text, stats


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="kara-templater 的 Python 复现（外部测试，未接入主程序）")
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?", help="输出文件；省略时在源文件旁生成 *_actual.ass")
    ap.add_argument("--zh-style", default=ZH_STYLE_DEFAULT,
                    help="译文行改用的样式名（默认 %s）；传 \"\" 表示不改" % ZH_STYLE_DEFAULT)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dst = a.dst
    if not dst:
        base, _ = os.path.splitext(a.src)
        dst = (base[:-7] if base.endswith("_merged") else base) + "_merged_actual.ass"
    with open(a.src, "r", encoding="utf-8-sig") as f:
        text = f.read()
    out, stats = convert(text, zh_style=a.zh_style)
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        f.write(out)
    print("已写出 %s：处理 %d 行，生成 fx 行 %d；不支持的模板 %d" % (dst, stats["lines"], stats["fx_lines"], len(stats["unsupported"])))
    for n in stats["notes"]:
        print("提示：" + n)
    for n in stats["fonts"]:
        print(n)
    for u in stats["unsupported"][:5]:
        print("不支持的模板：" + u)
    return 0


if __name__ == "__main__":
    sys.exit(main())




