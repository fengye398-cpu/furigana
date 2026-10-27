"""TXT 文件注音：输出 {原名}_ruby.docx 和 {原名}_ruby.html。

独立于 SRT 流程，不修改输入文件。
"""
import os
import re
from html import escape as _html_escape

import docx_ruby

# 编码回退：先 UTF-8，失败后按语言模式回退；英语不回退
_FALLBACK_ENCODING = {"japanese": "cp932", "chinese": "gbk"}


def read_txt_lines(path, phonetic_mode, log):
    """读取 TXT，返回保留空行的行列表。"""
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        text = raw.decode('utf-8-sig')
        used = 'utf-8'
    except UnicodeDecodeError:
        fallback = _FALLBACK_ENCODING.get(phonetic_mode)
        if not fallback:
            raise Exception("TXT 文件不是 UTF-8 编码，请另存为 UTF-8 后重试。")
        try:
            text = raw.decode(fallback)
            used = fallback
        except UnicodeDecodeError:
            raise Exception(f"TXT 文件既不是 UTF-8 也不是 {fallback} 编码，请另存为 UTF-8 后重试。")
    log(f"TXT 文件编码：{used}")
    lines = [line.strip() for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return lines


# ---------------------------------------------------------------- 双语 TXT：奇数行时定位缺口
# 只用于“非空行数为奇数”这一已确定错位的情形，给出疑似位置；偶数行不检查。
# 分类宁可判“无法判断”也不乱判。简体专用字都经 jp_dict.sqlite 核对：词典 surface 里 0 条（日语不用）。
_KANA_RE = re.compile(r'[ぁ-ゖァ-ヺー]')
_HAN_RE = re.compile(r'[一-鿿]')
_SIMP_ONLY = set("这们说吗还为对请让见呢谁时过样应边别从关开问间头现发实气总话认难办电书东车马鸟")
_PREVIEW = 18
_NL = "\n"


def _classify(text, phonetic_mode):
    """返回 'src'（原文）/ 'trans'（译文）/ '?'（无法判断）。"""
    if phonetic_mode == "english":                     # 英语原文 + 中文译文：含汉字即译文
        return 'trans' if _HAN_RE.search(text) else 'src'
    if phonetic_mode == "chinese":                     # 中文原文 + 英文译文：含汉字即原文
        return 'src' if _HAN_RE.search(text) else 'trans'
    # 日语原文 + 中文译文：假名 → 原文；简体专用字或全角逗号 → 译文；两者同时出现或都没有 → 无法判断
    kana = bool(_KANA_RE.search(text))
    zh = '，' in text or any(c in _SIMP_ONLY for c in text)
    if kana and zh:
        return '?'
    if zh:
        return 'trans'
    if kana:
        return 'src'
    return '?'


def _preview(text):
    return text if len(text) <= _PREVIEW else text[:_PREVIEW] + '…'


def _show(rows):
    return _NL.join(f"    第 {n} 行：{_preview(t)}" for n, t in rows)


def _locate_gap(kept, phonetic_mode, raw_lines):
    """kept: [(行号, 文本)] 非空行；raw_lines: 含空行的原始行。返回疑似缺口的描述（带“疑似”）。"""
    # A 层：按空行分块，第一个行数不是 2 的块
    blocks, cur = [], []
    for n, text in enumerate(raw_lines, 1):
        if text:
            cur.append((n, text))
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    # 只有“恰好一个块不是 2 行，其余块全是 2 行”时才采信：空行不规则（有的对之间有、有的没有）时
    # 块的边界不可靠，指出的位置会误导，交给 B 层
    odd = [b for b in blocks if len(b) != 2]
    if len(blocks) > 1 and len(odd) == 1:
        b = odd[0]
        first, last = b[0][0], b[-1][0]
        span = f"第 {first} 行" if first == last else f"第 {first}~{last} 行"
        return f"按空行分块，{span}这一块有 {len(b)} 行（应为 2 行）：{_NL}{_show(b[:4])}"
    # B 层：找“变点”。缺（或多）一行译文后，缺口之前的行按“偶数位是原文”对齐，之后按“奇数位是原文”对齐。
    # 枚举所有切分点 p，选两段不一致行数之和最少的；零星误判（如译文里夹着假名）不会改变最优切分点。
    cl = [_classify(t, phonetic_mode) for _, t in kept]
    n = len(kept)
    if all(c == '?' for c in cl):
        return "无法确定缺口位置（没有可以自动判断原文/译文的行），请对照原文件检查每一对。"
    bad0 = [0 if c == '?' or c == ('src' if k % 2 == 0 else 'trans') else 1 for k, c in enumerate(cl)]
    bad1 = [0 if c == '?' or c == ('trans' if k % 2 == 0 else 'src') else 1 for k, c in enumerate(cl)]
    pre0 = [0]
    for b in bad0:
        pre0.append(pre0[-1] + b)
    suf1 = [0] * (n + 1)
    for k in range(n - 1, -1, -1):
        suf1[k] = suf1[k + 1] + bad1[k]
    cost = [pre0[q] + suf1[q] for q in range(n + 1)]
    best = min(cost)
    ties = [q for q, c in enumerate(cost) if c == best]
    lo, hi = ties[0], ties[-1]          # 缺口落在 kept[lo-1] 与 kept[hi] 之间（q 行已按方式0对齐）
    if lo >= n:
        # 能判断的行全部按方式0对得上：缺口只可能在末尾
        return f"能判断的行都对得上，疑似最后一行缺了译文：{_NL}{_show(kept[-2:])}"
    first = kept[lo - 1][0] if lo >= 1 else kept[0][0]
    last = kept[min(hi, n - 1)][0]
    span = f"第 {first}~{last} 行之间" if first != last else f"第 {first} 行附近"
    if hi - lo + 1 > 6:                  # 范围宽时只预览两端，避免刷屏
        rows = kept[max(lo - 1, 0):lo + 1] + kept[max(hi - 1, 0):hi + 1]
        note = f"（范围较宽，共 {last - first + 1} 行，只预览两端）"
    else:
        rows = kept[max(lo - 1, 0):hi + 1]
        note = ""
    if best:
        note += f"（另有 {best} 行与整体规律不符，可能是译文里夹着日语或原文里夹着中文）"
    return f"{span}疑似缺了译文或多了一行{note}：{_NL}{_show(rows)}"


def _unclear_note(kept, phonetic_mode):
    """列出无法判断的行（主要出现在日语模式）。"""
    bad = [n for n, t in kept if _classify(t, phonetic_mode) == '?']
    if not bad:
        return ""
    more = f" 等共 {len(bad)} 行" if len(bad) > 15 else ""
    return (f"{_NL}另有无法自动判断原文/译文的行（缺口可能就在它们附近）：第 "
            + "、".join(str(n) for n in bad[:15]) + f" 行{more}。")


_HTML_DEFAULT_H2 = '<h2>注音文本</h2>'


def _set_html_title(html_path, title, log):
    """把页面标题“注音文本”换成输入文件名，并补上 <title>。
    只改 TXT 流程生成的文件，不动 save_ruby_html（SRT 也在用）。
    找不到固定的标题标记（save_ruby_html 以后被改过）时保持原样，只记日志。"""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if content.count(_HTML_DEFAULT_H2) != 1 or '<meta charset="UTF-8">' not in content:
        log("HTML 标题未替换：没有找到预期的标题标记，保持原样")
        return
    safe = _html_escape(title, quote=False)
    content = content.replace(_HTML_DEFAULT_H2, f'<h2>{safe}</h2>')
    title_tag = _NL + '    <title>' + safe + '</title>'
    content = content.replace('<meta charset="UTF-8">', '<meta charset="UTF-8">' + title_tag, 1)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)


