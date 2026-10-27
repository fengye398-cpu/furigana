# -*- coding: utf-8 -*-
"""AI 复核注音（第五层，后处理）。

设计见 ARCHITECTURE.md 第 7 节。本模块只做三件事：
  1. 复现前四层的推演（replay），找出满足 T4 的歧义词；
  2. 请 OpenAI 兼容接口在“候选读音内”选一个（只接受逐字等于候选的答案）；
  3. 产出改动结果（模式 B 采用 / 模式 A 仅建议）和改动清单。
任何失败都只会让该句保持前四层的结果，不会抛到调用方。
不修改 SubtitleProcessor 的任何方法，开关关闭时调用方一行都不会执行到这里。
"""
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor

PROMPT_VERSION = "v1"
C1_NOUN_KINDS = ("普通名詞", "固有名詞")     # 两来源一致时只放行这两类名词（数詞、代名詞等不放）
FREE_PROMPT_VERSION = "free-v2"      # v2：一词一请求（v1 是每句多词，已弃用，缓存不共用）
CONFIG_FILE = "ai_config.json"
CACHE_FILE = "ai_cache.json"
VETO_FILE = "ai_veto.txt"

DEFAULT_CONFIG = {
    "enabled": False,
    "mode": "adopt",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-flash",
    "thinking_off": True,
    "concurrency": 16,
    "timeout_seconds": 60,
    "max_retries": 2,
    "fuse_after_failures": 10,
    "ai_context_lines": 0,
    "free_reading": False,
    "rule_context_chars": 3,            # 已不再被预检读取，仅为兼容旧配置文件而保留
    "rule_pre_min": 2,                  # 规则草案最少取多少字前文（0~10）
    "rule_suf_min": 2,                  # 规则草案最少取多少字后文（0~10）
    "veto_context_chars": 2,
    "txt_docx": True,                   # TXT 注音是否输出 .docx（与 AI 无关，由 docx_ruby.load_txt_output 读取）
    "txt_html": False,                  # TXT 注音是否输出 .html
    "txt_bilingual": False,             # TXT 是否按双语处理（原文行、译文行交替，译文不注音）
    "skip_words": ["僕", "俺", "私", "君", "今日", "明日", "大丈夫", "好き", "何"],
}

SYSTEM_PROMPT = (
    "你是日语读音校对助手。根据整句语境，为每个给定的词从候选读音中选出最合适的一个。\n"
    "规则：\n"
    "1. 只能从该词的候选里选，必须逐字照抄，不得自造。\n"
    "2. 无法判断时，选择“当前读音”。\n"
    "3. 只输出一个 JSON 对象，键是词的编号（字符串），值是选中的读音。不要输出其他文字。"
)

FREE_SYSTEM_PROMPT = (
    "你是日语读音校对助手。根据整句语境，判断给定的词在这句话里的实际读音，用平假名回答。\n"
    "“系统当前读音”和“参考”都是程序给出的，可能有错：正确就原样返回，不对就给出你认为正确的读音。\n"
    "只输出一个 JSON 对象，键是 \"1\"，值是该词完整的平假名读音（含送假名），不要输出其他文字。"
)

RUBY_RE = re.compile(r"<ruby>(.*?)<rp>\(</rp><rt>(.*?)</rt><rp>\)</rp></ruby>")
RULE_RE = re.compile(r"^(.*?)\[([^\[\]]+)\](.*)$")
KANA_ONLY_RE = re.compile(r"^[ぁ-ゖー]+$")
FULLWIDTH_DIGIT_RE = re.compile(r"[０-９]")


def app_base_dir():
    """exe（或脚本）所在目录；配置、缓存、否决清单都放在这里，不打进 exe。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def load_config(base_dir=None):
    """读取 ai_config.json；文件缺失、损坏、字段类型不对一律回退默认值（即关闭）。"""
    cfg = dict(DEFAULT_CONFIG)
    cfg["skip_words"] = list(DEFAULT_CONFIG["skip_words"])
    path = os.path.join(base_dir or app_base_dir(), CONFIG_FILE)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key, default in DEFAULT_CONFIG.items():
        v = data.get(key)
        if isinstance(default, bool):
            if isinstance(v, bool):
                cfg[key] = v
        elif isinstance(default, int):
            if isinstance(v, int) and not isinstance(v, bool):
                cfg[key] = v
        elif isinstance(default, str):
            if isinstance(v, str):
                cfg[key] = v.strip()
        elif isinstance(default, list):
            if isinstance(v, list):
                cfg[key] = [str(x) for x in v if str(x)]
    if cfg["mode"] not in ("adopt", "suggest"):
        cfg["mode"] = "adopt"
    cfg["concurrency"] = min(64, max(1, cfg["concurrency"]))
    cfg["timeout_seconds"] = max(5, cfg["timeout_seconds"])
    cfg["max_retries"] = min(5, max(0, cfg["max_retries"]))
    cfg["fuse_after_failures"] = max(1, cfg["fuse_after_failures"])
    cfg["ai_context_lines"] = min(4, max(0, cfg["ai_context_lines"]))
    cfg["rule_context_chars"] = max(2, cfg["rule_context_chars"])
    cfg["rule_pre_min"] = min(10, max(0, cfg["rule_pre_min"]))
    cfg["rule_suf_min"] = min(10, max(0, cfg["rule_suf_min"]))
    cfg["veto_context_chars"] = max(0, cfg["veto_context_chars"])
    return cfg


# ---------------------------------------------------------------- 网络

class FatalError(Exception):
    """Key 无效 / 余额不足 / 无权限：继续请求没有意义，立即停止全部请求。"""


class SoftError(Exception):
    """单次请求失败：可重试，或放弃这一句。"""

    def __init__(self, msg, code=None, body=""):
        super().__init__(msg)
        self.code = code
        self.body = body


class _State:
    """一次复核运行内共享的请求状态。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.fatal = ""
        self.fails = 0
        self.json_mode = True
        self.json_fallback = False
        self.requests = 0
        self.tokens_in = 0
        self.tokens_out = 0


def _http_chat(cfg, messages, json_mode):
    """POST {base_url}/chat/completions，返回 (content, usage)。"""
    body = {"model": cfg["model"], "messages": messages, "temperature": 0}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if cfg["thinking_off"]:
        body["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"],
                 "User-Agent": "jp-furigana-ai-reviewer/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout_seconds"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            text = ""
        if e.code in (401, 402, 403):
            raise FatalError("HTTP %d：%s" % (e.code, text))
        raise SoftError("HTTP %d：%s" % (e.code, text), code=e.code, body=text)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SoftError("网络或解析错误：%s" % str(e)[:120])
    try:
        content = (data["choices"][0].get("message") or {}).get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise SoftError("响应结构异常")
    usage = data.get("usage") if isinstance(data, dict) else None
    return content, (usage if isinstance(usage, dict) else {})


def _chat_with_retry(cfg, messages, state, cancel):
    """429 / 5xx / 网络错误指数退避；服务端不认 response_format 时降级为不带。"""
    last = None
    for attempt in range(cfg["max_retries"] + 1):
        if state.fatal or (cancel is not None and cancel.is_set()):
            raise SoftError("已停止")
        try:
            with state.lock:
                state.requests += 1
            return _http_chat(cfg, messages, state.json_mode)
        except SoftError as e:
            last = e
            if e.code == 400 and state.json_mode and "response_format" in (e.body or ""):
                state.json_mode = False
                state.json_fallback = True
                continue
            if e.code is None or e.code == 429 or e.code >= 500:
                wait = min(20.0, (2 ** attempt) * 1.5 + random.random())
                if cancel is not None:
                    cancel.wait(wait)
                else:
                    time.sleep(wait)
                continue
            break
    raise last


def extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- 否决清单

