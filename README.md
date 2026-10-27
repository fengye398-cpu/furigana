1.看剧学语言的注音神器：给中文、日文、英文字幕一键加注音（假名/拼音/音标），支持 SRT 与 TXT；SRT 可导出 ASS、HTML，TXT 可导出 Word、HTML。
Add furigana, pinyin & IPA to Chinese, Japanese and English subtitles in one click. Learn languages from TV shows and films. SRT → ASS/HTML, TXT → Word/HTML.

2.教程网址:https://www.bilibili.com/video/BV1etyrBnE96

3.参考网址:https://www.bilibili.com/video/BV1cU411Z7Mx/

<img width="1121" height="347" alt="英语音标标注" src="https://github.com/user-attachments/assets/10cb0986-84b9-473e-b6f1-c642ab8c7bea" />
<img width="1161" height="432" alt="日语假名标注" src="https://github.com/user-attachments/assets/857256de-a776-4181-a4bb-6d97b0ba082f" />
<img width="1084" height="347" alt="汉语拼音标注" src="https://github.com/user-attachments/assets/f87c23c9-9364-4abc-b317-b5dea0928fbb" />
# 本地开发环境搭建

本文说明如何在本机从源码运行、调试和打包本工具。程序结构与设计见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 环境要求

- Windows 10 / 11。卡拉OK特效版（`kara_fx.py`）依赖 Windows GDI，其余功能与平台无关。
- Python 3.10（项目在 3.10.10 上开发）。
- 安装 Python 时勾选 tkinter（界面用）。

## 1. 获取代码

```bash
git clone <仓库地址>
cd <仓库目录>
```

## 2. 创建虚拟环境并安装依赖

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

国内网络可加镜像：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

`requirements.txt` 的内容：

| 包 | 版本 | 用途 |
|---|---|---|
| `pysrt` | 1.1.2 | SRT 解析 |
| `chardet` | 5.2.0 | 编码检测 |
| `sudachipy` | 0.6.10 | 日语分词 |
| `sudachidict-core` | 20250825 | Sudachi 词典 |
| `pykakasi` | 2.3.0 | 日语假名转换 |
| `pypinyin` | 0.55.0 | 中文拼音 |
| `tkinterdnd2` | 0.3.0 | 拖拽（可选，缺失时界面仍可用，只是不能拖拽） |
| `pyinstaller` | 6.13.0 | 打包 |

可选：`Pillow`。只在 `icon_manager.py` 需要程序生成图标时用到，缺失时有回退，不装也能运行。

验证安装：

```bat
python -c "import pysrt, chardet, sudachipy, pykakasi, pypinyin, tkinterdnd2; from sudachipy import dictionary; dictionary.Dictionary().create(); print('ok')"
```

## 3. 准备数据文件

程序从脚本（或 exe）同级目录读取下列文件。缺失时按下表回退，不会报错中断。

| 文件 | 作用 | 缺失时 |
|---|---|---|
| `jp_dict.sqlite` | 日语词典 + JPDB 词频 | 日语注音只剩上下文规则和 pykakasi，准确率明显下降 |
| `en_dict.sqlite` | 英语音标库 | 英语模式没有音标 |
| `context_rules.txt` | 上下文读音规则 | 该层不启用 |
| `replacement_rules.txt` | 用户替换规则 | 只用内置默认规则 |
| `docx_style.json` | docx 样式 | 用内置默认样式 |
| `ass_style.json` | 卡拉OK特效版样式 | 用 `_merged.ass` 里的样式 |
| `字体/` | 思源黑体 CN Medium | 特效版坐标可能错位（程序会在日志提示） |

仓库里 `context_rules_example_file.txt`、`replacement_rules_example_file.txt` 是规则文件的示例，复制并改名即可：

```bat
copy context_rules_example_file.txt context_rules.txt
copy replacement_rules_example_file.txt replacement_rules.txt
```

用户规则的语法见 ARCHITECTURE.md 第 4.2 节（层 ①）和第 5.1 节。

字体需要安装到系统才能让特效版的字宽计算准确，安装方法见 `字体/安装说明.txt`。

## 4. 运行

```bat
.venv\Scripts\activate
python phonetic_tool.py
```

在项目根目录运行，保证上面的数据文件能被找到。启动后界面日志会写出实际读取了哪些文件。

## 5. 开发时常用的操作

### 查看分词结果

勾选界面的"导出日文分词"，或在 Python 里直接调用：

```python
from sudachipy import dictionary, tokenizer
t = dictionary.Dictionary().create()
for m in t.tokenize("部屋に入れない", tokenizer.Tokenizer.SplitMode.C):
    print(m.surface(), m.reading_form(), m.part_of_speech()[0])
```

写 `context_rules.txt` 前先看分词，原因见 ARCHITECTURE.md 第 4.6 节。

### 不开界面调用注音函数

