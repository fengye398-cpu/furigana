# 日英中文注音工具 - 架构文档

> **文档版本**: v1.0
> **最后更新**: 2025-11-25
> **主程序**: test_ui_test2_copy_6.py
> **用途**: 为字幕文件添加注音标注，支持日语假名、英语音标、中文拼音

---

## 📋 目录

1. [程序概述](#程序概述)
2. [核心架构](#核心架构)
3. [类和方法详解](#类和方法详解)
4. [处理流程](#处理流程)
5. [文件格式说明](#文件格式说明)
6. [最近重要修改](#最近重要修改)
7. [配置和扩展](#配置和扩展)
8. [常见问题](#常见问题)

---

## 程序概述

### 功能定位
这是一个**字幕注音工具**，主要功能：
- 为SRT字幕文件添加注音标注（日语假名/英语音标/中文拼音）
- 支持单语和双语字幕处理
- 输出多种格式：ASS、SRT、HTML
- 提供自定义替换规则功能

### 支持的语言模式
1. **日语模式** (默认): 汉字→假名注音
2. **英语模式**: 单词→音标注音
3. **中文模式**: 汉字→拼音注音

### 输出格式
- **ASS格式**: 用于视频播放器，支持样式和特效
- **SRT格式**: 通用字幕格式
- **HTML格式**: 用于网页显示，支持Ruby标签

---

## 核心架构

### 文件结构
```
D:\桌面\JP_EN注音\
├── test_ui_test2_copy_6.py      # 主程序
├── icon_manager.py              # 图标管理
├── build_config.py              # 打包配置
├── replacement_rules.txt        # 用户自定义替换规则
├── english_phonetic.db          # 英语音标数据库
├── 版本记录/
│   ├── v0.0.0/                  # 原始版本
│   └── v0.0.1/                  # 修改版本
└── ARCHITECTURE.md              # 本文档
```

### 依赖库
```python
# 核心依赖
import pysrt                    # SRT文件解析
from sudachipy import tokenizer # 日语分词
from sudachipy import dictionary
import pykakasi                 # 日语假名转换
from pypinyin import pinyin     # 中文拼音
import sqlite3                  # 英语音标数据库

# GUI依赖
import tkinter as tk
from tkinter import ttk
from tkinterdnd2 import DND_FILES, TkinterDnD  # 拖拽支持（可选）
```

---

## 类和方法详解

### 1. ReplacementRules 类
**位置**: 第49-116行
**用途**: 管理文本替换规则

#### 类变量
```python
default_rules = [...]  # 默认规则（受保护，不可修改）
user_rules = []        # 用户自定义规则
```

#### 核心方法
| 方法名 | 功能 | 返回值 |
|--------|------|--------|
| `get_all_rules()` | 获取所有规则（默认+用户） | list[tuple] |
| `get_default_rules()` | 获取默认规则（只读） | list[tuple] |
| `get_user_rules()` | 获取用户规则 | list[tuple] |
| `set_user_rules(new_rules)` | 设置用户规则 | None |
| `load_rules_from_file(filename)` | 从文件加载规则 | bool |
| `save_rules_to_file(filename)` | 保存规则到文件 | bool |

#### 规则格式
```python
# 每条规则是一个元组: (旧文本, 新文本)
('二|<に{\\k1}人|<にん', '二人|<ふたり')
```

---

### 2. ReplacementRulesWindow 类
**位置**: 第118-883行
**用途**: 替换规则管理窗口（GUI）

#### 主要功能
- 显示默认规则和用户规则
- 添加/编辑/删除用户规则
- 导入/导出规则文件
- 规则搜索过滤
- 规则格式校验

#### 关键方法
| 方法名 | 功能 |
|--------|------|
| `add_rule()` | 添加新规则 |
| `update_rule()` | 更新选中规则 |
| `remove_rule()` | 删除选中规则 |
| `import_rules()` | 从文件导入规则 |
| `export_rules()` | 导出规则到文件 |
| `validate_rule_format()` | 校验规则格式 |

---

### 3. SubtitleProcessor 类 ⭐核心类
**位置**: 第885-2238行
**用途**: 字幕处理的核心逻辑

#### 初始化 (第886-903行)
```python
def __init__(self, logger=None, progress_callback=None):
    self.tokenizer_obj = dictionary.Dictionary().create()  # Sudachi分词器
    self.kakasi = pykakasi.kakasi()                        # 假名转换器
    self.conv = self.kakasi.getConverter()
```

#### 核心方法分类

##### A. 文件校验和解析
| 方法名 | 行号 | 功能 |
|--------|------|------|
| `validate_srt_structure()` | 916-1142 | 校验SRT文件结构，返回块列表和语言类型 |
| `parse_subtitle_blocks()` | 1382-1425 | 解析字幕块 |

**validate_srt_structure() 详解**:
- 检查文件编码（必须UTF-8）
- 校验序号连续性
- 校验时间轴格式
- 判断单语/双语类型
- 返回: `(blocks, lang_type)`
  - `blocks`: 字幕块列表
  - `lang_type`: `'single'` 或 `'double'`

##### B. 文本提取和处理
| 方法名 | 行号 | 功能 |
|--------|------|------|
| `process_file_zh()` | 1144-1159 | 提取第一语言文本 |
| `process_file_jp()` | 1161-1181 | 提取第二语言文本 |
| `process_file_to_nohonngokashi()` | 1215-1227 | 提取纯文本（去除时间轴） |

##### C. 注音处理 ⭐⭐⭐
| 方法名 | 行号 | 功能 |
|--------|------|------|
| `japanese_to_kana_html()` | 1923-1954 | 日语汉字→假名注音 |
| `english_text_to_ruby()` | 814-835 | 英语单词→音标注音 |
| `chinese_text_to_ruby()` | 852-882 | 中文汉字→拼音注音 |

**japanese_to_kana_html() 详解**:
```python
# 输入: "今日は雨"
# 输出: "<ruby>今日<rp>(</rp><rt>きょう</rt><rp>)</rp></ruby><ruby>は<rp>(</rp><rt>は</rt><rp>)</rp></ruby><ruby>雨<rp>(</rp><rt>あめ</rt><rp>)</rp></ruby>"
```

##### D. HTML生成 ⭐⭐⭐ (最近修改)
| 方法名 | 行号 | 功能 |
|--------|------|------|
| `save_ruby_html()` | 1485-1605 | 生成HTML格式注音文件 |
| `convert_k1_to_ruby_html()` | 1259-1289 | 将{\\k1}格式转换为HTML ruby标签 |

**save_ruby_html() 详解** (重要修改):
```python
def save_ruby_html(self, ruby_input_path, output_file, lang_type='single', zh_kashi_path=None):
    """
    参数:
        ruby_input_path: 原文注音文本路径
        output_file: 输出HTML文件路径
        lang_type: 'single'(单语) 或 'double'(双语)
        zh_kashi_path: 译文文本路径（双语时使用）

    处理逻辑:
        1. 读取原文注音文本
        2. 将{\\k1}格式转换为HTML ruby标签
        3. 如果是双语(lang_type='double'):
           - 读取译文文本(zh_kashi_path)
           - 合并原文和译文
        4. 生成HTML文件

    行间距规则:
        - 单语: 每行后添加 <br><br>
        - 双语: 原文后 <br>，译文后 <br><br>
    """
```

##### E. ASS/SRT生成
| 方法名 | 行号 | 功能 |
|--------|------|------|
| `add_k1_to_lines()` | 1665-1681 | 为每行添加{\\k1}标记 |
| `replace_lines()` | 1683-1703 | 替换SRT文件中的文本行 |
| `srt_to_ass()` | 1705-1718 | SRT转ASS格式 |
| `merge_ass_files()` | 1720-1782 | 合并多个ASS文件 |

##### F. 辅助方法
| 方法名 | 行号 | 功能 |
|--------|------|------|
| `add_blank_lines_to_match_lines()` | 1622-1648 | 对齐两个文件的行数 |
| `replace_multiple_text()` | 1650-1663 | 批量文本替换 |
| `save_ruby_before_k1()` | 1247-1257 | 保存添加k1标记前的文本 |

##### G. 主处理流程 ⭐⭐⭐
| 方法名 | 行号 | 功能 |
|--------|------|------|
| `process_subtitles()` | 1956-2238 | 主处理流程入口 |

---

### 4. SubtitleToolGUI 类
**位置**: 第2240-4130行
**用途**: 图形用户界面

#### 主要组件
- 文件选择区域
- 输出目录设置
- 注音模式选择（日语/英语/中文）
- 导出格式选择
- 进度条和日志显示
- 帮助文档标签页

#### 关键方法
| 方法名 | 功能 |
|--------|------|
| `start_processing()` | 启动处理线程 |
| `_processing_thread()` | 后台处理线程 |
| `browse_input_file()` | 浏览选择输入文件 |
| `open_rules_window()` | 打开替换规则窗口 |
| `merge_subtitles()` | 合并字幕功能 |

---

## 处理流程

### 完整处理流程图

```
用户输入SRT文件
    ↓
[1] 文件校验 (validate_srt_structure)
    ├─ 检查编码 (UTF-8)
    ├─ 校验序号连续性
    ├─ 校验时间轴格式
    └─ 判断单语/双语
    ↓
[2] 文本提取
    ├─ 提取第一语言 (process_file_zh) → jp_kashi_path
    └─ 提取第二语言 (process_file_jp) → zh_kashi_path
    ↓
[3] 注音处理 (根据phonetic_mode)
    ├─ 日语模式: japanese_to_kana_html()
    ├─ 英语模式: english_text_to_ruby()
    └─ 中文模式: chinese_text_to_ruby()
    ↓
    生成: ruby_input_path (原文注音文本)
    ↓
[4] 应用替换规则
    ├─ 读取所有规则 (默认+用户)
    └─ 批量替换 (replace_multiple_text)
    ↓
    生成: ruby_modi_path (替换后的注音文本)
    ↓
[5] 分支处理
    ├─────────────────┬─────────────────┐
    ↓                 ↓                 ↓
[HTML路径]        [ASS路径]         [SRT路径]
    ↓                 ↓                 ↓
save_ruby_html()  add_k1_to_lines()  复制文件
    ├─ 读取原文      ├─ 添加{\\k1}     ↓
    ├─ 转换ruby标签  └─ replace_lines() 保留原格式
    └─ 双语时:           ↓
       读取译文      srt_to_ass()
       合并生成          ↓
    ↓              merge_ass_files()
生成HTML文件          ↓
                  生成ASS文件
```

### 关键路径说明

#### HTML生成路径 (独立)
```
ruby_modi_path (原文注音)
    ↓
save_ruby_before_k1() → ruby_before_k1_path
    ↓
save_ruby_html(ruby_before_k1_path, zh_kashi_path, lang_type)
    ├─ 读取原文注音
    ├─ 转换为HTML ruby标签
    └─ 如果双语: 读取zh_kashi_path并合并
    ↓
生成: jp_ruby_html_path
```

#### ASS生成路径 (独立)
```
ruby_modi_path (原文注音)
    ↓
add_k1_to_lines() → 添加{\\k1}标记
    ↓
replace_lines(ruby_modi_path, output_file_path_zh, jp_ruby_srt_path)
    ↓
srt_to_ass(jp_ruby_srt_path, jp_ruby_ass_path)
    ↓
srt_to_ass(output_file_path_jp, zh_ass_path)
    ↓
merge_ass_files([jp_ruby_ass_path, zh_ass_path], merged_ass_path)
    ↓
生成: merged_ass_path
```

**重要**: HTML和ASS路径完全独立，互不影响！

---

## 文件格式说明

### 输入格式: SRT

#### 单语字幕
```srt
1
00:00:01,000 --> 00:00:03,000
今日は雨です

2
00:00:04,000 --> 00:00:06,000
外で遊べません
```

#### 双语字幕
```srt
1
00:00:01,000 --> 00:00:03,000
今日は雨です
今天下雨

2
00:00:04,000 --> 00:00:06,000
外で遊べません
不能在外面玩
```

### 中间格式: Ruby文本

#### 原始注音格式 (ruby_input_path)
```
<ruby>今日<rp>(</rp><rt>きょう</rt><rp>)</rp></ruby><ruby>は<rp>(</rp><rt>は</rt><rp>)</rp></ruby><ruby>雨<rp>(</rp><rt>あめ</rt><rp>)</rp></ruby>です
```

#### k1标记格式 (ruby_modi_path)
```
{\\k1}今日|<きょう{\\k1}は{\\k1}雨|<あめ{\\k1}です
```

### 输出格式

#### HTML格式
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        ruby { margin: 0 2px; }
        rt { color: #999999; }
        .original-text { color: inherit; }
        .translation-text { color: #595959; font-size: 18px; }
    </style>
</head>
<body>
    <p>
        <span class="original-text"><ruby>今日<rt>きょう</rt></ruby>は<ruby>雨<rt>あめ</rt></ruby>です</span><br>
        <span class="translation-text">今天下雨</span><br><br>
    </p>
</body>
</html>
```

#### ASS格式
```ass
[Script Info]
Title: 自动生成日文汉字注音字幕文件
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Style: Default,黑体,50,&H00FFFFFF,...

[Events]
Dialogue: 0,00:00:01.00,00:00:03.00,Default,,0,0,0,,{\\k1}今日|<きょう{\\k1}は{\\k1}雨|<あめ{\\k1}です
Dialogue: 0,00:00:01.00,00:00:03.00,Default_ZH,,0,0,0,,今天下雨
```

---

## 最近重要修改

### 修改日期: 2025-11-25
### 修改内容: HTML双语支持优化

#### 问题背景
之前的实现将中文翻译合并到`ruby_lines`中，导致ASS文件生成时时间轴错位。

#### 解决方案
**将HTML和ASS生成路径完全分离**

#### 具体修改

##### 1. 恢复原始文本处理流程 (第2072-2084行)
```python
# 修改前: 合并中文到ruby_lines
for i, jp_line in enumerate(jp_lines):
    ruby_html, kanji_count = self.japanese_to_kana_html(jp_line)
    ruby_lines.append(ruby_html)
    if lang_type == 'double' and i < len(zh_lines):
        ruby_lines.append(zh_lines[i])  # ❌ 这导致ASS错误
        ruby_lines.append('')

# 修改后: 只处理日文
for i, jp_line in enumerate(jp_lines):
    ruby_html, kanji_count = self.japanese_to_kana_html(jp_line)
    ruby_lines.append(ruby_html)  # ✅ 只保留日文注音
```

##### 2. 修改save_ruby_html()方法 (第1485-1512行)
```python
# 添加zh_kashi_path参数
def save_ruby_html(self, ruby_input_path, output_file, lang_type='single', zh_kashi_path=None):
    # 读取原文注音
    lines = converted_content.split('\n')
    lines = [line.strip() for line in lines if line.strip()]

    # 双语时读取中文翻译
    zh_lines = []
    if lang_type == 'double' and zh_kashi_path:
        with open(zh_kashi_path, 'r', encoding='utf-8-sig') as f:
            zh_lines = [line.strip() for line in f.readlines() if line.strip()]

    # 生成HTML时合并
    for i, line in enumerate(lines):
        if lang_type == 'single':
            formatted_lines.append(f'<span class="original-text">{line}</span><br><br>')
        else:
            formatted_lines.append(f'<span class="original-text">{line}</span><br>')
            if i < len(zh_lines):
                formatted_lines.append(f'<span class="translation-text">{zh_lines[i]}</span><br><br>')
```

##### 3. 更新方法调用 (第2137行)
```python
# 修改前
self.save_ruby_html(ruby_before_k1_path, jp_ruby_html_path, lang_type)

# 修改后
self.save_ruby_html(ruby_before_k1_path, jp_ruby_html_path, lang_type, zh_kashi_path)
```

#### 修改效果
- ✅ HTML双语显示正常
- ✅ ASS时间轴对齐正确
- ✅ 两种输出格式互不干扰
- ✅ 代码改动最小化

#### 变量命名说明
虽然变量名为`jp_kashi_path`和`zh_kashi_path`，但实际上：
- `jp_kashi_path`: 原文路径（可以是任何语言）
- `zh_kashi_path`: 译文路径（可以是任何语言）

这些名称只是历史遗留，不影响功能的语言无关性。

---

## 配置和扩展

### 替换规则配置

#### 规则文件格式 (replacement_rules.txt)
```
旧文本[TAB]新文本
二|<に{\\k1}人|<にん[TAB]二人|<ふたり
入|<い{\\k1}って[TAB]入|<はい{\\k1}って
```

#### 添加自定义规则
1. 通过GUI: 点击"自定义搜索替换规则"按钮
2. 手动编辑: 修改`replacement_rules.txt`文件

### HTML样式自定义

#### 修改位置: 第1522-1560行
```python
# 注音样式
const phoneticStyle = {
    color: "#999999",      # 注音颜色
    fontStyle: ""          # 字体样式
};

# 原文样式
const originalStyle = {
    color: "",             # 原文颜色（空=默认）
    fontSize: ""           # 字体大小（空=默认）
};

# 译文样式
const translationStyle = {
    color: "#595959",      # 译文颜色
    fontSize: "18px"       # 译文字体大小
};
```

#### Ruby标签间距
```css
ruby {
    margin: 0 2px;  /* 修改此值调整注音间距 */
}
```

### ASS样式配置

#### 修改位置: 第1720-1782行 (merge_ass_files方法)
```python
# 内置ASS样式定义
ass_header = """[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, ...
Style: Default,黑体,50,&H00FFFFFF,...
Style: Default_ZH,黑体,35,&H00FFFFFF,...
"""
```

---

## 常见问题

### Q1: 为什么ASS文件时间轴错位？
**A**: 确保使用最新版本（2025-11-25之后）。旧版本存在HTML和ASS路径混淆的问题。

### Q2: 如何添加新的语言支持？
**A**: 需要修改以下部分：
1. 添加新的注音函数（参考`japanese_to_kana_html`）
2. 在`process_subtitles`方法中添加新的`phonetic_mode`分支
3. 在GUI中添加新的模式选项

### Q3: 双语字幕如何判断？
**A**: 在`validate_srt_structure`方法中：
- 如果字幕块有2行内容 → 双语 (`'double'`)
- 如果字幕块有1行内容 → 单语 (`'single'`)

### Q4: 替换规则不生效？
**A**: 检查：
1. 规则格式是否正确（使用TAB分隔）
2. 规则是否保存到文件
3. 规则顺序（默认规则先执行，用户规则后执行）

### Q5: HTML中译文颜色如何修改？
**A**: 修改第1555-1558行的`translationStyle`配置：
```javascript
const translationStyle = {
    color: "#你的颜色",
    fontSize: "你的字号"
};
```

### Q6: 如何禁用某些默认替换规则？
**A**: 默认规则受保护，不能直接删除。可以添加用户规则来"反向替换"：
```
二人|<ふたり[TAB]二|<に{\\k1}人|<にん
```

### Q7: 文件编码错误如何解决？
**A**: 程序只支持UTF-8编码（带BOM或不带BOM）。解决方法：
1. 用记事本打开 → 另存为 → 编码选择UTF-8
2. 用VS Code → 右下角点击编码 → 选择UTF-8保存

### Q8: 如何批量处理多个文件？
**A**: 当前版本不支持批量处理。需要逐个文件处理。

---

## 附录

### 关键文件路径变量

| 变量名 | 含义 | 示例 |
|--------|------|------|
| `input_srt_path` | 输入SRT文件路径 | `D:\字幕\1.srt` |
| `output_dir` | 输出目录 | `D:\字幕\` |
| `jp_kashi_path` | 原文纯文本路径 | `D:\字幕\1_jp_kashi.txt` |
| `zh_kashi_path` | 译文纯文本路径 | `D:\字幕\1_zh_kashi.txt` |
| `ruby_input_path` | 原始注音文本 | `D:\字幕\1_ruby.txt` |
| `ruby_modi_path` | 替换后注音文本 | `D:\字幕\1_ruby_modi.txt` |
| `ruby_before_k1_path` | k1标记前文本 | `D:\字幕\1_ruby_before_k1.txt` |
| `jp_ruby_html_path` | HTML输出路径 | `D:\字幕\1_jp_ruby.html` |
| `jp_ruby_srt_path` | 注音SRT路径 | `D:\字幕\1_jp_ruby.srt` |
| `jp_ruby_ass_path` | 注音ASS路径 | `D:\字幕\1_jp_ruby.ass` |
| `zh_ass_path` | 译文ASS路径 | `D:\字幕\1_zh.ass` |
| `merged_ass_path` | 合并ASS路径 | `D:\字幕\1_merged.ass` |

### 进度百分比对应

| 进度值 | 阶段 |
|--------|------|
| 0% | 开始处理 |
| 10% | 文件校验完成 |
| 30% | 文本提取完成 |
| 50% | 注音处理完成 |
| 70% | 替换规则应用完成 |
| 90% | 格式转换完成 |
| 100% | 全部完成 |

---

## 版本历史

### v0.0.1 (2025-11-25)
- ✅ 修复HTML双语支持
- ✅ 分离HTML和ASS生成路径
- ✅ 优化代码结构

### v0.0.0 (初始版本)
- ✅ 基础注音功能
- ✅ 单语/双语支持
- ✅ ASS/SRT/HTML输出
- ✅ 自定义替换规则

---

## 联系和反馈

如有问题或建议，请通过以下方式反馈：
- 项目路径: `D:\桌面\JP_EN注音\`
- 文档更新: 修改本文件 `ARCHITECTURE.md`

---

**文档结束**