def process_txt(processor, input_path, output_dir, phonetic_mode, annotate, replacements):
    """处理 TXT 文件。

    annotate: 函数，接收一行文本，返回 (ruby_html, 计数)
    replacements: [(旧, 新), ...] 替换规则（与 SRT 流程相同）
    返回 (docx_path, html_path)
    """
    log = processor.log
    if not os.path.exists(input_path):
        raise Exception(f"输入文件 {input_path} 不存在！")
    output_dir = output_dir or os.path.dirname(input_path)
    os.makedirs(output_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(input_path))[0]

    docx_path = os.path.join(output_dir, f'{name}_ruby.docx')
    html_path = os.path.join(output_dir, f'{name}_ruby.html')
    tmp_ruby = os.path.join(output_dir, f'{name}_txt_ruby_tmp.txt')
    tmp_modi = os.path.join(output_dir, f'{name}_txt_ruby_modi_tmp.txt')
    tmp_trans = os.path.join(output_dir, f'{name}_txt_trans_tmp.txt')

    processor.update_progress(10, "读取 TXT 文件...")
    lines = read_txt_lines(input_path, phonetic_mode, log)
    if not any(lines):
        raise Exception("TXT 文件内容为空。")

    # 行号（从 1 起，含空行），用于 AI 清单的出处
    line_nos = list(range(1, len(lines) + 1))
    trans_lines = None
    if docx_ruby.load_txt_bilingual():
        # 双语：丢弃空行后两行一对，第一行原文、第二行译文；只有原文行注音
        kept = [(n, line) for n, line in zip(line_nos, lines) if line]
        if len(kept) % 2:
            raise Exception(f"双语 TXT 的非空行数为奇数（{len(kept)} 行），无法两行一对。{_NL}"
                            + _locate_gap(kept, phonetic_mode, lines)
                            + _unclear_note(kept, phonetic_mode)
                            + f"{_NL}请补上缺失的译文或删掉多余的行后重试（每行必须是 1 行原文 + 1 行译文）。")
        line_nos = [n for n, _ in kept[0::2]]
        lines = [line for _, line in kept[0::2]]
        trans_lines = [line for _, line in kept[1::2]]
        log(f"TXT 双语模式：共 {len(lines)} 对（原文行 + 译文行），译文不注音")

    processor.update_progress(30, "注音处理...")
    ruby_lines, total = [], 0
    for line in lines:
        if line:
            html, count = annotate(line)
            total += count
            ruby_lines.append(html)
        else:
            ruby_lines.append('')
    log(f"TXT 注音完成，共标注 {total} 处")

    # 可选：AI 复核（仅日语模式；空行不参与，复核后按原位置放回；失败保持原结果）
    if getattr(processor, "ai_review", False) and phonetic_mode == "japanese":
        try:
            import ai_reviewer
            idx = [i for i, line in enumerate(lines) if line]
            # 出处用原文件里的行号（含空行），方便在编辑器里定位
            new = ai_reviewer.apply_to_lines(processor, [lines[i] for i in idx],
                                             [ruby_lines[i] for i in idx], output_dir, name, log=log,
                                             locs=["TXT 第 %d 行" % line_nos[i] for i in idx],
                                             source=os.path.basename(input_path))
            for i, html in zip(idx, new):
                ruby_lines[i] = html
        except Exception as e:
            log(f"AI注音失败（已忽略）: {e}")

    try:
        processor.update_progress(60, "应用替换规则...")
        with open(tmp_ruby, 'w', encoding='utf-8') as f:
            f.write('\n'.join(ruby_lines))
        processor.replace_multiple_text(tmp_ruby, tmp_modi, replacements)
        with open(tmp_modi, 'r', encoding='utf-8-sig') as f:
            modi_lines = f.read().split('\n')

        want_docx, want_html = docx_ruby.load_txt_output(log)
        outputs = []
        processor.update_progress(80, "生成 docx / HTML...")
        if want_docx:
            try:
                docx_ruby.build_docx(modi_lines, phonetic_mode, docx_path, log, trans_lines=trans_lines, title=name)
            except PermissionError:
                raise Exception(f"无法写入 {docx_path}，请先关闭正在打开该文件的 Word。")
            outputs.append(docx_path)
        if want_html:
            try:
                if trans_lines is None:
                    processor.save_ruby_html(tmp_modi, html_path, 'single')
                else:
                    # save_ruby_html 会把译文原样放进 HTML，这里先转义，避免译文里的 < > & 破坏页面
                    with open(tmp_trans, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(_html_escape(t, quote=False) for t in trans_lines))
                    processor.save_ruby_html(tmp_modi, html_path, 'double', tmp_trans)
                _set_html_title(html_path, name, log)
            except PermissionError:
                raise Exception(f"无法写入 {html_path}，请先关闭正在打开该文件的程序。")
            outputs.append(html_path)
    finally:
        for tmp in (tmp_ruby, tmp_modi, tmp_trans):
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    processor.update_progress(100, "处理完成！")
    log("处理完成！保留输出文件:")
    for p in outputs:
        log(f"- {p}")
    log(f"✅结果文件目录: {output_dir}")
    return docx_path if want_docx else None, html_path if want_html else None