`SubtitleProcessor` 的构造参数都有默认值，可以脱离界面调用：

```python
from phonetic_tool import SubtitleProcessor
p = SubtitleProcessor()
html, kanji_count = p.japanese_to_kana_html("私たちは種を見た")
print(html)
```

### 修改后的回归检查

读音决策链改动容易牵连别处。至少用这些句子对比改动前后的输出：

私たち、種、誰が、一日中、入れない、辛い、何も、何の。

### 模块依赖关系

```
phonetic_tool.py ──┬─ icon_manager.py
                   ├─ txt_ruby.py ── docx_ruby.py      （TXT 流程；函数内才 import）
                   ├─ docx_ruby.py                      （SRT 导出 docx）
                   ├─ kara_fx.py                        （卡拉OK特效版）
                   └─ ai_settings_ui.py ── ai_reviewer.py （点击"设置"时才 import）
```

`txt_ruby`、`docx_ruby`、`ai_*` 都是**函数内导入**。新增类似的延迟导入模块时，要同步加到打包配置的 `hiddenimports`（见下）。

### 调试 AI 复核

AI 功能默认关闭。开发时：

1. 打开界面的"设置"，填接口地址、API Key、模型名，点"测试连接"。
2. 勾选主界面的"AI注音"后处理一个日语 SRT 或 TXT。
3. 生成的 `ai_config.json`（含 Key）、`ai_cache.json`、`ai_veto.txt` 都落在程序目录，**不要提交**。

没有 Key 时，想验证请求逻辑可以起一个本地假服务器，把"接口地址"指向它（接口是 OpenAI 兼容的 `POST /chat/completions`）。

## 6. 打包成 exe

```bat
.venv\Scripts\activate
python build_config.py
```

- 脚本会清理 `build/`、`dist/`，重新生成 `phonetic_tool.spec`，调用 PyInstaller，然后把词典、规则文件、样式文件、字体、pykakasi 数据复制到 `dist\日英中文注音工具\`。
- 脚本会自动切换到项目下的 `.venv`（如果存在且当前不在虚拟环境）。
- 只检查依赖不打包：`python build_config.py --check`；只清理：`python build_config.py --clean`。
- 打包是**目录模式**（onedir），分发时压缩整个 `dist\日英中文注音工具\` 文件夹。

注意：

- **新增模块要同步**：`build_config.py` 里 `create_spec_file` 的 `hiddenimports` 列表。`phonetic_tool.spec` 是它生成的，直接改 spec 会在下次打包时被覆盖。
- 打包临时目录在系统盘，磁盘空间不足会失败（`No space left on device`）。
- 打包后的 exe 里 TXT 流程、AI 模块的 HTTPS 调用**还没有系统性实测**，改动后请在包里点一遍。
- 分发前删除 `dist` 下的 `ai_config.json`、`ai_cache.json`、`ai_veto.txt`、`*.bak_*`，其中含 API Key 和个人规则。

## 7. 提交代码前

不要提交下列内容（建议写进 `.gitignore`）：

```
.venv/
build/
dist/
__pycache__/
config.json
ai_config.json
ai_cache.json
ai_veto.txt
*.bak_*
.claude/
```

`.gitignore` 对**已被跟踪的文件无效**。如果 `.venv/`、`build/`、`dist/` 已经在 git 索引里，需要先移出：

```bash
git rm -r --cached .venv build dist
```

`jp_dict.sqlite`（约 44 MB）和 `en_dict.sqlite`（约 35 MB）体积较大。公开仓库建议放到 GitHub Release 附件，或用 Git LFS，并在 README 说明下载位置。词典源文件（各 `*.zip`）和测试字幕同理，且要先确认许可允许再分发（见 ARCHITECTURE.md 第 10 节）。

## 8. 已知缺口

开发者需要知道的几处，仓库目前缺失或不一致：

- **`build_jp_dict.py` 不在仓库里**。`jp_dict.sqlite` 是由它从 `jitendex-yomitan.zip`、`明镜日汉双解词典_Yomitan 1.4.4.zip`、`小学馆日中辞典第三版_yomitan.zip`、`JPDB_v2.2_Frequency_Kana.zip` 生成的，现在没有脚本可以重新生成。要改词典内容，只能直接改 sqlite，或先把该脚本补回仓库。
- **`show_tokens.py` 不在仓库里**。ARCHITECTURE.md 提到的 `python show_tokens.py -s 句子` 暂时不可用，请用上面的 Python 片段代替。
- **测试脚本不在仓库里**。ARCHITECTURE.md 第 7.17 节提到的模块自测 / 集成自测（`test_ai_reviewer.py`、`test_ai_integration.py`）当前目录里没有。
- 项目里的 `项目依赖.txt` 写的 PyInstaller 版本是 6.16.0，`requirements.txt` 是 6.13.0，以后者为准。






