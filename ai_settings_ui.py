# -*- coding: utf-8 -*-
"""“AI 设置”窗口。独立文件：主程序只在用户点按钮时才导入，开关关闭的用户不会加载它。"""
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import ai_reviewer

# 每段单独一个标签；中英文之间不留空格（Tk 优先在空格处断行，会把“开启 AI”单独挤在一行）
NOTICE = ("开启AI注音后，满足条件的字幕原文会发送到你在此填写的接口，产生的费用由你自己承担。",
          "采用模式下，AI的选择会直接写入注音；请查看同时生成的“AI改动清单”，并标出不认可的条目。",
          "API Key以明文保存在本程序目录的ai_config.json，请勿把该文件发给他人。")
MODE_LABELS = {"adopt": "采用（AI 的选择直接写入注音）", "suggest": "仅建议（不改注音结果）"}


def open_settings(parent, set_icon=None, log=None):
    cfg = ai_reviewer.load_config()
    win = tk.Toplevel(parent)
    win.title("设置")
    win.transient(parent)
    win.resizable(False, False)
    if set_icon:
        try:
            set_icon(win)
        except Exception:
            pass

    frm = ttk.Frame(win, padding=12)
    frm.pack(fill=tk.BOTH, expand=True)

    v_url = tk.StringVar(value=cfg["base_url"])
    v_key = tk.StringVar(value=cfg["api_key"])
    v_model = tk.StringVar(value=cfg["model"])
    v_mode = tk.StringVar(value=MODE_LABELS[cfg["mode"]])
    v_conc = tk.StringVar(value=str(cfg["concurrency"]))
    v_think = tk.BooleanVar(value=cfg["thinking_off"])
    v_free = tk.BooleanVar(value=cfg.get("free_reading", False))
    v_pre = tk.StringVar(value=str(cfg["rule_pre_min"]))
    v_suf = tk.StringVar(value=str(cfg["rule_suf_min"]))
    v_warn = tk.StringVar(value="")

    def row(i, label, widget):
        ttk.Label(frm, text=label).grid(row=i, column=0, sticky="e", padx=(0, 8), pady=4)
        widget.grid(row=i, column=1, sticky="we", pady=4)

    row(1, "接口地址", ttk.Entry(frm, textvariable=v_url, width=52))
    key_entry = ttk.Entry(frm, textvariable=v_key, width=52, show="*")
    row(2, "API Key", key_entry)
    row(3, "模型名", ttk.Entry(frm, textvariable=v_model, width=52))
    row(4, "模式", ttk.Combobox(frm, textvariable=v_mode, values=list(MODE_LABELS.values()),
                                state="readonly", width=49))
    conc_bar = ttk.Frame(frm)                                # 并发数与规则前后文字数同一行，窗口高度不变
    ttk.Spinbox(conc_bar, textvariable=v_conc, from_=1, to=64, width=5).pack(side=tk.LEFT)
    ttk.Label(conc_bar, text="规则前文").pack(side=tk.LEFT, padx=(16, 4))
    ttk.Spinbox(conc_bar, textvariable=v_pre, from_=0, to=10, width=3, command=lambda: refresh_warn()).pack(side=tk.LEFT)
    ttk.Label(conc_bar, text="后文").pack(side=tk.LEFT, padx=(8, 4))
    ttk.Spinbox(conc_bar, textvariable=v_suf, from_=0, to=10, width=3, command=lambda: refresh_warn()).pack(side=tk.LEFT)
    ttk.Label(conc_bar, text="字").pack(side=tk.LEFT, padx=(4, 0))
    row(5, "并发数", conc_bar)
    checks = ttk.Frame(frm)                                  # 两个开关并排在同一行
    ttk.Checkbutton(checks, text="关闭思考", variable=v_think).pack(side=tk.LEFT)
    ttk.Checkbutton(checks, text="高精度注音", variable=v_free).pack(side=tk.LEFT, padx=(24, 0))
    row(6, "", checks)

    # TXT 导出：两项至少保留一项，取消最后一个时自动把另一项勾回去
    v_docx = tk.BooleanVar(value=cfg["txt_docx"])
    v_html = tk.BooleanVar(value=cfg["txt_html"])

    def keep_one(changed, other):
        if not changed.get() and not other.get():
            other.set(True)

    txt_bar = ttk.Frame(frm)
    ttk.Checkbutton(txt_bar, text="Word（docx）", variable=v_docx,
                    command=lambda: keep_one(v_docx, v_html)).pack(side=tk.LEFT)
    ttk.Checkbutton(txt_bar, text="网页（html）", variable=v_html,
                    command=lambda: keep_one(v_html, v_docx)).pack(side=tk.LEFT, padx=(24, 0))
    v_bi = tk.BooleanVar(value=cfg["txt_bilingual"])
    ttk.Checkbutton(txt_bar, text="双语（原文行+译文行）", variable=v_bi).pack(side=tk.LEFT, padx=(24, 0))
    row(7, "TXT 导出", txt_bar)

    status = tk.StringVar(value="")
    status_label = ttk.Label(frm, textvariable=status, foreground="#555555", justify=tk.LEFT)
    status_label.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _int(var, default, lo, hi):
        try:
            return min(hi, max(lo, int(var.get())))
        except ValueError:
            return default

    def refresh_warn(*_):
        both_zero = _int(v_pre, 2, 0, 10) == 0 and _int(v_suf, 2, 0, 10) == 0
        v_warn.set("规则前后文都为 0 时，生成的是全局规则；预检只检查当前文件，换作品可能误伤。" if both_zero else "")
        if both_zero:                                        # 平时不占高度，窗口大小不变
            warn_label.grid()
        else:
            warn_label.grid_remove()

    v_pre.trace_add("write", refresh_warn)
    v_suf.trace_add("write", refresh_warn)

    warn_label = ttk.Label(frm, textvariable=v_warn, foreground="#B00020", justify=tk.LEFT)
    warn_label.grid(row=10, column=0, columnspan=2, sticky="w")

    def collect():
        try:
            conc = int(v_conc.get())
        except ValueError:
            conc = cfg["concurrency"]
        mode = next((k for k, v in MODE_LABELS.items() if v == v_mode.get()), "adopt")
        return {"base_url": v_url.get().strip(), "api_key": v_key.get().strip(), "model": v_model.get().strip(),
                "mode": mode, "concurrency": min(64, max(1, conc)), "thinking_off": bool(v_think.get()),
                "free_reading": bool(v_free.get()),
                "txt_docx": bool(v_docx.get()), "txt_html": bool(v_html.get()),
                "txt_bilingual": bool(v_bi.get()),
                "rule_pre_min": _int(v_pre, cfg["rule_pre_min"], 0, 10),
                "rule_suf_min": _int(v_suf, cfg["rule_suf_min"], 0, 10)}

    def do_save(close=True):
        if not ai_reviewer.save_config(collect()):
            messagebox.showerror("设置", "保存失败：无法写入 ai_config.json", parent=win)
            return False
        if close:
            win.destroy()
        return True

    def do_test():
        if not do_save(close=False):
            return
        status.set("正在测试连接……")
        test_cfg = ai_reviewer.load_config()

        def work():
            ok, msg = ai_reviewer.test_connection(test_cfg)
            try:
                win.after(0, lambda: status.set(("✅ " if ok else "❌ ") + msg[:160]))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def do_clear():
        if messagebox.askyesno("清空缓存", "清空后，已复核过的句子下次会重新请求并产生费用。继续？", parent=win):
            n = ai_reviewer.clear_cache()
            status.set("已清空缓存 %d 条" % n)

    def do_apply():
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=win, title="选择标记后的 AI 清单",
            filetypes=[("AI 清单", "*_AI改动清单.txt *_AI建议.txt"), ("文本文件", "*.txt")])
        if not path:
            return
        pre = ai_reviewer.apply_report(path, dry_run=True)
        if not pre["ok"]:
            messagebox.showerror("应用清单", pre["message"], parent=win)
            return
        if not pre["rules_added"] and not pre["veto_added"]:
            detail = "\n".join("  [%s] %s" % x for x in pre["skipped"][:8])
            messagebox.showinfo("应用清单", "没有需要写入的内容（没有勾选、或都已写入过）。"
                                + (("\n\n跳过：\n" + detail) if detail else ""), parent=win)
            return
        msg = "将写入 context_rules.txt：%d 条；ai_veto.txt：%d 条。\n写入前会自动备份原文件。" % (
            len(pre["rules_added"]), len(pre["veto_added"]))
        if pre["skipped"]:
            msg += "\n\n将跳过 %d 条：\n" % len(pre["skipped"]) + "\n".join("  [%s] %s" % x for x in pre["skipped"][:6])
        if not messagebox.askyesno("应用清单", msg + "\n\n继续？", parent=win):
            return
        res = ai_reviewer.apply_report(path)
        (messagebox.showinfo if res["ok"] else messagebox.showerror)("应用清单", res["message"], parent=win)
        if log:
            log("应用清单：" + res["message"])
        if res["ok"] and messagebox.askyesno(
                "应用清单", "是否删除这份清单及其配套数据文件？\n\n" + path
                + "\n\n删除后无法再次应用这份清单。", parent=win):
            removed, err = ai_reviewer.delete_report(path)
            if err:
                messagebox.showwarning("应用清单", err, parent=win)
            elif log:
                log("已删除清单及配套数据：" + "、".join(os.path.basename(p) for p in removed))

    bar = ttk.Frame(frm)
    bar.grid(row=9, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(bar, text="测试连接", command=do_test).pack(side=tk.LEFT, padx=4)
    ttk.Button(bar, text="应用清单", command=do_apply).pack(side=tk.LEFT, padx=4)
    ttk.Button(bar, text="清空缓存", command=do_clear).pack(side=tk.LEFT, padx=4)
    ttk.Button(bar, text="保存", command=do_save).pack(side=tk.LEFT, padx=4)
    ttk.Button(bar, text="取消", command=win.destroy).pack(side=tk.LEFT, padx=4)

    # 说明文字最后创建：先按输入框撑开的宽度确定窗口宽度，再让说明按这个宽度换行
    win.update_idletasks()
    wrap = max(300, frm.winfo_reqwidth() - 24)
    status_label.configure(wraplength=wrap)      # 测试连接的长报错也按这个宽度换行，不再撑大窗口
    warn_label.configure(wraplength=wrap)
    refresh_warn()
    notice = ttk.Frame(frm)
    notice.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 10))
    for para in NOTICE:
        ttk.Label(notice, text=para, foreground="#B00020", justify=tk.LEFT, wraplength=wrap).pack(anchor="w", pady=1)

    win.grab_set()
    key_entry.focus_set()
    return win