def load_veto(base_dir=None):
    """读取 ai_veto.txt。每行：前文[词]后文<TAB>范围(here|next|word)；# 开头为注释；坏行忽略。
    返回 {词: [(前文, 后文, 范围)]}。"""
    path = os.path.join(base_dir or app_base_dir(), VETO_FILE)
    veto = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parts = line.split("\t")
                m = RULE_RE.match(parts[0]) if len(parts) == 2 else None
                scope = parts[1].strip() if len(parts) == 2 else ""
                if not m or scope not in ("here", "next", "word"):
                    continue
                prefix, target, suffix = m.groups()
                veto.setdefault(target, []).append((prefix, suffix, scope))
    except OSError:
        pass
    return veto


def is_vetoed(veto, text, begin, end, surface):
    for prefix, suffix, scope in veto.get(surface, ()):
        if scope == "word":
            return True
        if not text.startswith(suffix, end):
            continue
        if scope == "next":
            return True
        if scope == "here" and text[:begin].endswith(prefix):
            return True
    return False


# ---------------------------------------------------------------- 推演（复现前四层）

class Seg:
    """一段输出：[begin, end) 对应原文，html 是前四层给出的结果。"""
    __slots__ = ("begin", "end", "html", "kanji", "layer", "span")

    def __init__(self, begin, end, html, kanji, layer, span):
        self.begin, self.end, self.html, self.kanji, self.layer, self.span = begin, end, html, kanji, layer, span


class Word:
    """满足 T4、待复核的词。"""

    def __init__(self, seg_index, surface, begin, end, current, options):
        self.seg_index = seg_index
        self.surface = surface
        self.begin = begin
        self.end = end
        self.current = current
        self.options = options
        self.id = ""
        self.sud = self.pyk = self.top = None     # Sudachi / pykakasi / 词频第一的读音，供清单里的 ※ 标记用
        self.dict = []                            # 词典候选（自由读音的参考信息）


class Replayer:
    """复现 SubtitleProcessor.japanese_to_kana_html 的分段结果。
    只读调用 proc 上已有的方法，不改动它们；逻辑必须与该函数保持一致，由 selfcheck 逐句比对。"""

    def __init__(self, proc, skip_words):
        self.proc = proc
        self.mod = sys.modules[type(proc).__module__]
        self.skip = set(skip_words)

    def segments(self, text):
        """返回 [Seg]；任何异常返回 None（调用方应放弃该句，保持原结果）。"""
        proc, mod = self.proc, self.mod
        try:
            tokens = list(proc.tokenizer_obj.tokenize(text, proc.mode))
            segs, skip_until = [], 0
            for pos, token in enumerate(tokens):
                if pos < skip_until:
                    continue
                if proc.context_rules:
                    hit = proc._context_annotate(text, tokens, pos)
                    if hit:
                        html, k, used = hit
                        segs.append(Seg(tokens[pos].begin(), tokens[pos + used - 1].end(), html, k, "context", None))
                        skip_until = pos + used
                        continue
                hit, layer = None, ""
                if proc.jp_dict is not None:
                    hit, layer = proc._dict_annotate(tokens, pos), "dict"
                    if not hit:
                        hit, layer = proc._single_token_annotate(token), "single"
                if hit:
                    html, k, used = hit
                    segs.append(Seg(tokens[pos].begin(), tokens[pos + used - 1].end(), html, k, layer,
                                    tokens[pos:pos + used]))
                    skip_until = pos + used
                    continue
                segs.append(self._fallback(token))
            return segs
        except Exception:
            return None

    def _fallback(self, token):
        proc, mod = self.proc, self.mod
        surface = token.surface()
        kana = proc.get_hiragana(surface)
        if kana and kana != surface:
            aligned = (mod._align_ruby(surface, kana)
                       if proc.jp_dict is not None and mod._JP_KANA_ONLY_RE.match(kana) else None)
            if aligned is not None:
                parts, k = [], 0
                for seg, ruby in aligned:
                    if ruby is None:
                        parts.append(seg)
                    else:
                        parts.append(f'<ruby>{seg}<rp>(</rp><rt>{ruby}</rt><rp>)</rp></ruby>')
                        k += len(seg)
                html = "".join(parts)
            else:
                html, k = proc.process_token(surface, kana)
        else:
            html, k = surface, len(re.findall(r'[一-鿿]', surface))
        return Seg(token.begin(), token.end(), html, k, "fallback", [token])

    def words(self, text, segs):
        """从分段结果里挑出满足 T4 的词。层①（人工规则）命中的段不会进来。"""
        proc, mod = self.proc, self.mod
        if proc.jp_dict is None:
            return []
        out = []
        for i, sg in enumerate(segs):
            if sg.layer == "context" or not sg.span:
                continue
            surf = "".join(t.surface() for t in sg.span)
            if not mod._JP_KANJI_RE.search(surf) or surf in self.skip:
                continue
            cands = proc._jp_lookup(surf)
            if len(set(cands)) <= 1:
                continue
            if sg.layer == "fallback":
                current = proc.get_hiragana(surf) or surf
            else:
                current = RUBY_RE.sub(r"\2", sg.html)
            sud = mod._kata_to_hira("".join(t.reading_form() for t in sg.span))
            pyk = proc.get_hiragana(surf) or ""
            top = proc._jp_top_by_freq(surf, cands)
            if not ((top is not None and current != top) or sud != pyk):
                continue
            options = []
            for r in list(cands) + [sud, pyk, current]:
                if r and r not in options:
                    options.append(r)
            w = Word(i, surf, sg.begin, sg.end, current, options)
            w.sud, w.pyk, w.top = sud, pyk, top
            out.append(w)
        return out

    def free_words(self, text, segs):
        """方案②：现有 T4 管不到的词。两类：
        (1) B2/C2：词典 0 或 1 个候选，且两个独立来源（Sudachi 与 pykakasi）读音不一致；
        (2) C1 名词：词典无收录、Sudachi 与 pykakasi 一致、单个 token 的普通名词 / 固有名词
            （抽样 48 个 AI 只改 3 个、3 个全对；动词 / 形容词词干、数词等不放进来）。
        共同条件：含汉字；不在跳过词表；层①命中的段不要；
        系统当前读音是纯平假名（含全角数字等本来就写不进成品的词不要）。"""
        proc, mod = self.proc, self.mod
        if proc.jp_dict is None:
            return []
        out = []
        for i, sg in enumerate(segs):
            if sg.layer == "context" or not sg.span:
                continue
            surf = "".join(t.surface() for t in sg.span)
            if not mod._JP_KANJI_RE.search(surf) or surf in self.skip:
                continue
            cands = proc._jp_lookup(surf)
            if len(set(cands)) > 1:
                continue                                    # 多候选归 T4 管
            sud = mod._kata_to_hira("".join(t.reading_form() for t in sg.span))
            pyk = proc.get_hiragana(surf) or ""
            if sud == pyk:
                # 两个来源一致：只放行 C1 名词（词典无收录 + 单 token + 普通名詞 / 固有名詞）
                if cands or len(sg.span) != 1:
                    continue
                pos = sg.span[0].part_of_speech()
                if pos[0] != "名詞" or pos[1] not in C1_NOUN_KINDS:
                    continue
            current = (pyk or surf) if sg.layer == "fallback" else RUBY_RE.sub(r"\2", sg.html)
            if not KANA_RE.match(current):
                continue
            options = [r for r in dict.fromkeys(list(cands) + [sud, pyk, current]) if r]
            w = Word(i, surf, sg.begin, sg.end, current, options)
            w.sud, w.pyk, w.top = sud, pyk, None
            w.dict = list(cands)
            out.append(w)
        return out

    def selfcheck(self, lines):
        """对一批句子比对：分段拼接的 html 与汉字数必须与 japanese_to_kana_html 完全一致。
        返回 (句数, 不一致列表[(句子, 推演结果, 实际结果)])。"""
        bad, seen = [], set()
        for text in lines:
            if text in seen:
                continue
            seen.add(text)
            segs = self.segments(text)
            real = self.proc.japanese_to_kana_html(text)
            mine = ("".join(s.html for s in segs), sum(s.kanji for s in segs)) if segs is not None else None
            if mine != tuple(real):
                bad.append((text, mine, real))
        return len(seen), bad


