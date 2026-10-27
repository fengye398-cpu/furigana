"""生成带注音（Word 原生 Ruby）的 docx 文件。

只依赖标准库。输入是替换规则处理后的 {\\k1} 格式文本行，例如：
    {\\k1}漢|<かん{\\k1}字|<じ{\\k1}は
"""
import json
import os
import re
import sys
import zipfile
from xml.sax.saxutils import escape

# 与 SubtitleProcessor.convert_k1_to_ruby_html 使用同一条正则，保证 HTML 与 docx 理解一致
_K1_PATTERN = re.compile(r'(?:\{\\k1\})?([^|{\s]+)\|<([^{]+)\{\\k1\}')
_XML_INVALID = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

STYLE_FILE = "docx_style.json"
_ALIGNS = {"center", "distributeLetter", "distributeSpace", "left", "right", "rightVertical"}
_COLOR_RE = re.compile(r'^[0-9A-Fa-f]{6}$')

# 各语言模式的默认样式，与 HTML 输出一致：原文 12pt（16px）、注音约 8.5pt（0.7em）、注音灰色 999999。
# 字号单位为磅；颜色为 6 位十六进制，空字符串表示自动色。
# 日语注音比基字宽，用 distributeSpace；中文/英语注音较窄，用 center，
# 否则 Word 会把注音字母均匀拉开，字母之间出现空隙。
# 译文（TXT 双语）：trans_* 默认与 HTML 的 translationStyle 一致（595959、13px≈9.75pt，Word 字号只能是 0.5 磅的整数倍，取 10pt），字体留空同原文。
DEFAULT_STYLE = {
    "japanese": {"base_font": "Yu Mincho", "ruby_font": "Yu Mincho", "east_font": "",
                 "base_size": 12, "ruby_size": 8.5, "base_color": "", "ruby_color": "999999",
                 "ruby_align": "distributeSpace", "ruby_raise": "auto",
                 "line_spacing": 1.0, "blank_between": True,
                 "trans_font": "", "trans_size": 10, "trans_color": "595959"},
    "chinese": {"base_font": "宋体", "ruby_font": "宋体", "east_font": "",
                "base_size": 12, "ruby_size": 8.5, "base_color": "", "ruby_color": "999999",
                "ruby_align": "center", "ruby_raise": "auto",
                "line_spacing": 1.0, "blank_between": True,
                 "trans_font": "", "trans_size": 10, "trans_color": "595959"},
    "english": {"base_font": "Times New Roman", "ruby_font": "Cambria", "east_font": "宋体",
                "base_size": 12, "ruby_size": 8.5, "base_color": "", "ruby_color": "999999",
                "ruby_align": "center", "ruby_raise": "auto",
                "line_spacing": 1.0, "blank_between": True,
                 "trans_font": "", "trans_size": 10, "trans_color": "595959"},
}
_LANG_IDS = {"japanese": ("ja-JP", "ja-JP"), "chinese": ("zh-CN", "zh-CN"),
             "english": ("en-US", "zh-CN")}


def _style_file_candidates():
    # 与 jp_dict.sqlite 的查找顺序一致：exe（或脚本）同目录 → 同目录下的 dict 子目录 → 打包临时目录
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [os.path.join(base_dir, STYLE_FILE),
             os.path.join(base_dir, "dict", STYLE_FILE)]
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        paths.append(os.path.join(meipass, STYLE_FILE))
    return paths


def _style_file_path():
    """返回第一个存在的配置文件路径；都不存在返回 None。"""
    for path in _style_file_candidates():
        if os.path.exists(path):
            return path
    return None