# ---------------------------------------------------------------- 请求、缓存

def build_messages(text, words):
    lines = ["句子：" + text, "待判断的词（只对这一句里的词作答）："]
    for w in words:
        ctx = text[max(0, w.begin - 6):w.begin] + "【" + w.surface + "】" + text[w.end:w.end + 6]
        lines.append("%s. 【%s】（上下文：…%s…）当前读音：%s  候选：%s"
                     % (w.id, w.surface, ctx, w.current, " / ".join(w.options)))
    lines.append('请只输出 JSON，例如：{"1": "读音"}')
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n".join(lines)}]


def _sig(words):
    return json.dumps([[w.surface, w.begin, w.options] for w in words], ensure_ascii=False)


def cache_key(cfg, text, words):
    raw = "\n".join((cfg["model"], PROMPT_VERSION, text, _sig(words)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _last_key(text, w):
    """跨模型/提示词版本的“同一位置”键，用来发现与上次不同的答案。"""
    return hashlib.sha1(("\n".join((text, w.surface, str(w.begin), "/".join(w.options)))).encode("utf-8")).hexdigest()


class Cache:
    """ai_cache.json：{"entries": {key: {"answers": {begin: 读音}, "prev": {begin: 上次读音}}}, "last": {key2: 读音}}。
    只缓存成功解析且通过校验的结果。损坏或缺失按空缓存处理。"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.entries, self.last, self.dirty = {}, {}, False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if isinstance(data.get("entries"), dict):
                    self.entries = data["entries"]
                if isinstance(data.get("last"), dict):
                    self.last = data["last"]
        except (OSError, ValueError):
            pass

    def get(self, key):
        with self.lock:
            return self.entries.get(key)

    def put(self, key, text, words, answers):
        """answers: {Word: 读音}。写入条目，并对照 last 记录“与上次不同”。"""
        entry = {"answers": {}, "prev": {}}
        with self.lock:
            for w, r in answers.items():
                entry["answers"][str(w.begin)] = r
                lk = _last_key(text, w)
                old = self.last.get(lk)
                if old is not None and old != r:
                    entry["prev"][str(w.begin)] = old
                self.last[lk] = r
            self.entries[key] = entry
            self.dirty = True
        return entry

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"entries": self.entries, "last": self.last}, f, ensure_ascii=False)
                os.replace(tmp, self.path)
                self.dirty = False
            except OSError:
                pass


def clear_cache(base_dir=None):
    """清空缓存条目；保留 last（用于下次发现“与上次不同”）。返回清掉的条数。"""
    path = os.path.join(base_dir or app_base_dir(), CACHE_FILE)
    c = Cache(path)
    n = len(c.entries)
    c.entries, c.dirty = {}, True
    c.save()
    return n


def test_connection(cfg):
    """设置窗口的“测试连接”：成功返回 (True, 说明)，失败返回 (False, 原因)。"""
    if not cfg.get("api_key"):
        return False, "未填写 API Key"
    probe = dict(cfg, timeout_seconds=min(cfg["timeout_seconds"], 30))
    try:
        content, _ = _http_chat(probe, [{"role": "user", "content": "hi"}], False)
    except (FatalError, SoftError) as e:
        return False, str(e)
    return True, "连接成功"


# ---------------------------------------------------------------- 复核

class Change:
    """一处 AI 与前四层不一致的结果。"""

    def __init__(self, text, word, reading, prev=None, applied=False, reason=""):
        self.text, self.word, self.reading = text, word, reading
        self.prev = prev                  # 缓存里记录的上一次答案（与这次不同时才有）
        self.applied = applied
        self.reason = reason              # 未应用的原因
        self.html = None                  # 应用后该词的新 ruby
        self.kind = "t4"                  # "t4"=候选内选择；"free"=自由读音（无候选约束）

    @property
    def fullwidth(self):
        return bool(FULLWIDTH_DIGIT_RE.search(self.word.surface))


class Review:
    """一次复核的全部结果。"""

    def __init__(self):
        self.segs = {}        # text -> [Seg]
        self.answers = {}     # text -> {begin: 读音}（含与当前相同的）
        self.changes = []     # [Change]，每个 (句子, 位置) 一条
        self.stats = Counter()
        self.fatal = ""
        self.json_fallback = False

    def html_for(self, text, adopt=True):
        """返回 (html, 汉字数)。模式 A 或没有改动时返回 None，表示沿用原结果。"""
        if not adopt:
            return None
        segs = self.segs.get(text)
        ch = [c for c in self.changes if c.text == text and c.applied]
        if not segs or not ch:
            return None
        repl = {c.word.seg_index: c.html for c in ch}
        html = "".join(repl.get(i, s.html) for i, s in enumerate(segs))
        kanji = sum(s.kanji for s in segs)
        return html, kanji


def merge_reviews(first, second):
    """把 T4 的 Review 与自由读音的 Review 拼成一份（只拼接，不修改任何一方）。
    segs / answers 以 first 为准，second 里 first 没有的句子补进去；changes 先 first 后 second；
    stats 里的数值相加；fatal 取第一个非空。"""
    m = Review()
    for src in (first, second):
        for t, sg in src.segs.items():
            m.segs.setdefault(t, sg)
        for t, a in src.answers.items():
            m.answers.setdefault(t, {}).update(a)
        m.changes.extend(src.changes)
        for k, v in src.stats.items():
            if isinstance(v, (int, float)):
                m.stats[k] += v
        m.fatal = m.fatal or src.fatal
        m.json_fallback = m.json_fallback or src.json_fallback
    return m


def plan_requests(proc, texts, cfg, veto):
    """推演并挑词，不联网。返回 ({text: (segs, words)}, 需要请求的句数)。"""
    rp = Replayer(proc, cfg["skip_words"])
    plan, seen = OrderedDict(), set()
    for text in texts:
        if not text or text in seen:
            continue
        seen.add(text)
        segs = rp.segments(text)
        if segs is None:
            continue
        words = [w for w in rp.words(text, segs) if not is_vetoed(veto, text, w.begin, w.end, w.surface)]
        if words:
            for i, w in enumerate(words, 1):
                w.id = str(i)
            plan[text] = (segs, words)
    return plan


def count_requests(proc, texts, cfg=None, base_dir=None):
    """批量前预估：本批会向服务发多少次请求（扣除缓存命中）。不联网。"""
    cfg = cfg or load_config(base_dir)
    base_dir = base_dir or app_base_dir()
    plan = plan_requests(proc, texts, cfg, load_veto(base_dir))
    cache = Cache(os.path.join(base_dir, CACHE_FILE))
    n = sum(1 for t, (_, ws) in plan.items() if cache.get(cache_key(cfg, t, ws)) is None)
    if cfg.get("free_reading"):                         # 自由读音开着，预估把它也算进去
        n += count_free_requests(proc, texts, cfg, base_dir)
    return n


def _build_html(proc, word, reading):
    """把词的读音重新对齐成 ruby；对不上（含全角数字等）返回 None。"""
    mod = sys.modules[type(proc).__module__]
    aligned = mod._align_ruby(word.surface, reading)
    if aligned is None:
        return None
    parts = []
    for seg, ruby in aligned:
        parts.append(seg if ruby is None else f'<ruby>{seg}<rp>(</rp><rt>{ruby}</rt><rp>)</rp></ruby>')
    return "".join(parts)


def review_texts(proc, texts, cfg=None, base_dir=None, log=None, cancel=None, plan=None):
    """主入口：对一组日文行做 AI 复核，返回 Review。永不抛异常（异常情况下返回已完成的部分）。
    cancel: threading.Event，置位后不再发新请求。"""
    cfg = cfg or load_config(base_dir)
    base_dir = base_dir or app_base_dir()
    out = Review()
    say = log or (lambda m: None)
    try:
        veto = load_veto(base_dir)
        if plan is None:
            plan = plan_requests(proc, texts, cfg, veto)
        cache = Cache(os.path.join(base_dir, CACHE_FILE))
        state = _State()
        todo = []
        for text, (segs, words) in plan.items():
            out.segs[text] = segs
            hit = cache.get(cache_key(cfg, text, words))
            if hit is not None:
                _absorb(proc, out, text, words, hit, cached=True)
            else:
                todo.append((text, words))
        out.stats["lines"] = len(plan)
        out.stats["cached"] = len(plan) - len(todo)
        say("AI注音：%d 句含待复核词，需请求 %d 次（缓存命中 %d）" % (len(plan), len(todo), len(plan) - len(todo)))
        if todo and not cfg["api_key"]:
            out.fatal = "未填写 API Key，已跳过 AI注音"
            say(out.fatal)
            todo = []

        def work(item):
            text, words = item
            if state.fatal or (cancel is not None and cancel.is_set()):
                return
            try:
                content, usage = _chat_with_retry(cfg, build_messages(text, words), state, cancel)
            except FatalError as e:
                state.fatal = "Key 无效/余额不足/无权限，已停止后续请求：" + str(e)
                return
            except SoftError:
                with state.lock:
                    state.fails += 1
                    if state.fails >= cfg["fuse_after_failures"] and not state.fatal:
                        state.fatal = "连续失败 %d 次，已停止后续请求，请检查网络或 Key" % state.fails
                return
            with state.lock:
                state.fails = 0
                state.tokens_in += usage.get("prompt_tokens", 0) or 0
                state.tokens_out += usage.get("completion_tokens", 0) or 0
            parsed = extract_json(content)
            if not isinstance(parsed, dict):
                return
            answers = {}
            for w in words:
                v = parsed.get(w.id)
                if isinstance(v, str) and v.strip() in w.options:
                    answers[w] = v.strip()
            if not answers:
                return
            entry = cache.put(cache_key(cfg, text, words), text, words, answers)
            with state.lock:
                _absorb(proc, out, text, words, entry, cached=False)

        if todo:
            with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
                list(ex.map(work, todo))
        cache.save()
        out.fatal = out.fatal or state.fatal
        out.json_fallback = state.json_fallback
        out.stats["requests"] = state.requests
        out.stats["tokens_in"], out.stats["tokens_out"] = state.tokens_in, state.tokens_out
        if out.fatal:
            say("!! AI注音提前停止：" + out.fatal)
    except Exception as e:                                   # 任何意外都不能影响主流程
        out.fatal = out.fatal or "AI注音出错，已跳过：%s" % e
        say(out.fatal)
    return out


# ---------------------------------------------------------------- 自由读音（方案②，只出建议，不改成品）

def plan_free(proc, texts, cfg, veto):
    """挑出方案②范围内的词，一句一个任务。返回 {text: (segs, [Word])}。不联网。"""
    rp = Replayer(proc, cfg["skip_words"])
    plan, seen = OrderedDict(), set()
    for text in texts:
        if not text or text in seen:
            continue
        seen.add(text)
        segs = rp.segments(text)
        if segs is None:
            continue
        # 现有 T4 已经管的词不重复问（它们走候选内选择）
        t4 = {(w.begin, w.end) for w in rp.words(text, segs)}
        words = [w for w in rp.free_words(text, segs)
                 if (w.begin, w.end) not in t4 and not is_vetoed(veto, text, w.begin, w.end, w.surface)]
        if words:
            for i, w in enumerate(words, 1):
                w.id = str(i)
            plan[text] = (segs, words)
    return plan


def free_messages(text, w):
    """一词一请求：自由读音对每个词单独判断（每句多词时，实验里 19 条有 4 条与单词请求不一致）。
    不给候选，只给系统当前读音与 Sudachi / pykakasi / 词典的参考。"""
    ctx = text[max(0, w.begin - 6):w.begin] + "【" + w.surface + "】" + text[w.end:w.end + 6]
    ref = "Sudachi=%s；pykakasi=%s；词典=%s" % (w.sud, w.pyk, "/".join(w.dict) or "无收录")
    user = ("句子：%s\n待判断的词：【%s】（上下文：…%s…）\n系统当前读音：%s\n参考：%s\n"
            "请只输出 JSON，例如：{\"1\": \"ひらがな\"}" % (text, w.surface, ctx, w.current, ref))
    return [{"role": "system", "content": FREE_SYSTEM_PROMPT}, {"role": "user", "content": user}]


def free_cache_key(cfg, text, w):
    """一词一键：模型 + 提示词版本 + 句子 + 词的位置 + 词形 + 当前读音 + 参考读音。"""
    raw = "\n".join((cfg["model"], FREE_PROMPT_VERSION, text, str(w.begin), w.surface, w.current,
                     w.sud or "", w.pyk or "", "/".join(w.dict)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def count_free_requests(proc, texts, cfg=None, base_dir=None):
    """预估自由读音要发多少次请求（扣除缓存命中）。不联网。"""
    cfg = cfg or load_config(base_dir)
    base_dir = base_dir or app_base_dir()
    plan = plan_free(proc, texts, cfg, load_veto(base_dir))
    cache = Cache(os.path.join(base_dir, CACHE_FILE))
    return sum(1 for t, (_, ws) in plan.items() for w in ws if cache.get(free_cache_key(cfg, t, w)) is None)


def review_free(proc, texts, cfg=None, base_dir=None, log=None, cancel=None, adopt=False):
    """自由读音复核：AI 没有候选约束，答案必须是纯平假名。永不抛异常。
    adopt=False：只产生“建议”（applied 一律为 False）；adopt=True（模式 B）：能对齐汉字的标 applied=True。
    答案是否能对齐汉字块（_align_ruby）也会检查并记入 reason，供清单告知。"""
    cfg = cfg or load_config(base_dir)
    base_dir = base_dir or app_base_dir()
    out = Review()
    say = log or (lambda m: None)
    try:
        veto = load_veto(base_dir)
        plan = plan_free(proc, texts, cfg, veto)
        cache = Cache(os.path.join(base_dir, CACHE_FILE))
        state = _State()
        todo, n_words = [], 0
        for text, (segs, words) in plan.items():
            out.segs[text] = segs
            for w in words:
                n_words += 1
                hit = cache.get(free_cache_key(cfg, text, w))
                if hit is not None:
                    _absorb_free(proc, out, text, w, hit, cached=True, adopt=adopt)
                else:
                    todo.append((text, w))
        out.stats["lines"] = len(plan)
        out.stats["words"] = n_words
        out.stats["cached"] = n_words - len(todo)
        say("AI 自由读音：%d 句 / %d 个词，需请求 %d 次（缓存命中 %d）" % (len(plan), n_words, len(todo), n_words - len(todo)))
        if todo and not cfg["api_key"]:
            out.fatal = "未填写 API Key，已跳过自由读音复核"
            say(out.fatal)
            todo = []

        def work(item):
            text, w = item
            if state.fatal or (cancel is not None and cancel.is_set()):
                return
            try:
                content, usage = _chat_with_retry(cfg, free_messages(text, w), state, cancel)
            except FatalError as e:
                state.fatal = "Key 无效/余额不足/无权限，已停止后续请求：" + str(e)
                return
            except SoftError:
                with state.lock:
                    state.fails += 1
                    if state.fails >= cfg["fuse_after_failures"] and not state.fatal:
                        state.fatal = "连续失败 %d 次，已停止后续请求，请检查网络或 Key" % state.fails
                return
            with state.lock:
                state.fails = 0
                state.tokens_in += usage.get("prompt_tokens", 0) or 0
                state.tokens_out += usage.get("completion_tokens", 0) or 0
            parsed = extract_json(content)
            v = parsed.get("1") if isinstance(parsed, dict) else None
            if not (isinstance(v, str) and KANA_RE.match(v.strip())):
                return                                      # 自由读音：只要求是纯平假名，不要求在候选里
            entry = cache.put(free_cache_key(cfg, text, w), text, [w], {w: v.strip()})
            with state.lock:
                _absorb_free(proc, out, text, w, entry, cached=False, adopt=adopt)

        if todo:
            with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
                list(ex.map(work, todo))
        cache.save()
        out.fatal = out.fatal or state.fatal
        out.stats["requests"] = state.requests
        out.stats["tokens_in"], out.stats["tokens_out"] = state.tokens_in, state.tokens_out
        if out.fatal:
            say("!! 自由读音复核提前停止：" + out.fatal)
    except Exception as e:
        out.fatal = out.fatal or "自由读音复核出错，已跳过：%s" % e
        say(out.fatal)
    return out


def _absorb_free(proc, out, text, w, entry, cached, adopt=False):
    """自由读音一个词的答案并入 Review。只记“与当前读音不同”的改动。
    adopt=True（模式 B）：能对齐汉字的标 applied=True，由 apply_to_lines 写入成品；
    adopt=False（模式 A）：一律 applied=False，只出建议。
    同一句里的多个词各自并入，不互相覆盖。"""
    answers, prev = entry.get("answers", {}), entry.get("prev", {})
    rd = answers.get(str(w.begin))
    if rd is None:
        return
    out.answers.setdefault(text, {})[w.begin] = rd
    out.stats["answered"] += 1
    if rd == w.current:
        return
    c = Change(text, w, rd, prev=None if cached else prev.get(str(w.begin)))
    c.kind = "free"
    html = None if c.fullwidth else _build_html(proc, w, rd)
    c.html = html
    if c.fullwidth:
        c.reason = "自由读音：AI 建议但无法应用（全角数字）"
    elif html is None:
        c.reason = "自由读音：AI 建议但无法应用（此读音无法对齐汉字）"
    elif adopt:
        c.applied = True
    else:
        c.reason = "自由读音建议（未写入成品）"
    out.changes.append(c)
    w.options = list(dict.fromkeys(w.options + [rd]))         # 让清单“候选”一栏看得到 AI 的答案


def _absorb(proc, out, text, words, entry, cached):
    """把一句的答案并入 Review：与当前读音不同的记为 Change，并尝试生成新 ruby。"""
    answers, prev = entry.get("answers", {}), entry.get("prev", {})
    out.answers[text] = {int(k): v for k, v in answers.items()}
    for w in words:
        r = answers.get(str(w.begin))
        if r is None or r not in w.options:
            continue
        out.stats["answered"] += 1
        if r == w.current:
            continue
        # 缓存命中时答案不变，不产生“与上次不同”标记（§9.1）
        c = Change(text, w, r, prev=None if cached else prev.get(str(w.begin)))
        if c.fullwidth:
            c.reason = "AI 建议但无法应用（全角数字）"
        else:
            html = _build_html(proc, w, r)
            if html is None:
                c.reason = "AI 建议但无法应用（读音无法对齐汉字）"
            else:
                c.html, c.applied = html, True
        out.changes.append(c)


# ---------------------------------------------------------------- 规则草案与预检（§6.2）

COUNTER_WORDS = {"時", "本", "日", "分", "人", "回", "個", "枚", "匹", "階", "歳", "年", "月", "冊", "台", "杯"}
DIGIT_RE = re.compile(r"[0-9０-９〇一二三四五六七八九十百千]")


def high_risk(surface):
    """§6.3：数字+量词、助数词、含「何」的拼词、单字汉字排最前。"""
    if len(surface) == 1:
        return True
    if surface in COUNTER_WORDS or (len(surface) > 1 and surface[-1] in COUNTER_WORDS and DIGIT_RE.search(surface)):
        return True
    return "何" in surface


def _group_changes(changes, texts=None):
    """按（词、原读音、AI 读音）分组。给了 texts 时，组内改动按“文件先后、词在句中先后”递增排序，
    这样出处、规则草案、配套数据里的语境顺序都稳定且一致，不受并发请求完成先后的影响。"""
    groups = OrderedDict()
    for c in changes:
        # 同一个 (词, 原读音, AI读音) 若既有 T4 又有自由读音，分成两条：它们的风险不同，标记也不同
        key = (c.word.surface, c.word.current, c.reading) if c.kind == "t4" else (c.word.surface, c.word.current, c.reading, "free")
        groups.setdefault(key, []).append(c)
    if texts is not None:
        first = {}
        for i, t in enumerate(texts):
            first.setdefault(t, i)
        for grp in groups.values():
            grp.sort(key=lambda c: (first.get(c.text, 10 ** 9), c.word.begin))
    return groups


def _draft(text, w, pre_w, suf_w=None):
    """规则草案的前后文；suf_w 缺省时前后取同样字数。"""
    suf_w = pre_w if suf_w is None else suf_w
    return text[max(0, w.begin - pre_w):w.begin], text[w.end:w.end + suf_w]


def _usable(prefix, suffix):
    return not any(ch in prefix + suffix for ch in "[]\t\r\n")


def _expected_html(review, group):
    """只应用本组改动时，每个来源句应有的 html；有任何一处无法生成则为 None。"""
    exp = {}
    for text in {c.text for c in group}:
        segs = review.segs[text]
        repl = {}
        for c in group:
            if c.text == text:
                if c.html is None:
                    exp[text] = None
                    break
                repl[c.word.seg_index] = c.html
        else:
            exp[text] = "".join(repl.get(i, s.html) for i, s in enumerate(segs))
    return exp


class _TrialRules:
    """临时把候选规则并入 proc.context_rules；退出时一定还原。"""

    def __init__(self, proc, rules):
        self.proc, self.rules = proc, rules

    def __enter__(self):
        self.saved = self.proc.context_rules
        new = {k: list(v) for k, v in self.saved.items()}
        for prefix, target, suffix, reading in self.rules:
            new.setdefault(target[0], []).append((prefix, target, suffix, reading))
        for lst in new.values():
            lst.sort(key=lambda r: len(r[0]) + len(r[2]), reverse=True)
        self.proc.context_rules = new

    def __exit__(self, *a):
        self.proc.context_rules = self.saved


def precheck(proc, review, texts, cfg):
    """为每组改动挑规则草案并预检。返回 {(词, 原读音, AI读音): info}。
    info: width, rules[(prefix,target,suffix,reading)], effective, total, collateral[句子], ok。
    基线在加载任何候选规则之前一次性算好并固定，否则会被新规则污染。"""
    groups = _group_changes(review.changes, texts)
    surfaces = {k[0] for k in groups}
    uniq = list(OrderedDict.fromkeys(t for t in texts if t))
    related = {s: [t for t in uniq if s in t] for s in surfaces}
    baseline = {}
    for s in surfaces:
        for t in related[s]:
            if t not in baseline:
                baseline[t] = tuple(proc.japanese_to_kana_html(t))
    # 前后文各有下限（默认 2/2），在下限上依次加 0、1、2、4 字，从短到长逐级试（默认即 2、3、4、6）
    widths = [(min(10, cfg["rule_pre_min"] + d), min(10, cfg["rule_suf_min"] + d)) for d in (0, 1, 2, 4)]
    widths = list(OrderedDict.fromkeys(widths))
    info = {}
    for key, group in groups.items():
        surface, reading = key[0], key[2]
        sources = {c.text for c in group}
        expected = _expected_html(review, group)
        best = None
        for width in widths:
            rules, seen = [], set()
            for c in group:
                prefix, suffix = _draft(c.text, c.word, width[0], width[1])
                if not _usable(prefix, suffix):
                    continue
                r = (prefix, surface, suffix, reading)
                if r not in seen:
                    seen.add(r)
                    rules.append(r)
            if not rules:
                continue
            with _TrialRules(proc, rules):
                effective = sum(1 for t in sources
                                if expected[t] is not None and proc.japanese_to_kana_html(t)[0] == expected[t])
                collateral = [t for t in related[surface] if t not in sources
                              and tuple(proc.japanese_to_kana_html(t)) != baseline[t]]
            cand = {"width": width, "rules": rules, "effective": effective, "total": len(sources),
                    "collateral": collateral, "ok": effective == len(sources) and not collateral}
            rank = (cand["ok"], effective, -len(collateral), -sum(width))
            if best is None or rank > best[0]:
                best = (rank, cand)
            if cand["ok"]:
                break
        info[key] = best[1] if best else {"width": None, "rules": [], "effective": 0, "total": len(sources),
                                          "collateral": [], "ok": False}
    return info


# ---------------------------------------------------------------- 改动清单

def report_filename(src_path, mode):
    """统一一份清单：采用模式 _AI改动清单.txt，建议模式 _AI建议.txt。T4 与自由读音的条目同在一份里。"""
    base = os.path.splitext(src_path)[0]
    return base + ("_AI改动清单.txt" if mode == "adopt" else "_AI建议.txt")


def _excerpt(text, w, n=12):
    return ("…" if w.begin > n else "") + text[max(0, w.begin - n):w.begin] + "【" + w.surface + "】" + text[w.end:w.end + n]


def sidecar_path(report_path):
    """清单的配套数据文件：同名 .json。应用清单时需要它（清单里只列了部分出处和规则草案）。"""
    return os.path.splitext(report_path)[0] + ".json"


def _ctx_windows(grp, n=6):
    """一组改动的全部不同语境：[[前文n字, 后文n字], ...]，供否决/写规则时取用。"""
    seen, out = set(), []
    for c in grp:
        pre = c.text[max(0, c.word.begin - n):c.word.begin]
        suf = c.text[c.word.end:c.word.end + n]
        if (pre, suf) not in seen:
            seen.add((pre, suf))
            out.append([pre, suf])
    return out


def _build_report(proc, review, texts, cfg, mode=None, tags=None, source_name="", data_name=""):
    """返回 (清单文本, 配套数据)。没有任何改动时返回 ("", None)。
    tags: 与 texts 等长的位置描述列表（SRT 为“#块号 时间码”）；缺省用“第 N 行”。"""
    mode = mode or cfg["mode"]                                  # 自由读音也跟随采用 / 仅建议
    if not review.changes:
        return "", None
    data = {"version": 2, "mode": mode, "source": source_name, "model": cfg["model"],
            "veto_chars": cfg["veto_context_chars"], "groups": {}}
    # 每个位置的下标（texts 里的先后就是文件里的先后）：组内出处按它递增，不依赖并发完成的先后
    index_of = {}
    for i, t in enumerate(texts):
        index_of.setdefault(t, []).append(i)

    def places(grp):
        """一组改动的全部出处，按“文件先后、词在句中的先后”递增。返回 [(位置描述, 改动)]。"""
        rows = []
        for c in grp:
            for i in index_of.get(c.text, []):
                loc = tags[i] if isinstance(tags, (list, tuple)) and i < len(tags) and tags[i] else "第 %d 行" % (i + 1)
                rows.append((i, c.word.begin, loc, c))
        rows.sort(key=lambda r: (r[0], r[1]))
        return [(loc, c) for _, _, loc, c in rows]

    groups = _group_changes(review.changes, texts)
    info = precheck(proc, review, texts, cfg)
    ranked = sorted(groups.items(), key=lambda kv: (not high_risk(kv[0][0]), -len(kv[1]), kv[0][0]))
    adopt = mode == "adopt"
    has_free = any(c.kind == "free" for c in review.changes)
    n_free = sum(1 for c in review.changes if c.kind == "free")
    title = ("改动清单（采用模式：AI 的选择已写入注音）" if adopt else "建议（建议模式：未改动注音结果）")
    out = ["# AI注音%s%s" % (title, "  来源：" + source_name if source_name else ""),
           "# 模型：%s   请求：%d 次   有答案的词：%d   与前四层不同：%d 处 / %d 组%s" % (
               cfg["model"], review.stats.get("requests", 0), review.stats.get("answered", 0),
               len(review.changes), len(groups), ("（其中自由读音 %d 处）" % n_free) if has_free else "")]
    if has_free:
        out += ["# 【自由读音】= 词典仅 1 个候选或无收录、且 Sudachi 与 pykakasi 读音不一致的词，AI 在没有候选约束下直接给出读音。",
                "#   抽样标注里改对率约 90%（21 条里对 19 条，样本小）；人名最容易被改错（例如 笹子→ささご），请优先核对这类条目。",
                "#   没有【自由读音】标记的条目是“词典多候选、AI 在候选内选择”，更稳。"]
    if adopt:
        out += ["# 判定方法（只处理你想处理的条目；不勾 = 保持现状，仍由 AI 决定）：",
                "#   [x] 认可         把 AI 的读音沉淀成规则写入 context_rules.txt（仅限预检 ✓ 的条目），以后不再问 AI",
                "#   [x] 原来对（否决） 让 AI 以后别改这个位置，写入 ai_veto.txt；范围在“否决范围”行填 here / next / word，默认 here",
                "#   [x] 都不对       在括号里填你认为正确的平假名读音，写入 context_rules.txt",
                "#   同一条只能勾一个。标记完后，在“AI 设置”窗口点“应用清单…”；写入前会自动备份。"]
    else:
        out += ["# 认可的条目把 [ ] 采纳 改成 [x]；这份建议没有改动成品。",
                "# 标记完后，在“AI 设置”窗口点“应用清单…”：采纳的读音会写入 context_rules.txt（仅限预检 ✓ 的条目），写入前会自动备份。"]
    if data_name:
        out.append("# 配套数据文件：%s（应用清单时需要，请放在同一目录，不要改名）" % data_name)
    out += ["# 排序：高风险词类（数字量词、助数词、含「何」、单字）在前，其余按出现次数降序。",
            "# ※ = AI 的选择与 Sudachi、pykakasi、词频第一都不一致，信息少，建议看一眼（不代表可疑）。",
            "# 预检 ✓ = 来源句全部生效且没有波及其他句子；!! = 有问题，先看说明。预检只统计本文件。", ""]
    n = 0
    for key, grp in ranked:
        surface, cur, reading = key[0], key[1], key[2]
        is_free = grp[0].kind == "free"
        n += 1
        w0 = grp[0].word
        flags = "  【自由读音】" if is_free else ""
        if reading not in (w0.sud, w0.pyk, w0.top) and not is_free:
            flags += "  ※"
        diffs = sorted({c.prev for c in grp if c.prev and c.prev != c.reading})
        if diffs:
            flags += "  【与上次不同：上次 %s → 这次 %s】" % ("/".join(diffs), reading)
        rows = places(grp)
        out.append("[%03d] %s   %s → %s   共 %d 处%s" % (n, surface, cur, reading, len(rows), flags))
        out.append("     候选：" + " / ".join(w0.options))
        for loc, c in rows:
            out.append("     出处：%s  %s" % (loc, _excerpt(c.text, c.word)))
        if adopt or is_free:
            reasons = sorted({c.reason for c in grp if c.reason})
            if reasons:
                out.append("     状态：" + "；".join(reasons))
            else:
                out.append("     状态：已写入成品")
        inf = info[key]
        for r in inf["rules"][:3]:
            out.append("     规则草案（示意，应用时以配套数据为准）：%s[%s]%s\t%s" % (r[0], r[1], r[2], r[3]))
        if len(inf["rules"]) > 3:
            out.append("           …另 %d 条" % (len(inf["rules"]) - 3))
        if inf["width"] is None:
            out.append("     预检：!! 无法生成规则草案（前后文含 [ ] 等特殊字符）")
        else:
            pw, sw = inf["width"]
            span = "前后文取 %d 字" % pw if pw == sw else "前文取 %d 字、后文取 %d 字" % (pw, sw)
            out.append("     预检：%s %s；来源句生效 %d/%d；波及其他句子 %d 句" % (
                "✓" if inf["ok"] else "!!", span, inf["effective"], inf["total"], len(inf["collateral"])))
            for t in inf["collateral"][:3]:
                out.append("           波及：" + t[:40])
        if adopt:
            out.append("     判定：[ ] 认可   [ ] 原来对（否决）   [ ] 都不对（我指定读音：________）")
            out.append("     否决范围：here   （选“原来对”时有效：here=仅此语境 / next=同后文 / word=整个词形）")
        else:
            out.append("     判定：[ ] 采纳")
        data["groups"]["%03d" % n] = {
            "surface": surface, "current": cur, "reading": reading, "options": w0.options, "count": len(grp),
            "ok": bool(inf["ok"]), "width": inf["width"], "effective": inf["effective"], "total": inf["total"],
            "collateral": len(inf["collateral"]), "rules": [[r[0], r[2]] for r in inf["rules"]],
            "ctx": _ctx_windows(grp), "free": is_free}
        out.append("")
    return "\n".join(out), data


def render_report(proc, review, texts, cfg, mode=None, tags=None, source_name=""):
    """只要清单文本（没有任何改动时返回 ""）。"""
    return _build_report(proc, review, texts, cfg, mode, tags, source_name)[0]


def write_report(proc, review, texts, cfg, src_path, tags=None, mode=None, source_name=None):
    """写出清单文件，返回路径；没有改动时返回 None。写失败也不抛异常。"""
    mode = mode or cfg["mode"]
    try:
        path = report_filename(src_path, mode)
        text, data = _build_report(proc, review, texts, cfg, mode, tags, source_name or os.path.basename(src_path),
                                   data_name=os.path.basename(sidecar_path(path)))
        if not text:
            return None
        with open(sidecar_path(path), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(text)
        return path
    except Exception:
        return None


# ---------------------------------------------------------------- 应用清单（第 4 步）

ITEM_RE = re.compile(r"^\[(\d{3})\] ", re.M)
KANA_RE = re.compile(r"^[ぁ-ゖー]+$")


def _checked(s):
    return s.strip().lower() == "x"


def parse_marked_report(text):
    """解析用户标记后的清单 -> {编号: {"verdict": ..., "reading": ..., "scope": ...}}。
    verdict: approve / veto / custom / adopt（模式 A 的采纳）；同一条勾了多个、或勾了但没填读音的记为 invalid。
    没勾任何选项的条目不出现在结果里（保持现状）。"""
    out = {}
    blocks = ITEM_RE.split(text)
    for i in range(1, len(blocks), 2):
        num, body = blocks[i], blocks[i + 1]
        j = re.search(r"判定：\[(.*?)\] 认可\s+\[(.*?)\] 原来对（否决）\s+\[(.*?)\] 都不对（我指定读音：(.*?)）", body)
        if j:
            a, v, c = _checked(j.group(1)), _checked(j.group(2)), _checked(j.group(3))
            reading = re.sub(r"[\s_＿]", "", j.group(4))
            m = re.search(r"否决范围：(\S*)", body)
            scope = (m.group(1).strip().lower() if m else "") or "here"
            n = a + v + c
            if n == 0:
                continue
            if n > 1:
                out[num] = {"verdict": "invalid", "why": "同一条勾选了多个选项"}
            elif a:
                out[num] = {"verdict": "approve"}
            elif v:
                out[num] = ({"verdict": "veto", "scope": scope} if scope in ("here", "next", "word")
                            else {"verdict": "invalid", "why": "否决范围应为 here / next / word，现为“%s”" % scope})
            elif KANA_RE.match(reading):
                out[num] = {"verdict": "custom", "reading": reading}
            else:
                out[num] = {"verdict": "invalid", "why": "“都不对”需要在括号里填写平假名读音，现为“%s”" % reading}
            continue
        j = re.search(r"判定：\[(.*?)\] 采纳", body)
        if j and _checked(j.group(1)):
            out[num] = {"verdict": "adopt"}
    return out


def _backup(path):
    """写入前备份：同目录下 xxx.bak_时间戳。文件不存在时无需备份，返回 None。"""
    if not os.path.exists(path):
        return None
    bak = "%s.bak_%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
    n = 1
    while os.path.exists(bak):
        n += 1
        bak = "%s.bak_%s_%d" % (path, time.strftime("%Y%m%d_%H%M%S"), n)
    with open(path, "rb") as src, open(bak, "wb") as dst:
        dst.write(src.read())
    return bak


def _existing_lines(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return {ln.rstrip("\r\n") for ln in f}
    except OSError:
        return set()


def _append_lines(path, header, lines):
    """追加到文件末尾（保留原内容与原编码习惯）；文件不以换行结尾时先补一个。"""
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        if exists and os.path.getsize(path) > 0:
            with open(path, "rb") as r:
                r.seek(-1, os.SEEK_END)
                if r.read(1) not in (b"\n", b"\r"):
                    f.write("\n")
        f.write(header + "\n")
        for ln in lines:
            f.write(ln + "\n")


def delete_report(report_path):
    """删除清单及其配套 .json（仅这两个文件，且只接受 *_AI改动清单.txt / *_AI建议.txt）。
    返回 (已删除的路径列表, 失败说明)。永不抛异常。"""
    name = os.path.basename(report_path)
    if not name.endswith(("_AI改动清单.txt", "_AI建议.txt")):
        return [], "不是 AI 清单文件，未删除"
    removed = []
    for p in (report_path, sidecar_path(report_path)):
        try:
            if os.path.exists(p):
                os.remove(p)
                removed.append(p)
        except OSError as e:
            return removed, "删除 %s 失败：%s" % (os.path.basename(p), e)
    return removed, ""


def apply_report(report_path, base_dir=None, rules_path=None, veto_path=None, dry_run=False):
    """读取标记后的清单，写入 ai_veto.txt / context_rules.txt（先备份，重复行不重复写）。
    返回 dict: ok, message, rules_added, veto_added, skipped[(编号,原因)], backups。
    dry_run=True 只预演，不写任何文件。永不抛异常。"""
    res = {"ok": False, "message": "", "rules_added": [], "veto_added": [], "skipped": [], "backups": []}
    try:
        base_dir = base_dir or app_base_dir()
        rules_path = rules_path or os.path.join(base_dir, "context_rules.txt")
        veto_path = veto_path or os.path.join(base_dir, VETO_FILE)
        try:
            with open(report_path, "r", encoding="utf-8-sig") as f:
                marks = parse_marked_report(f.read())
            with open(sidecar_path(report_path), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            res["message"] = "读取清单或配套数据文件失败：%s（配套的 .json 必须与清单放在同一目录、不要改名）" % e
            return res
        groups = data.get("groups", {})
        veto_chars = int(data.get("veto_chars", 2))
        rule_lines, veto_lines = [], []
        have_rules, have_veto = _existing_lines(rules_path), _existing_lines(veto_path)
        for num, m in sorted(marks.items()):
            g = groups.get(num)
            if g is None:
                res["skipped"].append((num, "配套数据里没有这一条（清单与 .json 不匹配？）"))
                continue
            if m["verdict"] == "invalid":
                res["skipped"].append((num, m["why"]))
                continue
            label = "%s %s→%s" % (g["surface"], g["current"], g["reading"])
            if m["verdict"] == "veto":
                for pre, suf in g["ctx"]:
                    p = pre[-veto_chars:] if veto_chars else ""
                    s = suf[:veto_chars] if veto_chars else ""
                    line = "%s[%s]%s\t%s" % (p, g["surface"], s, m["scope"])
                    if m["scope"] == "word":
                        line = "[%s]\tword" % g["surface"]
                    elif m["scope"] == "next":
                        line = "[%s]%s\tnext" % (g["surface"], s)
                    if any(ch in p + s for ch in "[]\t"):
                        res["skipped"].append((num, "语境含 [ ] 或制表符，无法写成否决行"))
                        continue
                    if line not in have_veto and line not in veto_lines:
                        veto_lines.append(line)
                continue
            # approve / adopt / custom：都是写 context_rules.txt
            if m["verdict"] == "custom":
                reading = m["reading"]
                if g["ok"] is False and not g["rules"]:
                    res["skipped"].append((num, "无法生成规则（前后文含特殊字符）"))
                    continue
            else:
                reading = g["reading"]
                if not g["ok"]:
                    res["skipped"].append((num, "预检为 !!（%s），未写入规则；请先看清单里的说明" % label))
                    continue
            for pre, suf in g["rules"]:
                line = "%s[%s]%s\t%s" % (pre, g["surface"], suf, reading)
                if line not in have_rules and line not in rule_lines:
                    rule_lines.append(line)
        res["rules_added"], res["veto_added"] = rule_lines, veto_lines
        if dry_run:
            res["ok"], res["message"] = True, "预演：将写入规则 %d 条、否决 %d 条" % (len(rule_lines), len(veto_lines))
            return res
        stamp = "# --- AI注音清单 %s 应用于 %s ---" % (os.path.basename(report_path), time.strftime("%Y-%m-%d %H:%M"))
        if rule_lines:
            b = _backup(rules_path)
            if b:
                res["backups"].append(b)
            _append_lines(rules_path, stamp, rule_lines)
        if veto_lines:
            b = _backup(veto_path)
            if b:
                res["backups"].append(b)
            _append_lines(veto_path, stamp, veto_lines)
        res["ok"] = True
        res["message"] = "已写入规则 %d 条、否决 %d 条；跳过 %d 条" % (len(rule_lines), len(veto_lines), len(res["skipped"]))
        if rule_lines:
            res["message"] += "。规则需重启程序后生效"
    except Exception as e:
        res["ok"], res["message"] = False, "应用清单出错：%s" % e
    return res


# ---------------------------------------------------------------- 主程序入口

def save_config(updates, base_dir=None):
    """把 updates 合并写入 ai_config.json，保留文件里的其他字段。成功返回 True。"""
    path = os.path.join(base_dir or app_base_dir(), CONFIG_FILE)
    data = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass
    for k, v in DEFAULT_CONFIG.items():
        data.setdefault(k, v)
    data.update(updates)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def apply_to_lines(proc, jp_lines, ruby_lines, out_dir, name, log=None, cancel=None, locs=None, source=None):
    """主程序钩子：对已注音的行做 AI 复核，返回新的 ruby_lines（与输入等长、同序）。
    模式 B 把 AI 的选择写入返回值；模式 A 原样返回。有改动时在 out_dir 写清单文件。
    locs: 与 jp_lines 等长的位置描述（块号+时间码），用于清单的“出处”；缺省为行号。
    source: 输入文件的真实文件名（含后缀），写在清单开头的“来源”；缺省时只用 name，不带后缀。
    任何异常都只返回原 ruby_lines，不影响主流程。"""
    say = log or (lambda m: None)
    try:
        cfg = load_config()
        if not cfg["api_key"]:
            say("AI注音：未填写 API Key，已跳过（请在“AI 设置”里填写）")
            return ruby_lines
        say("AI注音：模式=%s，模型=%s，并发=%d" % ("采用" if cfg["mode"] == "adopt" else "仅建议", cfg["model"], cfg["concurrency"]))
        rv = review_texts(proc, jp_lines, cfg, log=say, cancel=cancel)
        adopt = cfg["mode"] == "adopt"
        new, applied = list(ruby_lines), 0
        if adopt:
            for i, (t, h) in enumerate(zip(jp_lines, ruby_lines)):
                got = rv.html_for(t)
                segs = rv.segs.get(t)
                # 保险：推演结果必须与实际注音完全一致才替换，否则保持原样
                if got and segs and "".join(s.html for s in segs) == h:
                    new[i] = got[0]
                    applied += 1
        say("AI注音完成：与前四层不同 %d 处，%s" % (
            len(rv.changes), ("已写入成品 %d 行" % applied) if adopt else "仅生成建议，成品未改动"))
        n_skip = sum(1 for c in rv.changes if not c.applied)
        if adopt and n_skip:
            say("其中 %d 处无法应用（全角数字或无法对齐），见清单" % n_skip)
        report_rv = rv                                   # 写进清单的 Review：默认只有 T4
        # 自由读音（默认关闭，开关 free_reading）。与 T4 互不重叠（plan_free 已排除 T4 的词）；
        # 跟随 mode：采用 → 能对齐汉字的写入成品；仅建议 → 只出清单。
        # 写入时以“T4 之后的当前成品”为基准，推演结果与当前成品对不上就保持原样。
        # 它失败只记日志，T4 的结果与清单照常输出。
        if cfg.get("free_reading"):
            try:
                fr = review_free(proc, jp_lines, cfg, log=say, cancel=cancel, adopt=adopt)
                f_applied = 0
                if adopt:
                    for i, (t, h) in enumerate(zip(jp_lines, new)):
                        segs = fr.segs.get(t)
                        ch = [c for c in fr.changes if c.text == t and c.applied]
                        if not ch or not segs:
                            continue
                        t4_html = rv.html_for(t)
                        base = t4_html[0] if t4_html else "".join(s.html for s in segs)
                        if base != h:
                            continue                                     # 对不上就不动（保险）
                        t4_repl = {c.word.seg_index: c.html for c in rv.changes if c.text == t and c.applied}
                        repl = {c.word.seg_index: c.html for c in ch if c.word.seg_index not in t4_repl}
                        if not repl:
                            continue
                        new[i] = "".join(repl.get(k, t4_repl.get(k, sg.html)) for k, sg in enumerate(segs))
                        f_applied += 1
                n_f_skip = sum(1 for c in fr.changes if not c.applied) if adopt else 0
                say("AI 自由读音完成：与前四层不同 %d 处，%s" % (
                    len(fr.changes), ("已写入成品 %d 行" % f_applied) if adopt else "仅生成建议，成品未改动"))
                if adopt and n_f_skip:
                    say("其中 %d 处无法应用（全角数字或无法对齐），见清单" % n_f_skip)
                report_rv = merge_reviews(rv, fr)
            except Exception as e:
                say("AI 自由读音出错，已跳过（T4 的结果与清单不受影响）：%s" % e)
        path = write_report(proc, report_rv, jp_lines, cfg, os.path.join(out_dir, name + ".txt"), tags=locs,
                            mode=cfg["mode"], source_name=source or name)
        if path:
            say("已生成：" + path)
        return new
    except Exception as e:
        say("AI注音出错，已跳过（成品不受影响）：%s" % e)
        return ruby_lines


# ---------------------------------------------------------------- 命令行（第 2 步验收用，不进主程序）

def _load_processor():
    import importlib.util
    import types
    sys.modules.setdefault("icon_manager", types.SimpleNamespace(
        set_window_icon=lambda *a, **k: None, get_icon_path=lambda *a, **k: None))
    path = os.path.join(app_base_dir(), "phonetic_tool.py")
    spec = importlib.util.spec_from_file_location("phonetic_tool", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phonetic_tool"] = mod
    spec.loader.exec_module(mod)
    return mod.SubtitleProcessor()


def _corpus(limit):
    # 仅供命令行验收使用：按路径动态加载，避免 PyInstaller 静态扫描后把实验脚本打进安装包
    import importlib
    ex = importlib.import_module("ai_experiment")
    here = app_base_dir()
    lines = ex.load_ass(os.path.join(here, "白熊咖啡厅双语字幕文件"), 1) + ex.load_srt(os.path.join(here, "第一季字幕"), 1)
    texts = [t for _, t in lines]
    return texts[:limit] if limit else texts


def main(argv=None):
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="AI 复核注音 - 第 2 步验收")
    ap.add_argument("cmd", choices=["selfcheck", "count"])
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 行字幕，0 为全部")
    args = ap.parse_args(argv)
    proc = _load_processor()
    texts = _corpus(args.limit)
    if args.cmd == "selfcheck":
        n, bad = Replayer(proc, DEFAULT_CONFIG["skip_words"]).selfcheck(texts)
        print("自检：%d 个不同句子，不一致 %d" % (n, len(bad)))
        for text, mine, real in bad[:10]:
            print("  句子：%s\n    推演：%s\n    实际：%s" % (text, mine, real))
        return 1 if bad else 0
    cfg = load_config()
    veto = load_veto()
    plan = plan_requests(proc, texts, cfg, veto)
    print("含待复核词的不同句子（即请求数上限）：%d；待复核词 %d" % (len(plan), sum(len(w) for _, w in plan.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