def _strip_json_comments(text):
    """去掉 // 行注释，使配置文件可以写说明。字符串内的 // 不会被误删。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch == '/' and text.startswith('//', i):
            while i < n and text[i] != '\n':
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _is_number(value, low, high):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def load_style(lang, log=None):
    """返回指定语言的样式字典（默认值 + docx_style.json 的覆盖）。
    配置文件缺失或某项不合法时回退默认值，只记日志，不抛异常。"""
    note = log or (lambda msg: None)
    if lang not in DEFAULT_STYLE:
        lang = "japanese"
    style = dict(DEFAULT_STYLE[lang])
    path = _style_file_path()
    if path is None:
        note(f"未找到 {STYLE_FILE}，使用默认样式")
        return style
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            user = json.loads(_strip_json_comments(f.read())).get(lang, {})
        if not isinstance(user, dict):
            raise ValueError(f"“{lang}”应为对象")
    except Exception as e:
        note(f"读取 {STYLE_FILE} 失败，使用默认样式：{e}")
        return style
    note(f"已读取 {STYLE_FILE}: {path}")
    for key, value in user.items():
        if key not in style:
            note(f"{STYLE_FILE}：忽略未知字段 {lang}.{key}")
        elif key in ("base_size", "ruby_size", "trans_size"):
            if _is_number(value, 1, 72):
                style[key] = value
            else:
                note(f"{STYLE_FILE}：{lang}.{key} 应为 1-72 之间的数字，已用默认值")
        elif key in ("base_color", "ruby_color", "trans_color"):
            if value == "" or (isinstance(value, str) and _COLOR_RE.match(value)):
                style[key] = value.upper()
            else:
                note(f"{STYLE_FILE}：{lang}.{key} 应为 6 位十六进制颜色（如 666666），已用默认值")
        elif key == "ruby_align":
            if value in _ALIGNS:
                style[key] = value
            else:
                note(f"{STYLE_FILE}：{lang}.ruby_align 可选 {sorted(_ALIGNS)}，已用默认值")
        elif key == "ruby_raise":
            if value == "auto" or _is_number(value, 0.5, 36):
                style[key] = value
            else:
                note(f"{STYLE_FILE}：{lang}.ruby_raise 应为 \"auto\" 或 0.5-36 之间的数字，已用默认值")
        elif key == "line_spacing":
            if _is_number(value, 0.5, 3):
                style[key] = value
            else:
                note(f"{STYLE_FILE}：{lang}.line_spacing 应为 0.5-3 之间的数字，已用默认值")
        elif key == "blank_between":
            if isinstance(value, bool):
                style[key] = value
            else:
                note(f"{STYLE_FILE}：{lang}.blank_between 应为 true 或 false，已用默认值")
        else:  # 字体名
            if isinstance(value, str) and not _XML_INVALID.search(value):
                style[key] = value.strip()
            else:
                note(f"{STYLE_FILE}：{lang}.{key} 应为字体名字符串，已用默认值")
    return style


def _read_app_config():
    """读 exe（或脚本）同目录的 ai_config.json；缺失或损坏返回空字典。"""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base_dir, "ai_config.json"), 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_txt_bilingual():
    """TXT 是否按双语处理（原文行、译文行交替），来自 ai_config.json 的 txt_bilingual，默认 False。"""
    value = _read_app_config().get("txt_bilingual", False)
    return value if isinstance(value, bool) else False


def load_txt_output(log=None):
    """返回 (输出docx, 输出html)，来自 ai_config.json 的 txt_docx / txt_html（“设置”窗口里勾选）。
    文件缺失、读取失败、字段不合法都用默认值 (True, False)；两项都为 false 时警告并回退为只输出 docx。
    直接读 json，不 import ai_reviewer，未使用 AI 的用户不会加载它。"""
    note = log or (lambda msg: None)
    data = _read_app_config()
    want_docx, want_html = data.get("txt_docx", True), data.get("txt_html", False)
    if not isinstance(want_docx, bool):
        want_docx = True
    if not isinstance(want_html, bool):
        want_html = False
    if not want_docx and not want_html:
        note("TXT 导出的 docx 和 html 都未勾选，已改为只输出 docx")
        return True, False
    return want_docx, want_html


def _build_cfg(lang, style):
    """把样式字典换算成生成 XML 所需的配置（字号转半磅）。"""
    lid, east_lang = _LANG_IDS.get(lang, _LANG_IDS["japanese"])
    base_font = style["base_font"] or DEFAULT_STYLE[lang]["base_font"]
    ruby_font = style["ruby_font"] or base_font
    east = style["east_font"] or base_font
    base_hps = int(round(style["base_size"] * 2))
    ruby_hps = int(round(style["ruby_size"] * 2))
    return {
        "base": base_font, "ruby": ruby_font, "east": east,
        # 日语/中文：汉字与注音都按各自字体显示；英语：东亚字符用 east_font
        "ruby_east": east if lang == "english" else ruby_font,
        "base_hps": base_hps,
        "ruby_hps": ruby_hps,
        # 注音抬升高度（半磅）。auto：以原文 12pt、注音 6pt 时的 22 为基准，注音每多 1 个半磅就多抬 0.5，
        # 避免注音变大后与原文贴在一起（原文 12pt、注音 8.5pt 时为 24）。这是估算值
        "raise": (max(1, int(round(base_hps * 22 / 24 + (ruby_hps - 12) * 0.5)))
                  if style["ruby_raise"] == "auto" else int(round(style["ruby_raise"] * 2))),
        "line": int(round(240 * style["line_spacing"])),  # 多倍行距：240 为单倍
        "blank_between": style["blank_between"],
        # 译文：字体留空同原文；指定字体时中日文字符也用它
        "trans_font": style["trans_font"] or base_font,
        "trans_east": style["trans_font"] or east,
        "trans_hps": int(round(style["trans_size"] * 2)),
        "trans_color": style["trans_color"],
        "base_color": style["base_color"], "ruby_color": style["ruby_color"],
        "align": style["ruby_align"], "lid": lid, "east_lang": east_lang,
    }


def parse_k1_line(line):
    """把一行 {\\k1} 格式文本拆成 [(基字, 注音或None), ...]。"""
    segments = []
    pos = 0
    for m in _K1_PATTERN.finditer(line):
        if m.start() > pos:
            plain = line[pos:m.start()].replace('{\\k1}', '')
            if plain:
                segments.append((plain, None))
        segments.append((m.group(1), m.group(2)))
        pos = m.end()
    tail = line[pos:].replace('{\\k1}', '')
    if tail:
        segments.append((tail, None))
    return segments


def _clean(text):
    return escape(_XML_INVALID.sub('', text))


def _attr(text):
    return escape(text, {'"': '&quot;'})


def _rpr(font, east, size, color):
    font, east = _attr(font), _attr(east)
    color_xml = f'<w:color w:val="{color}"/>' if color else ''
    # rPr 子元素顺序：rFonts、color、sz、szCs
    return (f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" '
            f'w:eastAsia="{east}" w:cs="{font}"/>{color_xml}'
            f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>')


def _run(text, cfg):
    return (f'<w:r>{_rpr(cfg["base"], cfg["east"], cfg["base_hps"], cfg["base_color"])}'
            f'<w:t xml:space="preserve">{_clean(text)}</w:t></w:r>')


def _ruby(base, ruby, cfg):
    # 子元素顺序必须是 rubyPr、rt、rubyBase，否则 Word 会提示文件损坏
    return (
        '<w:r><w:ruby>'
        f'<w:rubyPr><w:rubyAlign w:val="{cfg["align"]}"/>'
        f'<w:hps w:val="{cfg["ruby_hps"]}"/><w:hpsRaise w:val="{cfg["raise"]}"/>'
        f'<w:hpsBaseText w:val="{cfg["base_hps"]}"/><w:lid w:val="{cfg["lid"]}"/></w:rubyPr>'
        f'<w:rt><w:r>{_rpr(cfg["ruby"], cfg["ruby_east"], cfg["ruby_hps"], cfg["ruby_color"])}'
        f'<w:t xml:space="preserve">{_clean(ruby)}</w:t></w:r></w:rt>'
        f'<w:rubyBase><w:r>{_rpr(cfg["base"], cfg["east"], cfg["base_hps"], cfg["base_color"])}'
        f'<w:t xml:space="preserve">{_clean(base)}</w:t></w:r></w:rubyBase>'
        '</w:ruby></w:r>'
    )


def _paragraph(line, cfg):
    # 空行对应空段落
    runs = []
    for base, ruby in parse_k1_line(line):
        runs.append(_ruby(base, ruby, cfg) if ruby else _run(base, cfg))
    return (f'<w:p><w:pPr><w:spacing w:before="0" w:after="0" '
            f'w:line="{cfg["line"]}" w:lineRule="auto"/></w:pPr>' + ''.join(runs) + '</w:p>')


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '</Types>'
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)

_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '</Relationships>'
)

_W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _styles_xml(cfg):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles {_W_NS}><w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:rFonts w:ascii="{_attr(cfg["base"])}" w:hAnsi="{_attr(cfg["base"])}" '
        f'w:eastAsia="{_attr(cfg["east"])}" w:cs="{_attr(cfg["base"])}"/>'
        f'<w:sz w:val="{cfg["base_hps"]}"/><w:szCs w:val="{cfg["base_hps"]}"/>'
        f'<w:lang w:val="{cfg["lid"]}" w:eastAsia="{cfg["east_lang"]}"/>'
        '</w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style></w:styles>'
    )


def _trans_paragraph(text, cfg):
    """译文段落：样式来自 trans_font / trans_size / trans_color，不带注音。"""
    run = (f'<w:r>{_rpr(cfg["trans_font"], cfg["trans_east"], cfg["trans_hps"], cfg["trans_color"])}'
           f'<w:t xml:space="preserve">{_clean(text)}</w:t></w:r>')
    return (f'<w:p><w:pPr><w:spacing w:before="0" w:after="0" '
            f'w:line="{cfg["line"]}" w:lineRule="auto"/></w:pPr>{run}</w:p>')


TITLE_EXTRA_HPS = 8   # 标题比正文大 4 磅（单位半磅）


def _title_paragraph(text, cfg):
    """标题段落：原文字体、比正文大 4 磅、加粗、居中、自动色。rPr 顺序：rFonts、b、sz、szCs。"""
    font, east = _attr(cfg["base"]), _attr(cfg["east"])
    size = cfg["base_hps"] + TITLE_EXTRA_HPS
    rpr = (f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{east}" w:cs="{font}"/>'
           f'<w:b/><w:bCs/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>')
    return (f'<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="{cfg["line"]}" w:lineRule="auto"/>'
            f'<w:jc w:val="center"/></w:pPr>'
            f'<w:r>{rpr}<w:t xml:space="preserve">{_clean(text)}</w:t></w:r></w:p>')


def build_docx(lines, lang, out_path, log=None, blank_between=None, trans_lines=None, title=None):
    """把 {\\k1} 格式的行列表写成 docx。lang 取 japanese / chinese / english。
    样式来自 DEFAULT_STYLE，并被 docx_style.json（如存在）覆盖。
    blank_between 为 True 时，每个非空行后追加一个空段落（注音行->空行->注音行），
    输入中原有的空行照样保留，最后一行之后不追加。None 表示读取配置（默认 True）。
    trans_lines 不为 None 时为双语：与 lines 逐行配对，译文紧跟在对应的注音行下面（不带注音），
    只处理 min(len(lines), len(trans_lines)) 对。
    title 不为空时，正文最前面加一个居中加粗的标题段落和一个空段落；None 或空串则与不传时完全相同。"""
    if lang not in DEFAULT_STYLE:
        lang = "japanese"
    cfg = _build_cfg(lang, load_style(lang, log))
    if blank_between is None:
        blank_between = cfg["blank_between"]
    if trans_lines is not None:
        lines = lines[:len(trans_lines)]
    paragraphs = []
    if title and title.strip():
        paragraphs.append(_title_paragraph(title.strip(), cfg))
        paragraphs.append(_paragraph('', cfg))
    last = max((i for i, line in enumerate(lines) if line.strip()), default=-1)
    for i, line in enumerate(lines):
        paragraphs.append(_paragraph(line, cfg))
        if trans_lines is not None:
            paragraphs.append(_trans_paragraph(trans_lines[i], cfg))
        if blank_between and line.strip() and i != last:
            paragraphs.append(_paragraph('', cfg))
    body = ''.join(paragraphs)
    if not body:
        body = _paragraph('', cfg)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_W_NS}><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
        '</w:body></w:document>'
    )
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', _CONTENT_TYPES)
        z.writestr('_rels/.rels', _RELS)
        z.writestr('word/document.xml', document)
        z.writestr('word/_rels/document.xml.rels', _DOC_RELS)
        z.writestr('word/styles.xml', _styles_xml(cfg))
