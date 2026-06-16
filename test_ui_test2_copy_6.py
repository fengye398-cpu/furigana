import os
import sys
import re
import codecs
import shutil
import platform
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pysrt
from sudachipy import tokenizer
from sudachipy import dictionary
from icon_manager import set_window_icon, get_icon_path
#增加了自定义搜索替换规则的功能，兼容单语与双语
#修复保存规则逻辑，只保存用户规则，不保存搜索替换规则的结果，增加自定义规则实时显示
#增加输入文件编码报警提示
#屏蔽了序号块映射
#增加了详细报警提示；增加字幕块序号和时间轴校验提示；增加了导入规则校验提示
#新增格式导出选项
#新增合并字幕窗口，但还有BUG
# 尝试导入拖拽支持库
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DRAG_DROP_AVAILABLE = True
except ImportError:
    DRAG_DROP_AVAILABLE = False

# 获取资源文件路径的函数（支持打包后的EXE）
def get_resource_path(relative_path):
    """获取资源文件的绝对路径，支持开发环境和打包后的EXE"""
    try:
        # PyInstaller创建临时文件夹，并将路径存储在_MEIPASS中
        base_path = sys._MEIPASS
    except Exception:
        # 开发环境中使用当前目录
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# 设置中文字体支持
def set_ui_font():
    if platform.system() == "Windows":
        return ("微软雅黑", 10)
    elif platform.system() == "Darwin":  # macOS
        return ("Hiragino Sans GB", 12)
    else:  # Linux
        return ("WenQuanYi Micro Hei", 10)

# 静态汉字映射替换规则 - 改为类变量，方便修改
class ReplacementRules:
    # 默认规则（受保护的）
    default_rules = [
        ('<br/>', '\n{\\k1}'),
        ('<ruby>', '{\\k1}'),
        ('<rp>(</rp><rt>', '|<'),
        ('</rt><rp>)</rp></ruby>', '{\\k1}'),
        ('{\\k1}{\\k1}', '{\\k1}'),
        ('二|<に{\\k1}人|<にん', '二人|<ふたり'),
        ('入|<い{\\k1}って', '入|<はい{\\k1}って'),
        ('小|<ちー{\k1}', '小|<ちい{\k1}')      
    ]
    
    # 用户规则
    user_rules = []
    
    @classmethod
    def get_all_rules(cls):
        """获取所有规则（先默认规则，后用户规则）"""
        return cls.default_rules.copy() + cls.user_rules.copy()
    
    @classmethod
    def get_default_rules(cls):
        """获取默认规则（只读）"""
        return cls.default_rules.copy()
    
    @classmethod
    def get_user_rules(cls):
        """获取用户规则"""
        return cls.user_rules.copy()
    
    @classmethod
    def set_user_rules(cls, new_rules):
        """设置用户规则"""
        cls.user_rules = new_rules.copy()
    
    @classmethod
    def load_rules_from_file(cls, filename="replacement_rules.txt"):
        """从文件加载用户规则"""
        try:
            if os.path.exists(filename):
                with open(filename, "r", encoding="utf-8") as f:
                    user_rules = []
                    for line in f:
                        line = line.strip()
                        if line:
                            parts = line.split("\t", 1)
                            if len(parts) == 2:
                                user_rules.append((parts[0], parts[1]))
                cls.user_rules = user_rules
                return True
            return False
        except Exception as e:
            print(f"加载规则文件失败: {e}")
            return False
    
    @classmethod
    def save_rules_to_file(cls, filename="replacement_rules.txt"):
        """保存用户规则到文件"""
        try:
            with open(filename, "w", encoding="utf-8") as f:
                for old, new in cls.user_rules:
                    f.write(f"{old}\t{new}\n")
            return True
        except Exception as e:
            print(f"保存规则文件失败: {e}")
            return False

# 替换规则窗口
class ReplacementRulesWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.parent = parent
        self.title("自定义多音字注音规则")

        self.geometry("700x650")

        # 设置窗口图标
        set_window_icon(self)

        self.font = ('SimHei', 10)  # 假设已设置字体
        
        self.fullscreen = False
        self.bind("<F11>", self.toggle_fullscreen)
        self.bind("<Escape>", self.exit_fullscreen)
        
        self.create_widgets()
        self.load_saved_rules()
        self.update_rule_count_display()
    
    def create_widgets(self):
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 标题和规则计数显示
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(title_frame, text="自定义多音字注音规则", font=("", 12, "bold")).pack(side=tk.LEFT)
        self.rule_count_label = ttk.Label(title_frame, text="用户规则总数: 0", foreground="#1E88E5")
        self.rule_count_label.pack(side=tk.LEFT, padx=15)
        ttk.Button(title_frame, text="全屏(F11)", command=self.toggle_fullscreen).pack(side=tk.RIGHT)
        
        ttk.Label(main_frame, text="灰色条目为默认规则（不可编辑/删除），黑色条目为用户规则").pack(anchor=tk.W)
        
        # 搜索框区域
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(search_frame, text="搜索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.search_var.trace("w", self.filter_rules)
        
        # 树状视图框架
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        columns = ("old", "new")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=15)
        self.tree.heading("old", text="原始文本")
        self.tree.heading("new", text="替换文本")
        self.tree.column("old", width=300, minwidth=150)
        self.tree.column("new", width=300, minwidth=150)
        
        v_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        self.tree.bind("<Double-1>", self.on_double_click)
        
        # 输入框区域
        input_frame = ttk.LabelFrame(main_frame, text="编辑规则")
        input_frame.pack(fill=tk.X, pady=5)
        
        old_frame = ttk.Frame(input_frame)
        old_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(old_frame, text="原始文本:").pack(side=tk.LEFT)
        self.old_text = ttk.Entry(old_frame)
        self.old_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        new_frame = ttk.Frame(input_frame)
        new_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(new_frame, text="替换文本:").pack(side=tk.LEFT)
        self.new_text = ttk.Entry(new_frame)
        self.new_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.old_text.bind("<KeyRelease>", self.on_input_change)
        self.new_text.bind("<KeyRelease>", self.on_input_change)
        self.old_text.bind("<Return>", self.on_enter_press)
        self.new_text.bind("<Return>", self.on_enter_press)
        
        # 按钮区域
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        left_btn_frame = ttk.Frame(button_frame)
        left_btn_frame.pack(side=tk.LEFT)
        
        self.add_btn = ttk.Button(left_btn_frame, text="添加规则", command=self.add_rule)
        self.add_btn.pack(side=tk.LEFT, padx=2)
    
        self.update_btn = ttk.Button(left_btn_frame, text="更新规则", command=self.update_rule)
        self.update_btn.pack(side=tk.LEFT, padx=2)
        
        self.remove_btn = ttk.Button(left_btn_frame, text="删除选中规则", command=self.remove_rule)
        self.remove_btn.pack(side=tk.LEFT, padx=2)
        
        self.clear_btn = ttk.Button(left_btn_frame, text="清空用户规则", command=self.clear_user_rules)
        self.clear_btn.pack(side=tk.LEFT, padx=2)
        
        right_btn_frame = ttk.Frame(button_frame)
        right_btn_frame.pack(side=tk.RIGHT)
        
        self.import_btn = ttk.Button(right_btn_frame, text="导入用户规则", command=self.import_rules)
        self.import_btn.pack(side=tk.LEFT, padx=2)
        
        self.export_btn = ttk.Button(right_btn_frame, text="导出用户规则", command=self.export_rules)
        self.export_btn.pack(side=tk.LEFT, padx=2)
        
        self.save_btn = ttk.Button(right_btn_frame, text="保存规则", command=self.save_rules)
        self.save_btn.pack(side=tk.LEFT, padx=2)
        
        self.close_btn = ttk.Button(right_btn_frame, text="关闭", command=self.destroy)
        self.close_btn.pack(side=tk.LEFT, padx=2)
    
    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.attributes("-fullscreen", self.fullscreen)
        return "break"
    
    def exit_fullscreen(self, event=None):
        self.fullscreen = False
        self.attributes("-fullscreen", False)
        return "break"
    
    def add_rule_to_list(self, old_val, new_val, is_default=False):
        item_id = self.tree.insert("", tk.END, values=(old_val, new_val))
        if is_default:
            self.tree.item(item_id, tags=("default",))
        else:
            self.tree.item(item_id, tags=("user",))
        
        self.tree.tag_configure("default", foreground="gray")
        self.tree.tag_configure("user", foreground="black")
    
    def filter_rules(self, *args):
        search_text = self.search_var.get().lower()
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for old, new in ReplacementRules.get_default_rules():
            if search_text in old.lower() or search_text in new.lower():
                self.add_rule_to_list(old, new, is_default=True)
        
        for old, new in ReplacementRules.get_user_rules():
            if search_text in old.lower() or search_text in new.lower():
                self.add_rule_to_list(old, new, is_default=False)
    
    def on_input_change(self, event=None):
        old_val = self.old_text.get().strip()
        new_val = self.new_text.get().strip()
        
        if old_val and not new_val:
            for item in self.tree.get_children():
                if self.tree.item(item, "values")[0] == old_val:
                    self.new_text.delete(0, tk.END)
                    self.new_text.insert(0, self.tree.item(item, "values")[1])
                    break
    
    def on_enter_press(self, event=None):
        selected_items = self.tree.selection()
        if selected_items:
            self.update_rule()
        else:
            self.add_rule()
        return "break"

    def validate_rule_format(self, old_text, new_text):
        """验证规则格式的合法性"""
        errors = []
        warnings = []

        # 基本格式检查
        if not old_text or not new_text:
            errors.append("原始文本和替换文本都不能为空")
            return errors, warnings

        # 检查是否包含制表符（会影响文件保存格式）
        if '\t' in old_text:
            errors.append("原始文本不能包含制表符(Tab)，这会影响规则文件的保存格式")
        if '\t' in new_text:
            errors.append("替换文本不能包含制表符(Tab)，这会影响规则文件的保存格式")

        # 检查是否包含换行符
        if '\n' in old_text or '\r' in old_text:
            errors.append("原始文本不能包含换行符")
        if '\n' in new_text or '\r' in new_text:
            errors.append("替换文本不能包含换行符")

        # 检查长度限制（防止过长的规则影响性能）
        if len(old_text) > 500:
            warnings.append("原始文本过长(>500字符)，可能影响处理性能")
        if len(new_text) > 500:
            warnings.append("替换文本过长(>500字符)，可能影响处理性能")

        # 检查是否为纯空白字符
        if old_text.isspace():
            errors.append("原始文本不能为纯空白字符")
        if new_text.isspace():
            errors.append("替换文本不能为纯空白字符")

        # 检查是否相同（无意义的规则）
        if old_text == new_text:
            warnings.append("原始文本和替换文本相同，此规则无实际效果")

        # 检查潜在的循环替换风险
        if old_text in new_text and len(old_text) < len(new_text):
            warnings.append("替换文本包含原始文本，可能存在循环替换风险")

        # 检查特殊字符组合（基于默认规则的模式）
        special_patterns = ['{\\k1}', '|<', '</', '<ruby>', '</ruby>']
        has_special = any(pattern in old_text or pattern in new_text for pattern in special_patterns)
        if has_special:
            warnings.append("检测到特殊字符组合，请确认这是多音字注音相关的规则")

        return errors, warnings
    
    def add_rule(self):
        old_val = self.old_text.get().strip()
        new_val = self.new_text.get().strip()

        # 格式验证
        errors, warnings = self.validate_rule_format(old_val, new_val)

        # 如果有错误，显示错误信息并返回
        if errors:
            error_message = "❌ 规则格式错误：\n\n" + "\n".join(f"• {error}" for error in errors)
            if warnings:
                error_message += "\n\n⚠️ 警告：\n" + "\n".join(f"• {warning}" for warning in warnings)
            error_message += "\n\n💡 请修正错误后重试。"
            messagebox.showerror("格式错误", error_message)
            return

        # 如果有警告，询问用户是否继续
        if warnings:
            warning_message = "⚠️ 发现以下警告：\n\n" + "\n".join(f"• {warning}" for warning in warnings)
            warning_message += "\n\n❓ 是否仍要添加此规则？"
            if not messagebox.askyesno("格式警告", warning_message):
                return

        # 格式验证通过，执行添加逻辑
        if old_val and new_val:
            user_rules = ReplacementRules.get_user_rules()
            existing_index = None
            for i, (r_old, r_new) in enumerate(user_rules):
                if r_old == old_val:
                    existing_index = i
                    break

            if existing_index is not None:
                if messagebox.askyesno("确认", "已存在相同原始文本的用户规则，是否覆盖？"):
                    user_rules[existing_index] = (old_val, new_val)
                    ReplacementRules.set_user_rules(user_rules)
                    self.filter_rules()

                    # 自动定位到覆盖后的规则
                    for item in self.tree.get_children():
                        values = self.tree.item(item, "values")
                        if values[0] == old_val and values[1] == new_val:
                            self.tree.selection_set(item)  # 选中该项
                            self.tree.see(item)            # 滚动到可见
                            break

                    self.old_text.delete(0, tk.END)
                    self.new_text.delete(0, tk.END)
                    self.update_rule_count_display()
            else:
                user_rules.append((old_val, new_val))
                ReplacementRules.set_user_rules(user_rules)
                self.filter_rules()

                # 自动定位到新添加的规则
                for item in self.tree.get_children():
                    values = self.tree.item(item, "values")
                    if values[0] == old_val and values[1] == new_val:
                        self.tree.selection_set(item)  # 选中该项
                        self.tree.see(item)            # 滚动到可见
                        break

                self.old_text.delete(0, tk.END)
                self.new_text.delete(0, tk.END)
                self.update_rule_count_display()
        else:
            messagebox.showwarning("警告", "原始文本和替换文本都不能为空")
    
    def update_rule(self):
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("警告", "请先选择要更新的规则")
            return

        item = selected_items[0]
        tags = self.tree.item(item, "tags")
        if "default" in tags:
            messagebox.showwarning("警告", "默认规则不可编辑")
            return

        old_val = self.old_text.get().strip()
        new_val = self.new_text.get().strip()

        # 格式验证
        errors, warnings = self.validate_rule_format(old_val, new_val)

        # 如果有错误，显示错误信息并返回
        if errors:
            error_message = "❌ 规则格式错误：\n\n" + "\n".join(f"• {error}" for error in errors)
            if warnings:
                error_message += "\n\n⚠️ 警告：\n" + "\n".join(f"• {warning}" for warning in warnings)
            error_message += "\n\n💡 请修正错误后重试。"
            messagebox.showerror("格式错误", error_message)
            return

        # 如果有警告，询问用户是否继续
        if warnings:
            warning_message = "⚠️ 发现以下警告：\n\n" + "\n".join(f"• {warning}" for warning in warnings)
            warning_message += "\n\n❓ 是否仍要更新此规则？"
            if not messagebox.askyesno("格式警告", warning_message):
                return

        # 格式验证通过，执行更新逻辑
        if not (old_val and new_val):
            messagebox.showwarning("警告", "原始文本和替换文本都不能为空")
            return

        original_old, original_new = self.tree.item(item, "values")
        user_rules = ReplacementRules.get_user_rules()
        for i, (r_old, r_new) in enumerate(user_rules):
            if r_old == original_old and r_new == original_new:
                user_rules[i] = (old_val, new_val)
                ReplacementRules.set_user_rules(user_rules)
                break

        self.filter_rules()
        self.update_rule_count_display()
    
    def on_double_click(self, event):
        item = self.tree.selection()[0] if self.tree.selection() else None
        if item:
            tags = self.tree.item(item, "tags")
            old_val, new_val = self.tree.item(item, "values")
            
            if "default" in tags:
                messagebox.showinfo("提示", "此为默认规则，不可编辑。请添加用户规则进行自定义。")
                return
            
            self.old_text.delete(0, tk.END)
            self.new_text.delete(0, tk.END)
            self.old_text.insert(0, old_val)
            self.new_text.insert(0, new_val)
    
    def remove_rule(self):
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("警告", "请先选择要删除的规则")
            return
        
        item = selected_items[0]
        tags = self.tree.item(item, "tags")
        if "default" in tags:
            messagebox.showwarning("警告", "默认规则不可删除")
            return
        
        if messagebox.askyesno("确认", "确定要删除选中的规则吗？"):
            old_val, new_val = self.tree.item(item, "values")
            user_rules = ReplacementRules.get_user_rules()
            user_rules = [(r_old, r_new) for r_old, r_new in user_rules 
                          if not (r_old == old_val and r_new == new_val)]
            ReplacementRules.set_user_rules(user_rules)
            self.filter_rules()
            self.update_rule_count_display()
    
    def clear_user_rules(self):
        if messagebox.askyesno("确认", "确定要清空所有用户规则吗？"):
            ReplacementRules.set_user_rules([])
            self.filter_rules()
            self.update_rule_count_display()
    
    def load_saved_rules(self):
        try:
            for old, new in ReplacementRules.get_default_rules():
                self.add_rule_to_list(old, new, is_default=True)
            
            if ReplacementRules.load_rules_from_file():
                for old, new in ReplacementRules.get_user_rules():
                    self.add_rule_to_list(old, new, is_default=False)
        except Exception as e:
            messagebox.showerror("错误", f"加载规则失败: {str(e)}")
    
    def save_rules(self):
        try:
            user_rules = ReplacementRules.get_user_rules()

            # 检查是否存在本地规则文件
            rules_file = "replacement_rules.txt"
            file_exists = os.path.exists(rules_file)

            # 构建确认消息
            if file_exists:
                confirm_message = (
                    f"⚠️ 确认保存规则？\n\n"
                    f"📁 将要保存到：{rules_file}\n"
                    f"📊 当前用户规则数量：{len(user_rules)} 条\n\n"
                    f"🔄 注意：此操作将覆盖现有的本地规则文件！\n"
                    f"💡 如果您刚刚清空了用户规则，保存后本地文件也会被清空。\n\n"
                    f"确定要继续保存吗？"
                )
            else:
                confirm_message = (
                    f"💾 确认保存规则？\n\n"
                    f"📁 将要保存到：{rules_file}\n"
                    f"📊 当前用户规则数量：{len(user_rules)} 条\n\n"
                    f"确定要保存吗？"
                )

            # 显示确认对话框
            if not messagebox.askyesno("确认保存", confirm_message):
                return  # 用户选择取消，直接返回

            # 用户确认后执行保存
            if ReplacementRules.save_rules_to_file():
                messagebox.showinfo("成功", f"用户自定义规则已保存在程序目录replacement_rules.txt里，共 {len(user_rules)} 条")
            else:
                messagebox.showerror("错误", "保存规则文件失败")
        except Exception as e:
            messagebox.showerror("错误", f"保存规则失败: {str(e)}")
    
    def import_rules(self):
        try:
            file_path = filedialog.askopenfilename(
                title="选择规则文件",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
            )
            if file_path:
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    lines = [line.strip() for line in f if line.strip()]
            
                valid_rules = []
                invalid_lines = []
                duplicate_rules = []
                file_internal_duplicates = []  # 文件内部重复规则
                file_rules_dict = {}  # 用于检测文件内部重复

                # 获取现有的用户规则
                existing_user_rules = ReplacementRules.get_user_rules()
                existing_rules_dict = {old: new for old, new in existing_user_rules}

                for i, line in enumerate(lines, 1):
                    parts = line.split("\t", 1)
                    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                        old_text = parts[0].strip()
                        new_text = parts[1].strip()

                        # 检查文件内部重复
                        if old_text in file_rules_dict:
                            if file_rules_dict[old_text] == new_text:
                                file_internal_duplicates.append((old_text, new_text, f"第{i}行与前面行重复"))
                            else:
                                file_internal_duplicates.append((old_text, f"{file_rules_dict[old_text]} → {new_text}", f"第{i}行与前面行冲突"))
                            continue  # 跳过文件内部重复的规则

                        file_rules_dict[old_text] = new_text

                        # 检查是否与现有用户规则重复
                        if old_text in existing_rules_dict:
                            if existing_rules_dict[old_text] == new_text:
                                # 完全重复的规则
                                duplicate_rules.append((old_text, new_text, "完全重复"))
                            else:
                                # 原文相同但译文不同的规则
                                duplicate_rules.append((old_text, f"{existing_rules_dict[old_text]} → {new_text}", "冲突"))
                        else:
                            valid_rules.append((old_text, new_text))
                    else:
                        invalid_lines.append(i)
                
                # 处理重复规则的用户选择
                conflicted_rules = []
                should_overwrite = False

                if duplicate_rules:
                    # 分离冲突规则（需要用户决定）和完全重复规则（直接跳过）
                    conflicted_rules = [r for r in duplicate_rules if r[2] == "冲突"]

                    if conflicted_rules:
                        # 询问用户是否要处理冲突规则
                        conflict_message = f"🔄 发现 {len(conflicted_rules)} 条冲突规则（原文相同但译文不同）：\n\n"
                        for i, (old, new_info, _) in enumerate(conflicted_rules[:3], 1):
                            conflict_message += f"{i}. 原文：{old}\n   冲突：{new_info}\n\n"
                        if len(conflicted_rules) > 3:
                            conflict_message += f"... 还有 {len(conflicted_rules) - 3} 条冲突规则\n\n"
                        conflict_message += "⚠️ 是否要覆盖现有规则？\n\n"
                        conflict_message += "✅ 选择'是'：用新规则覆盖现有规则\n"
                        conflict_message += "❌ 选择'否'：跳过这些冲突规则，保持现有规则不变"

                        should_overwrite = messagebox.askyesno("🔄 处理冲突规则", conflict_message)

                        if should_overwrite:
                            # 用户选择覆盖，将冲突规则添加到有效规则中
                            for old_text, new_info, _ in conflicted_rules:
                                # 从new_info中提取新的译文（格式：旧译文 → 新译文）
                                if " → " in new_info:
                                    new_text = new_info.split(" → ")[1]
                                    valid_rules.append((old_text, new_text))

                # 只添加非重复的有效规则
                if valid_rules:
                    user_rules = ReplacementRules.get_user_rules()

                    # 如果有冲突规则需要覆盖，先移除旧规则
                    if should_overwrite and conflicted_rules:
                        for old_text, _, _ in conflicted_rules:
                            user_rules = [(old, new) for old, new in user_rules if old != old_text]

                    user_rules.extend(valid_rules)
                    ReplacementRules.set_user_rules(user_rules)
                    self.filter_rules()

                    # 自动选择第一条新导入的规则
                    if valid_rules:
                        self.old_text.delete(0, tk.END)
                        self.new_text.delete(0, tk.END)
                        self.old_text.insert(0, valid_rules[0][0])
                        self.new_text.insert(0, valid_rules[0][1])

                self.update_rule_count_display()
                
                # 构建详细的导入结果消息
                message_parts = []

                if valid_rules:
                    message_parts.append(f"✅ 成功导入 {len(valid_rules)} 条新规则")

                if duplicate_rules:
                    duplicate_count = len([r for r in duplicate_rules if r[2] == "完全重复"])
                    conflict_count = len([r for r in duplicate_rules if r[2] == "冲突"])

                    if duplicate_count > 0:
                        message_parts.append(f"⚠️ 跳过 {duplicate_count} 条重复规则")
                    if conflict_count > 0:
                        message_parts.append(f"⚠️ 跳过 {conflict_count} 条冲突规则（原文相同但译文不同）")

                    # 显示前3个重复/冲突的规则作为示例
                    if len(duplicate_rules) <= 3:
                        message_parts.append("\n重复/冲突的规则：")
                        for old, new, type_str in duplicate_rules:
                            message_parts.append(f"  • {old} → {new} ({type_str})")
                    else:
                        message_parts.append(f"\n重复/冲突的规则（显示前3条）：")
                        for old, new, type_str in duplicate_rules[:3]:
                            message_parts.append(f"  • {old} → {new} ({type_str})")
                        message_parts.append(f"  ... 还有 {len(duplicate_rules) - 3} 条")

                if file_internal_duplicates:
                    message_parts.append(f"🔄 跳过 {len(file_internal_duplicates)} 条文件内部重复规则")
                    if len(file_internal_duplicates) <= 3:
                        message_parts.append("文件内部重复的规则：")
                        for old, new, location in file_internal_duplicates:
                            message_parts.append(f"  • {old} → {new} ({location})")
                    else:
                        message_parts.append("文件内部重复的规则（显示前3条）：")
                        for old, new, location in file_internal_duplicates[:3]:
                            message_parts.append(f"  • {old} → {new} ({location})")
                        message_parts.append(f"  ... 还有 {len(file_internal_duplicates) - 3} 条")

                if invalid_lines:
                    message_parts.append(f"❌ 跳过 {len(invalid_lines)} 行无效格式：第{', '.join(map(str, invalid_lines))}行")

                if not valid_rules and not duplicate_rules and not invalid_lines and not file_internal_duplicates:
                    message_parts.append("📝 文件为空或无有效规则")

                message = "\n".join(message_parts)
                messagebox.showinfo("导入结果", message)
        except Exception as e:
            messagebox.showerror("错误", f"导入规则失败: {str(e)}")
    
    def export_rules(self):
        try:
            file_path = filedialog.asksaveasfilename(
                title="保存规则文件",
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
            )
            if file_path:
                user_rules = ReplacementRules.get_user_rules()
                with open(file_path, "w", encoding="utf-8") as f:
                    for old, new in user_rules:
                        f.write(f"{old}\t{new}\n")
                
                messagebox.showinfo("成功", f"已导出 {len(user_rules)} 条用户规则")
        except Exception as e:
            messagebox.showerror("错误", f"导出规则失败: {str(e)}")
    
    def update_rule_count_display(self):
        """更新用户规则总数显示"""
        user_rules_count = len(ReplacementRules.get_user_rules())
        self.rule_count_label.config(text=f"用户规则总数: {user_rules_count}")

# ========================== 英语注音功能 ==========================
# 配置
PHONETIC_DB_FILE = "phonetic_accurate_db.sqlite"  # 英语音标数据库文件

def init_english_phonetic_db():
    """初始化英语音标数据库（如果不存在则创建空表）"""
    import sqlite3
    db_path = get_resource_path(PHONETIC_DB_FILE)

    # 如果数据库文件不存在，直接返回False
    if not os.path.exists(db_path):
        return False

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='phonetic'")
        table_exists = cursor.fetchone() is not None

        if not table_exists:
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS phonetic (
                word TEXT PRIMARY KEY,
                ipa TEXT,
                word_lower TEXT
            )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_word_lower ON phonetic(word_lower)')
            conn.commit()

        # 检查数据库是否有数据
        cursor.execute("SELECT COUNT(*) FROM phonetic")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        print(f"英语音标数据库初始化失败: {e}")
        return False

def get_english_phonetic(word):
    """从数据库查询英语单词的音标"""
    import sqlite3
    db_path = get_resource_path(PHONETIC_DB_FILE)

    if not os.path.exists(db_path):
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 1. 精确匹配原单词
        cursor.execute("SELECT ipa FROM phonetic WHERE word = ?", (word,))
        result = cursor.fetchone()
        if result:
            conn.close()
            return result[0]

        # 2. 小写匹配（忽略大小写）
        cursor.execute("SELECT ipa FROM phonetic WHERE word_lower = ?", (word.lower(),))
        result = cursor.fetchone()
        if result:
            conn.close()
            return result[0]

        # 3. 处理带撇号的单词（如don't）
        if "'" in word:
            no_apos_word = word.replace("'", "")
            cursor.execute("SELECT ipa FROM phonetic WHERE word_lower = ?", (no_apos_word.lower(),))
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None

        conn.close()
        return None
    except Exception as e:
        print(f"查询音标失败: {e}")
        return None

def split_english_sentence(sentence):
    """拆分英语句子为单词和标点，保留换行结构"""
    pattern = r"(\w+[']?\w*|\W)"
    tokens = re.findall(pattern, sentence)
    # 保留所有非空token（包括换行符），只过滤纯空字符串
    return [token for token in tokens if token != ""]

def english_text_to_ruby(text):
    """将英文文本转换为带音标标注的Ruby格式（与日语注音格式一致）"""
    tokens = split_english_sentence(text)
    html_parts = []
    found_count = 0
    word_count = 0

    for token in tokens:
        if re.match(r'[a-zA-Z]', token):
            word_count += 1
            phonetic = get_english_phonetic(token)
            if phonetic:
                found_count += 1
                # 使用与日语相同的Ruby HTML格式
                html_parts.append(f'<ruby>{token}<rp>(</rp><rt>{phonetic}</rt><rp>)</rp></ruby>')
            else:
                html_parts.append(token)
        else:
            html_parts.append(token)

    html_text = ''.join(html_parts)
    return html_text, word_count, found_count

# ========================== 中文注音功能 ==========================
def init_chinese_pinyin():
    """初始化中文拼音库（检查pypinyin是否可用）"""
    try:
        from pypinyin import pinyin, Style
        # 测试pypinyin是否正常工作
        test_result = pinyin("测试", style=Style.TONE)
        return True
    except ImportError:
        print("警告: pypinyin库未安装，中文注音功能将不可用")
        return False
    except Exception as e:
        print(f"中文拼音库初始化失败: {e}")
        return False

def chinese_text_to_ruby(text):
    """将中文文本转换为带拼音注音的Ruby格式（与日语、英语格式一致）"""
    try:
        from pypinyin import pinyin, Style
    except ImportError:
        return text, 0, 0

    # 使用正则表达式分割文本，保留非汉字字符
    pattern = re.compile(r'([\u4e00-\u9fff]+)|([^\u4e00-\u9fff]+)')
    segments = pattern.findall(text)

    html_parts = []
    hanzi_count = 0
    pinyin_count = 0

    for hanzi_group, non_hanzi in segments:
        if hanzi_group:
            # 处理汉字组
            pinyin_list = pinyin(hanzi_group, style=Style.TONE, heteronym=False)
            for char, py in zip(hanzi_group, pinyin_list):
                pinyin_str = py[0]
                # 使用与日语、英语相同的Ruby HTML格式
                html_parts.append(f'<ruby>{char}<rp>(</rp><rt>{pinyin_str}</rt><rp>)</rp></ruby>')
                hanzi_count += 1
                pinyin_count += 1
        elif non_hanzi:
            # 直接添加非汉字字符
            html_parts.append(non_hanzi)

    html_text = ''.join(html_parts)
    return html_text, hanzi_count, pinyin_count

# ========================== 字幕处理功能 ==========================
class SubtitleProcessor:
    def __init__(self, logger=None, progress_callback=None):
        self.logger = logger
        self.progress_callback = progress_callback
        self.current_progress = 0
        
        # 初始化Sudachi分词器
        self.tokenizer_obj = dictionary.Dictionary().create()
        self.mode = tokenizer.Tokenizer.SplitMode.C
        
        # 初始化pykakasi
        try:
            import pykakasi
            self.kakasi = pykakasi.kakasi()
            self.kakasi.setMode("J", "H")  # 日语转平假名
            self.conv = self.kakasi.getConverter()
        except ImportError:
            self.log("警告: pykakasi未安装，可能影响假名标注功能")
            self.conv = None
            
    def log(self, message):
        if self.logger:
            self.logger(message)
        else:
            print(message)
            
    def update_progress(self, value, message=None):
        self.current_progress = value
        if self.progress_callback:
            self.progress_callback(value, message)
            
    def validate_srt_structure(self, file_path):
        """校验SRT文件结构，返回块列表和类型（'single'单语或'double'双语）"""
        try:
            # 尝试以utf-8-sig编码打开（兼容带BOM和不带BOM的UTF-8文件）
            try:
                with open(file_path, 'r', encoding='utf-8-sig') as f:
                    lines = [(i+1, line.rstrip('\n')) for i, line in enumerate(f.readlines())]
            except UnicodeDecodeError:
                # 明确提示文件不是UTF-8系列编码（含带BOM和不带BOM）
                raise Exception(f"📄 文件编码错误：请确保输入文件为UTF-8编码（可含BOM），当前文件使用了非UTF-8编码\n"
                              f"💡 解决方案：\n"
                              f"   1. 使用记事本打开文件，点击'另存为'，编码选择'UTF-8'\n"
                              f"   2. 使用VS Code等编辑器，右下角点击编码，选择'通过编码重新打开'，选择正确编码后保存为UTF-8\n"
                              f"   3. 确保文件不是GBK、ANSI等其他编码格式")
            
            i = 0
            blocks = []
            block_count = 0
            content_lengths = []  # 记录每个块的序号和内容行数，用于错误定位
            expected_index = 1  # 期望的序号，用于检查序号连续性
            
            while i < len(lines):
                # 跳过块间空行
                while i < len(lines) and not lines[i][1].strip():
                    i += 1
                if i >= len(lines):
                    break
                
                # 校验序号行
                index_line_num, index_line_content = lines[i]
                if not index_line_content.strip().isdigit():
                    # 详细的序号检测错误提示
                    actual_content = index_line_content.strip()[:50]  # 限制显示长度
                    #raise Exception(f"🔍 序号检测错误：第{index_line_num}行为空行，应为字幕块序号（纯数字）\n"
                    if not actual_content:
                        raise Exception(f"🔍 序号检测错误：第{index_line_num}行为空行，应为字幕块序号（纯数字）\n"
                                      f"💡 解决方案：检查SRT文件格式，确保每个字幕块都以序号开始")
                    else:
                        raise Exception(f"🔍 序号检测错误：第{index_line_num}行内容为 '{actual_content}'，应为字幕块序号（纯数字）\n"
                                      f"💡 解决方案：\n"
                                          f"   1. 检查该行内容字幕块是否缺失序号\n"
                                          f"   2. 确保序号为纯数字，且不包含肉眼可见的“�”或其他字符\n"
                                          f"   3. 修正序号为纯数字，如：1、2、3...")
                                                
                index = index_line_content.strip()
               
                # 检查序号连续性
                try:
                    current_index = int(index)
                    if current_index != expected_index:
                        # 提供详细的序号连续性错误提示
                        if current_index < expected_index:
                            raise Exception(f"🔍 序号连续性错误：第{index_line_num}行序号{current_index}重复或倒退\n"
                                          f"💡 可能序号：{expected_index}，实际序号：{current_index}\n"
                                          f"💡 解决方案：\n"
                                          f"   1. 检查是否有重复的序号\n"
                                          f"   2. 确保序号按1、2、3...顺序递增\n"
                                          f"   3. 删除重复的字幕块或修正序号")
                        else:
                            raise Exception(f"🔍 序号连续性错误：第{index_line_num}行序号{current_index}跳跃过大\n"
                                          f"💡 可能序号：{expected_index}，实际序号：{current_index}\n"
                                          f"💡 解决方案：\n"
                                          f"   1. 检查是否缺少序号{expected_index}到{current_index-1}的字幕块\n"
                                          f"   2. 修正当前序号为{expected_index}\n"
                                          f"   3. 确保序号连续无跳跃")
                    expected_index += 1
                except ValueError:
                    # 这个错误应该在前面的isdigit()检查中被捕获，但为了安全起见保留
                    raise Exception(f"🔍 序号格式错误：第{index_line_num}行序号 '{index}' 不是有效数字")

                # 新增映射逻辑：index基础上加1，不影响原index值
                mapped_index = int(index) + 1  # 转换为整数后加1
                #print(f"序号映射关系：原序号 {index} -> 映射后序号 {mapped_index}")  # 在终端显示映射结果
                i += 1
                
                # 校验时间轴行
                if i >= len(lines):
                    raise Exception(f"🔍 时间轴检测错误：序号{index}字幕块后缺少时间轴行\n"
                                  f"💡 解决方案：在序号{index}后添加时间轴行，格式如：00:00:01,000 --> 00:00:03,000")

                time_line_num, time_line_content = lines[i]
                if '-->' not in time_line_content:
                    # 详细的时间轴检测错误提示
                    actual_content = time_line_content.strip()[:50]  # 限制显示长度
                    raise Exception(f"🔍 时间轴格式错误：第{time_line_num}行内容为 '{actual_content}'，缺少时间轴分隔符 '-->' \n"
                                  f"💡 正确格式：HH:MM:SS,mmm --> HH:MM:SS,mmm\n"
                                  f"💡 示例：00:00:01,000 --> 00:00:03,000\n"
                                  f"💡 当前字幕块：序号{index}")

                # 进一步验证时间轴格式
                time_parts = time_line_content.split('-->')
                if len(time_parts) != 2:
                    raise Exception(f"🔍 时间轴格式错误：第{time_line_num}行时间轴分隔符 '-->' 数量不正确\n"
                                  f"💡 正确格式：开始时间 --> 结束时间\n"
                                  f"💡 当前内容：{time_line_content.strip()}\n"
                                  f"💡 当前字幕块：序号{index}")

                start_time, end_time = time_parts[0].strip(), time_parts[1].strip()

                # 验证时间格式 (HH:MM:SS,mmm)
                time_pattern = r'^\d{2}:\d{2}:\d{2},\d{3}$'
                if not re.match(time_pattern, start_time):
                    raise Exception(f"🔍 开始时间格式错误：第{time_line_num}行开始时间 '{start_time}' 格式不正确\n"
                                  f"💡 正确格式：HH:MM:SS,mmm（如：00:00:01,000）\n"
                                  f"💡 当前字幕块：序号{index}")

                if not re.match(time_pattern, end_time):
                    raise Exception(f"🔍 结束时间格式错误：第{time_line_num}行结束时间 '{end_time}' 格式不正确\n"
                                  f"💡 正确格式：HH:MM:SS,mmm（如：00:00:03,000）\n"
                                  f"💡 当前字幕块：序号{index}")

                # 验证时间逻辑（开始时间应小于结束时间）
                try:
                    start_parts = start_time.split(':')
                    start_seconds = int(start_parts[0]) * 3600 + int(start_parts[1]) * 60 + float(start_parts[2].replace(',', '.'))

                    end_parts = end_time.split(':')
                    end_seconds = int(end_parts[0]) * 3600 + int(end_parts[1]) * 60 + float(end_parts[2].replace(',', '.'))

                    if start_seconds >= end_seconds:
                        raise Exception(f"🔍 时间逻辑错误：第{time_line_num}行开始时间 '{start_time}' 应小于结束时间 '{end_time}'\n"
                                      f"💡 解决方案：\n"
                                      f"   1. 检查时间轴是否写反了\n"
                                      f"   2. 确保开始时间早于结束时间\n"
                                      f"   3. 当前字幕块：序号{index}")

                    # 检查时间是否合理（不能超过24小时）
                    if start_seconds >= 86400 or end_seconds >= 86400:
                        raise Exception(f"🔍 时间范围错误：第{time_line_num}行时间超过24小时限制\n"
                                      f"💡 开始时间：{start_time}，结束时间：{end_time}\n"
                                      f"💡 解决方案：检查时间格式是否正确\n"
                                      f"💡 当前字幕块：序号{index}")

                    # 检查字幕持续时间是否过短（小于0.1秒可能有问题）
                    duration = end_seconds - start_seconds
                    if duration < 0.1:
                        raise Exception(f"🔍 时间持续过短：第{time_line_num}行字幕持续时间仅{duration:.3f}秒\n"
                                      f"💡 开始时间：{start_time}，结束时间：{end_time}\n"
                                      f"💡 解决方案：检查时间是否设置正确，字幕持续时间建议至少0.5秒\n"
                                      f"💡 当前字幕块：序号{index}")

                except ValueError as ve:
                    raise Exception(f"🔍 时间解析错误：第{time_line_num}行时间格式包含无效数值\n"
                                  f"💡 开始时间：{start_time}，结束时间：{end_time}\n"
                                  f"💡 错误详情：{str(ve)}\n"
                                  f"💡 当前字幕块：序号{index}")

                time = time_line_content.strip()
                i += 1
                
                # 收集内容行
                contents = []
                while i < len(lines) and lines[i][1].strip():
                    contents.append(lines[i][1])
                    i += 1
                
                if not contents:
                    raise Exception(f"🔍 内容检测错误：序号{index}字幕块的时间轴后缺少字幕内容行\n"
                                  f"💡 解决方案：在时间轴行后添加字幕内容（1-2行文本）")
                if len(contents) > 2:
                    raise Exception(f"🔍 内容行数错误：序号{index}字幕块包含{len(contents)}行内容，超出支持范围（最多2行）\n"
                                  f"💡 解决方案：检查字幕块间是否缺少空行分隔，或将多行内容合并为1-2行\n"
                                  f"💡 当前内容：{contents[:3]}{'...' if len(contents) > 3 else ''}")
                
                # 记录当前块的内容行数
                content_lengths.append((index, len(contents)))
                
                # 记录块间空行状态
                has_empty_line = False
                if i < len(lines) and not lines[i][1].strip():
                    has_empty_line = True
                    i += 1
                
                # 保存块信息
                blocks.append({
                    'index': index,
                    'time': time,
                    'contents': contents,
                    'has_empty_line': has_empty_line
                })
                block_count += 1
            
            if not blocks:
                raise Exception("🔍 文件结构错误：文件中未检测到有效的字幕块\n"
                              "💡 解决方案：\n"
                              "   1. 检查文件是否为有效的SRT格式\n"
                              "   2. 确保文件包含序号、时间轴和内容三个基本元素\n"
                              "   3. 检查文件是否为空或只包含空行\n"
                              "   4. 标准SRT格式示例：\n"
                              "      1\n"
                              "      00:00:01,000 --> 00:00:03,000\n"
                              "      字幕内容\n"
                              "      \n"
                              "      2\n"
                              "      00:00:03,000 --> 00:00:05,000\n"
                              "      下一行字幕内容")

            # 检查所有块的内容行数是否一致
            content_counts = {length for _, length in content_lengths}
            if len(content_counts) > 1:
                # 找出所有不一致的块
                base_length = content_lengths[0][1]
                inconsistent_blocks = []
                for idx, length in content_lengths:
                    if length != base_length:
                        inconsistent_blocks.append(f"序号{idx}({length}行)")

                if inconsistent_blocks:
                    raise Exception(f"🔍 字幕块结构不一致：检测到混合的单语/双语字幕块\n"
                                  f"💡 基准格式：{base_length}行内容（{'单语' if base_length == 1 else '双语'}）\n"
                                  f"💡 不一致的块：{', '.join(inconsistent_blocks[:5])}{'...' if len(inconsistent_blocks) > 5 else ''}\n"
                                  f"💡 解决方案：统一所有字幕块的格式，要么全部单语（1行），要么全部双语（2行）")
            
            # 确定类型代码和类型文本
            lang_type_code = 'single' if content_lengths[0][1] == 1 else 'double'
            lang_type_text = '单语' if lang_type_code == 'single' else '双语'

            # 只有在有日志回调时才输出日志（避免合并窗口操作时在主界面显示日志）
            if hasattr(self, 'log_callback') and self.log_callback:
                self.log(f"加载的SRT文件结构校验通过，共检测到{block_count}个有效字幕块，类型：{lang_type_text}")
            elif self.logger:
                self.log(f"加载的SRT文件结构校验通过，共检测到{block_count}个有效字幕块，类型：{lang_type_text}")

            return blocks, lang_type_code
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def process_file_zh(self, blocks, lang_type, output_file):
        """提取第一语言（单语时为唯一语言，双语时为第一语言）"""
        try:
            with codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                block_count = 0
                for block in blocks:
                    f_out.write(f"{block['index']}\n")
                    f_out.write(f"{block['time']}\n")
                    f_out.write(f"{block['contents'][0]}\n")
                    if block['has_empty_line']:
                        f_out.write("\n")
                    block_count += 1
                self.log(f"第一语言处理完成！共处理了{block_count}个字幕块")
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def process_file_jp(self, blocks, lang_type, output_file):
        """提取第二语言（仅双语时有效）"""
        try:
            if lang_type == 'single':
                # 单语时创建纯时间轴文件
                self.process_file_timeline(blocks, output_file)
                return
            
            with codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                block_count = 0
                for block in blocks:
                    f_out.write(f"{block['index']}\n")
                    f_out.write(f"{block['time']}\n")
                    f_out.write(f"{block['contents'][1]}\n")
                    if block['has_empty_line']:
                        f_out.write("\n")
                    block_count += 1
                self.log(f"第二语言处理完成！共处理了{block_count}个字幕块")
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def process_file_to_kashi(self, blocks, lang_type, output_file):
        """提取歌词文本（单语提取第一语言，双语提取第二语言）"""
        try:
            with codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                text_line_count = 0
                for block in blocks:
                    line = block['contents'][0] if lang_type == 'single' else block['contents'][1]
                    f_out.write(f"{line}\n")
                    text_line_count += 1
                self.log(f"提取完成！共提取了{text_line_count}行文本")
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def process_file_timeline(self, blocks, output_file):
        """提取纯时间轴字幕文本（保持SRT格式，第三行为字幕块序号作为分隔）"""
        try:
            with codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                timeline_count = 0
                for block in blocks:
                    # 保持SRT格式：序号 + 时间轴 + 字幕块序号（替代内容）
                    f_out.write(f"{block['index']}\n")
                    f_out.write(f"{block['time']}\n")
                    f_out.write(f"{block['index']}\n")  # 第三行为字幕块序号作为分隔
                    if block['has_empty_line']:
                        f_out.write("\n")
                    timeline_count += 1
                self.log(f"纯时间轴字幕提取完成！共提取了{timeline_count}个时间轴块")
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def process_file_to_nohonngokashi(self, input_file, output_file):
        """删除前三行保留一行，提取纯字幕文本"""
        try:
            with codecs.open(input_file, 'r', encoding='utf-8-sig', errors='ignore') as f_in, \
                 codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                line_count = 0
                for line in f_in:
                    if line_count % 4 == 3:  # 删除前三行保留一行
                        f_out.write(line)
                    line_count += 1
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def process_file_to_timeline_with_index(self, blocks, output_file):
        """导出第一语言行和字幕序号块同一内容的纯数字字幕文本"""
        try:
            with codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                timeline_count = 0
                for block in blocks:
                    # 格式：序号 + 时间轴 + 序号（作为内容）
                    f_out.write(f"{block['index']}\n")
                    f_out.write(f"{block['time']}\n")
                    f_out.write(f"{block['index']}\n")  # 第三行为字幕块序号
                    if block['has_empty_line']:
                        f_out.write("\n")
                    timeline_count += 1
                self.log(f"纯数字字幕文本导出完成！共导出了{timeline_count}个字幕块")
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def save_ruby_before_k1(self, ruby_input_path, output_file):
        """保存经过用户规则和默认规则替换后的ruby文本（替换{\k1}之前），移除空行用于HTML生成"""
        try:
            with codecs.open(ruby_input_path, 'r', encoding='utf-8-sig', errors='ignore') as f_in:
                content = f_in.read()
            with codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                f_out.write(content)
            self.log(f"Ruby替换后文本保存完成：{output_file}")
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def convert_k1_to_ruby_html(self, text):
        """将{\k1}格式转换为HTML ruby格式

        支持三种注音格式：
        - 日语：{\k1}漢|<かん{\k1}
        - 英语：{\k1}word|<phonetic{\k1}
        - 中文：汉|<pīn{\k1}字|<yīn{\k1}（连续汉字，中间无{\k1}）
        """
        import re

        # 改进的正则表达式，兼容中文连续汉字格式
        # (?:\{\\k1\})? 使开头的{\k1}可选（兼容中文）
        # ([^|{\s]+) 匹配文字部分（排除|、{、空格）
        # \|< 匹配分隔符
        # ([^{]+) 匹配注音部分
        # \{\\k1\} 匹配结尾标记
        pattern = r'(?:\{\\k1\})?([^|{\s]+)\|<([^{]+)\{\\k1\}'

        def replace_match(match):
            text_part = match.group(1)  # 文字部分（汉字/单词）
            phonetic = match.group(2)   # 注音部分（假名/音标/拼音）
            # 转换为HTML ruby格式
            return f'<ruby>{text_part}<rp>(</rp><rt>{phonetic}</rt><rp>)</rp></ruby>'

        # 执行替换
        converted_text = re.sub(pattern, replace_match, text)

        # 清理残留的{\k1}标记（中文连续汉字开头可能有）
        converted_text = converted_text.replace('{\\k1}', '')

        return converted_text

    def validate_subtitle_files_for_merge(self, file1_path, file2_path):
        """校验两个字幕文件是否可以合并"""
        errors = []
        warnings = []

        try:
            # 检查文件是否存在
            if not os.path.exists(file1_path):
                errors.append(f"❌ 第一语言字幕文件不存在：{file1_path}")
            if not os.path.exists(file2_path):
                errors.append(f"❌ 第二语言字幕文件不存在：{file2_path}")

            if errors:
                return False, errors, warnings

            # 检测文件编码
            def detect_encoding(file_path):
                import chardet
                with open(file_path, 'rb') as f:
                    raw_data = f.read()
                    result = chardet.detect(raw_data)
                    return result['encoding']

            try:
                encoding1 = detect_encoding(file1_path)
                encoding2 = detect_encoding(file2_path)

                if encoding1 != encoding2:
                    warnings.append(f"⚠️ 文件编码不同：第一语言({encoding1}) vs 第二语言({encoding2})")
            except:
                warnings.append("⚠️ 无法检测文件编码，建议确保两个文件使用相同编码")

            # 读取文件内容
            try:
                with codecs.open(file1_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                    lines1 = f.readlines()
            except:
                try:
                    with codecs.open(file1_path, 'r', encoding='gbk', errors='ignore') as f:
                        lines1 = f.readlines()
                except:
                    errors.append(f"❌ 无法读取第一语言字幕文件：{file1_path}")
                    return False, errors, warnings

            try:
                with codecs.open(file2_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                    lines2 = f.readlines()
            except:
                try:
                    with codecs.open(file2_path, 'r', encoding='gbk', errors='ignore') as f:
                        lines2 = f.readlines()
                except:
                    errors.append(f"❌ 无法读取第二语言字幕文件：{file2_path}")
                    return False, errors, warnings

            # 检查行数是否相同
            if len(lines1) != len(lines2):
                errors.append(f"❌ 文件行数不同：第一语言({len(lines1)}行) vs 第二语言({len(lines2)}行)")
                return False, errors, warnings

            # 解析字幕块并校验
            blocks1 = self.parse_subtitle_blocks(lines1)
            blocks2 = self.parse_subtitle_blocks(lines2)

            if len(blocks1) != len(blocks2):
                errors.append(f"❌ 字幕块数量不同：第一语言({len(blocks1)}块) vs 第二语言({len(blocks2)}块)")
                return False, errors, warnings

            # 逐块校验序号和时间轴
            for i, (block1, block2) in enumerate(zip(blocks1, blocks2)):
                block_num = i + 1

                # 校验序号
                if block1['index'] != block2['index']:
                    errors.append(f"❌ 第{block_num}块序号不匹配：第一语言({block1['index']}) vs 第二语言({block2['index']})")

                # 校验时间轴
                if block1['time'] != block2['time']:
                    errors.append(f"❌ 第{block_num}块时间轴不匹配：\n  第一语言: {block1['time']}\n  第二语言: {block2['time']}")

            # 如果有错误，返回失败
            if errors:
                return False, errors, warnings

            # 成功校验
            return True, [], warnings

        except Exception as e:
            errors.append(f"❌ 校验过程中发生错误：{str(e)}")
            return False, errors, warnings

    def parse_subtitle_blocks(self, lines):
        """解析字幕文件为字幕块"""
        blocks = []
        i = 0

        while i < len(lines):
            # 跳过空行
            while i < len(lines) and not lines[i].strip():
                i += 1

            if i >= len(lines):
                break

            # 读取序号
            index_line = lines[i].strip()
            if not index_line.isdigit():
                i += 1
                continue

            i += 1
            if i >= len(lines):
                break

            # 读取时间轴
            time_line = lines[i].strip()
            if '-->' not in time_line:
                i += 1
                continue

            i += 1

            # 读取内容行
            content_lines = []
            while i < len(lines) and lines[i].strip():
                content_lines.append(lines[i].strip())
                i += 1

            blocks.append({
                'index': index_line,
                'time': time_line,
                'content': content_lines
            })

        return blocks

    def merge_subtitle_files(self, file1_path, file2_path, output_path):
        """合并两个单语字幕文件为双语字幕文件"""
        try:
            # 先校验文件
            is_valid, errors, warnings = self.validate_subtitle_files_for_merge(file1_path, file2_path)

            if not is_valid:
                error_msg = "字幕文件校验失败：\n" + "\n".join(errors)
                if warnings:
                    error_msg += "\n\n警告：\n" + "\n".join(warnings)
                raise Exception(error_msg)

            # 读取文件
            with codecs.open(file1_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                lines1 = f.readlines()
            with codecs.open(file2_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                lines2 = f.readlines()

            # 解析字幕块
            blocks1 = self.parse_subtitle_blocks(lines1)
            blocks2 = self.parse_subtitle_blocks(lines2)

            # 合并字幕
            merged_content = []
            for block1, block2 in zip(blocks1, blocks2):
                # 写入序号
                merged_content.append(block1['index'] + '\n')

                # 写入时间轴
                merged_content.append(block1['time'] + '\n')

                # 写入第一语言内容
                for content_line in block1['content']:
                    merged_content.append(content_line + '\n')

                # 写入第二语言内容
                for content_line in block2['content']:
                    merged_content.append(content_line + '\n')

                # 添加空行分隔
                merged_content.append('\n')

            # 保存合并后的文件
            with codecs.open(output_path, 'w', encoding='utf-8', errors='ignore') as f:
                f.writelines(merged_content)

            success_msg = f"✅ 字幕合并成功！\n输出文件：{output_path}"
            if warnings:
                success_msg += "\n\n⚠️ 警告信息：\n" + "\n".join(warnings)

            return True, success_msg

        except Exception as e:
            return False, str(e)

    def save_ruby_html(self, ruby_input_path, output_file, lang_type='single', zh_kashi_path=None):
        """将ruby文本转换为HTML格式保存"""
        try:
            with codecs.open(ruby_input_path, 'r', encoding='utf-8-sig', errors='ignore') as f_in:
                content = f_in.read()

            # 第一步：将{\k1}格式转换为HTML ruby格式
            converted_content = self.convert_k1_to_ruby_html(content)

            # 第二步：处理换行，根据单语/双语添加<br>标签
            lines = converted_content.split('\n')
            lines = [line.strip() for line in lines if line.strip()]  # 过滤空行

            # 双语时读取中文翻译
            zh_lines = []
            if lang_type == 'double' and zh_kashi_path:
                with open(zh_kashi_path, 'r', encoding='utf-8-sig') as f:
                    zh_lines = [line.strip() for line in f.readlines() if line.strip()]

            formatted_lines = []
            for i, line in enumerate(lines):
                if lang_type == 'single':
                    formatted_lines.append(f'<span class="original-text">{line}</span><br><br>')
                else:
                    # 双语：原文 + 翻译
                    formatted_lines.append(f'<span class="original-text">{line}</span><br>')
                    if i < len(zh_lines):
                        formatted_lines.append(f'<span class="translation-text">{zh_lines[i]}</span><br><br>')

            # 将所有行合并并包装在<p>标签中
            if formatted_lines:
                formatted_content = f"    <p>\n {chr(10).join(formatted_lines)}\n    </p>"
            else:
                formatted_content = "    <p>\n    </p>"

            # 创建HTML文档结构，按照用户提供的模板
            html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        /* 基础样式 */
        p {{
      font-size: 16px; // 原文字体大小，默认16px
  }}

        ruby {{
            margin: 0 2px; /* 默认2px；调整单词与单词间距*/
        }}

        rt {{
            font-size: 0.7em; /* 基础大小，可被JS覆盖，默认0.7 */
            transition: all 0.3s ease;
        }}
        /* 让<title>标题居中显示 */
        h2 {{
            text-align: center; /* 核心属性：文本水平居中 */
            margin: 20px 0; /* 可选：添加上下边距，增强视觉效果 */
        }}
    </style>
</head>
<body>
    <h2>注音文本</h2>

{formatted_content}

    <script>
        // 注音样式（rt标签）
        const phoneticStyle = {{
            color: "#999999",    // 注音颜色，默认浅灰
            fontStyle: ""        // 注音样式，默认正常；斜体：italic
        }};

        // 原文样式
        const originalStyle = {{
            color: "",           // 原文颜色，默认继承（黑色）
            
        }};

        // 译文样式
        const translationStyle = {{
            color: "#595959",    // 译文颜色，默认深灰
            fontSize: "13px"     // 译文字体大小
        }};

        // 应用注音样式
        const rtElements = document.querySelectorAll('rt');
        rtElements.forEach(rt => {{
            Object.keys(phoneticStyle).forEach(styleProp => {{
                if (phoneticStyle[styleProp]) {{
                    rt.style[styleProp] = phoneticStyle[styleProp];
                }}
            }});
        }});

        // 应用原文样式
        const originalElements = document.querySelectorAll('.original-text');
        originalElements.forEach(el => {{
            Object.keys(originalStyle).forEach(styleProp => {{
                if (originalStyle[styleProp]) {{
                    el.style[styleProp] = originalStyle[styleProp];
                }}
            }});
        }});

        // 应用译文样式
        const translationElements = document.querySelectorAll('.translation-text');
        translationElements.forEach(el => {{
            Object.keys(translationStyle).forEach(styleProp => {{
                if (translationStyle[styleProp]) {{
                    el.style[styleProp] = translationStyle[styleProp];
                }}
            }});
        }});
    </script>
</body>
</html>"""

            with codecs.open(output_file, 'w', encoding='utf-8', errors='ignore') as f_out:
                f_out.write(html_content)
            self.log(f"HTML格式注音文件保存完成：{output_file}")
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def add_blank_line_to_file(self, file_path):
        """srt文件头若无空行，添加空行，使其呈现规律性"""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as file:
                lines = file.readlines()

            if lines and lines[0].strip():
                lines.insert(0, '\n')

            with open(file_path, 'w', encoding='utf-8') as file:
                file.writelines(lines)
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def add_blank_lines_to_match_lines(self, file1_path, file2_path):
        """匹配原文译文srt文件行数，保证后续译文配对时轴正确"""
        try:
            with open(file1_path, 'r', encoding='utf-8-sig') as file1:
                lines_file1 = file1.readlines()
            num_lines_file1 = len(lines_file1)

            with open(file2_path, 'r', encoding='utf-8-sig') as file2:
                lines_file2 = file2.readlines()
            num_lines_file2 = len(lines_file2)

            if num_lines_file1 != num_lines_file2:
                if num_lines_file1 < num_lines_file2:
                    num_blank_lines = num_lines_file2 - num_lines_file1
                    with open(file1_path, 'r+', encoding='utf-8-sig') as file1:
                        content = file1.read()
                        file1.seek(0, 0)
                        file1.write('\n' * num_blank_lines + content)
                else:
                    num_blank_lines = num_lines_file1 - num_lines_file2
                    with open(file2_path, 'r+', encoding='utf-8-sig') as file2:
                        content = file2.read()
                        file2.seek(0, 0)
                        file2.write('\n' * num_blank_lines + content)
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def replace_multiple_text(self, input_file, output_file, replacements):
        """将sunshj.top_ruby.txt整理为Aegisub可用格式"""
        try:
            with open(input_file, 'r', encoding='utf-8-sig') as file:
                content = file.read()

            for old, new in replacements:
                content = content.replace(old, new)

            with open(output_file, 'w', encoding='utf-8') as file:
                file.write(content)
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def add_k1_to_lines(self, file_path):
        """将sunshj.top_ruby_modi.txt整理为Aegisub可用格式"""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as file:
                lines = file.readlines()

            with open(file_path, 'w', encoding='utf-8') as file:
                for line in lines:
                    if not line.startswith('{\\k1}'):
                        line = '{\\k1}' + line
                    file.write(line)
        except IOError as e:
            # 不在这里记录日志，让上层统一处理
            raise
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def replace_lines(self, a_file, b_file, c_file):
        """对齐译文与原文时轴"""
        try:
            with open(a_file, 'r', encoding='utf-8-sig') as file_a:
                lines_a = file_a.readlines()

            with open(b_file, 'r', encoding='utf-8-sig') as file_b:
                lines_b = file_b.readlines()

            count = 0
            for i, line_b in enumerate(lines_b):
                if (i + 1) % 4 == 0:  # 检查是否能被4整除
                    if count < len(lines_a):
                        lines_b[i] = lines_a[count]
                        count += 1

            with open(c_file, 'w', encoding='utf-8') as file_c:
                file_c.writelines(lines_b)
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def srt_to_ass(self, srt_file, ass_file):
        """srt_to_纯ass"""
        try:
            subs = pysrt.open(srt_file)
            
            with open(ass_file, 'w', encoding='utf-8') as f:
                for sub in subs:
                    start = sub.start.to_time().strftime('%H:%M:%S.%f')[:-3]
                    end = sub.end.to_time().strftime('%H:%M:%S.%f')[:-3]
                    text = sub.text.replace('\n', '\\N')
                    f.write("Dialogue: 0,{},{},Default,,0,0,0,,{}\n".format(start, end, text))
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def merge_ass_files(self, input_files, output_file):
        """整合ass至example（修改版：第一个文件路径实际使用内置内容）"""
        try:
            # 内置的example.ass内容
            EXAMPLE_ASS_CONTENT = r"""[Script Info]
; Script generated by 日文汉字注音工具
Title: 自动生成日文汉字注音字幕文件
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601
PlayResX: 1920
PlayResY: 1080

[Aegisub Project Garbage]

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke_up_right-furigana,Arial,45,&H00FFA500,&H192722D5,&H00FFFFFF,&H007F7F7F,0,0,0,0,100,100,0,0,1,1,1,7,500,10,10,1
Style: Default_up_right-furigana,宋体,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,7,500,10,60,1
Style: Default_ZH_up_right-furigana,宋体,15,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,7,500,10,110,1
Style: Karaoke_up-furigana,Arial,45,&H00FFA500,&H192722D5,&H00FFFFFF,&H007F7F7F,0,0,0,0,100,100,0,0,1,1,1,2,10,10,10,1
Style: Default_up-furigana,宋体,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,1,100,10,100,1
Style: Default_ZH_up-furigana,宋体,15,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,1,100,10,60,1
Style: Default_ZH-furigana,楷体,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,1,150,10,30,1
Style: Default,思源黑体 CN Medium,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0.5,1,2,100,10,100,1
Style: Default-furigana,思源黑体 CN Medium,30,&H00FFFFCC,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0.5,1,2,10,10,50,1
Style: Karaoke,Arial,90,&H00FFA500,&H192722D5,&H00FFFFFF,&H007F7F7F,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
Style: Karaoke-furigana,Arial,30,&H00FFA500,&H190000FF,&H00FFFFFF,&H007F7F7F,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
Style: Default_ZH,思源黑体 CN Medium,40,&H00CCFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0.5,1,2,100,10,60,1
Style: Default_ZH_up,宋体,30,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,2,7,100,10,110,1
Style: Default_up,宋体,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,2,7,100,10,60,1
Style: Karaoke_up,Arial,90,&H00FFA500,&H192722D5,&H00FFFFFF,&H007F7F7F,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
Style: Default_ZH_up_right,宋体,30,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,2,9,100,100,110,1
Style: Default_up_right,宋体,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,2,9,10,100,60,1
Style: Karaoke_up_right,Arial,90,&H00FFA500,&H192722D5,&H00FFFFFF,&H007F7F7F,0,0,0,0,100,100,0,0,1,2,2,7,500,10,10,1
Style: Note,黑体,30,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,template noblank syl,{\an5\pos($center,$middle)}
Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,template noblank furi,{\an5\pos($center,$middle)}
Comment: 0,0:00:00.00,0:00:00.00,Karaoke,,0,0,0,template noblank syl,{\an5\pos($center,$middle)\kf!syl.start_time/10!\kf$kdur}
Comment: 0,0:00:00.00,0:00:00.00,Karaoke,,0,0,0,template noblank furi,{\an5\pos($center,$middle)\kf!syl.start_time/10!\kf$kdur}
Comment: 0,0:00:00.00,0:00:00.00,Default_up,,0,0,0,template noblank syl,{\an5\pos($center,$middle)}
Comment: 0,0:00:00.00,0:00:00.00,Default_up,,0,0,0,template noblank furi,{\an5\pos($center,$middle)}
Comment: 0,0:00:00.00,0:00:00.00,Karaoke_up,,0,0,0,template noblank syl,{\an5\pos($center,$middle)\kf!syl.start_time/10!\kf$kdur}
Comment: 0,0:00:00.00,0:00:00.00,Karaoke_up,,0,0,0,template noblank furi,{\an5\pos($center,$middle)\kf!syl.start_time/10!\kf$kdur}
Comment: 0,0:00:00.00,0:00:00.00,Default_up_right,,0,0,0,template noblank syl,{\an5\pos($center,$middle)}
Comment: 0,0:00:00.00,0:00:00.00,Default_up_right,,0,0,0,template noblank furi,{\an5\pos($center,$middle)}
Comment: 0,0:00:00.00,0:00:00.00,Karaoke_up_right,,0,0,0,template noblank syl,{\an5\pos($center,$middle)\kf!syl.start_time/10!\kf$kdur}
Comment: 0,0:00:00.00,0:00:00.00,Karaoke_up_right,,0,0,0,template noblank furi,{\an5\pos($center,$middle)\kf!syl.start_time/10!\kf$kdur}
"""
            with open(output_file, 'w', encoding='utf-8') as out_f:
                # 写入内置的example.ass内容（对应原input_files[0]）
                out_f.write(EXAMPLE_ASS_CONTENT)
                # 写入其他ass文件内容
                for input_file in input_files[1:]:
                    with open(input_file, 'r', encoding='utf-8-sig') as in_f:
                        out_f.write(in_f.read())
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def remove_trailing_empty_lines(self, file_path):
        """移除txt末尾空行"""
        try:
            with open(file_path, 'r+', encoding='utf-8-sig') as file:
                lines = file.readlines()
                while lines and lines[-1].strip() == '':
                    lines.pop()
                file.seek(0)
                file.writelines(lines)
                file.truncate()
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def remove_leading_empty_lines(self, file_path):
        """移除文件开头空行"""
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as file:
                lines = file.readlines()

            # 移除开头的空行
            while lines and lines[0].strip() == '':
                lines.pop(0)

            # 重新写入文件
            with open(file_path, 'w', encoding='utf-8-sig') as file:
                file.writelines(lines)
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            raise

    def handle_operation(self, operation, filename, *args):
        """处理文件操作并返回操作结果"""
        try:
            if args:
                operation(filename, *args)
            else:
                operation(filename)
            return {
                'operation': operation.__name__,
                'file': filename,
                'success': True,
                'error': None
            }
        except Exception as e:
            error_msg = str(e)
            self.log(f"操作 {operation.__name__} 失败: {error_msg}")
            return {
                'operation': operation.__name__,
                'file': filename,
                'success': False,
                'error': error_msg
            }

    def get_hiragana(self, text):
        """使用pykakasi获取文本的平假名读音"""
        try:
            if self.conv:
                return self.conv.do(text)
            return text
        except Exception as e:
            error_msg = f"注音错误: {e}"
            self.log(error_msg)
            return None

    def is_kanji(self, char):
        """判断字符是否为汉字"""
        return bool(re.match(r'[\u4e00-\u9fff]', char))

    def process_token(self, surface, kana):
        """
        处理单个分词单元，将连续汉字合并到同一个Ruby标签中
        surface: 原分词
        kana: 对应的注音
        """
        try:
            result = []
            kanji_count = 0
            s_index = 0  # 原文字符索引
            k_index = 0  # 注音字符索引
            len_surface = len(surface)
            len_kana = len(kana)
            
            while s_index < len_surface and k_index < len_kana:
                current_char = surface[s_index]
                
                if self.is_kanji(current_char):
                    # 查找连续的汉字序列（汉字块）
                    kanji_end = s_index
                    while kanji_end < len_surface and self.is_kanji(surface[kanji_end]):
                        kanji_end += 1
                    kanji_block = surface[s_index:kanji_end]  # 连续汉字块
                    kanji_length = kanji_end - s_index  # 连续汉字数量
                    
                    # 确定当前汉字块对应的注音范围
                    end_k = k_index
                    # 查找下一个非汉字字符在注音中的位置
                    next_s = kanji_end
                    if next_s < len_surface:
                        next_char = surface[next_s]
                        # 在注音中找到与下一个非汉字字符匹配的位置
                        temp_k = k_index
                        while temp_k < len_kana:
                            if kana.startswith(next_char, temp_k):
                                end_k = temp_k
                                break
                            temp_k += 1
                    else:
                        end_k = len_kana  # 没有后续字符，使用全部剩余注音
                    
                    # 提取当前汉字块的注音
                    block_kana = kana[k_index:end_k]
                    if block_kana and block_kana != kanji_block:
                        # 为整个汉字块生成一个Ruby标签
                        result.append(f'<ruby>{kanji_block}<rp>(</rp><rt>{block_kana}</rt><rp>)</rp></ruby>')
                    else:
                        result.append(kanji_block)
                    
                    kanji_count += kanji_length  # 累加连续汉字数量
                    s_index = kanji_end  # 移动到汉字块结束位置
                    k_index = end_k  # 移动注音索引
                else:
                    # 非汉字字符直接添加
                    result.append(current_char)
                    # 同步注音索引（如果匹配）
                    if k_index < len_kana and kana[k_index] == current_char:
                        k_index += 1
                    s_index += 1
            
            # 添加剩余的非汉字字符
            while s_index < len_surface:
                result.append(surface[s_index])
                s_index += 1
            
            return ''.join(result), kanji_count
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            return surface, 0

    def japanese_to_kana_html(self, text):
        """使用SudachiPy分词，然后用pykakasi注音，生成带假名标注的HTML（仅标注汉字）"""
        try:
            if not text:
                return "", 0
            
            kana_parts = []
            total_kanji_count = 0
            
            # 使用Sudachi进行分词
            tokens = self.tokenizer_obj.tokenize(text, self.mode)
            
            for token in tokens:
                surface = token.surface()
                # 获取整个分词单元的注音
                kana = self.get_hiragana(surface)
                
                if kana and kana != surface:
                    # 处理分词单元，只给汉字添加注音
                    processed, kanji_count = self.process_token(surface, kana)
                    kana_parts.append(processed)
                    total_kanji_count += kanji_count
                else:
                    # 没有有效注音或注音与原词相同，直接添加原词
                    kana_parts.append(surface)
                    # 统计汉字数量
                    total_kanji_count += len(re.findall(r'[\u4e00-\u9fff]', surface))
            
            return ''.join(kana_parts), total_kanji_count
        except Exception as e:
            # 不在这里记录日志，让上层统一处理
            return text, 0

    def process_subtitles(self, input_srt_path, output_dir=None, export_format="默认导出", phonetic_mode="japanese"):
        """主处理函数：处理整个流程

        参数:
            phonetic_mode: 注音模式 ("japanese" / "english" / "chinese")
        """
        try:
            self.update_progress(5, "开始处理字幕文件...")
            
            # 1. 验证输入文件是否存在
            if not os.path.exists(input_srt_path):
                raise Exception(f"输入文件 {input_srt_path} 不存在！")
            
            # 设置输出目录
            if not output_dir:
                output_dir = os.path.dirname(input_srt_path)
            os.makedirs(output_dir, exist_ok=True)
         
            # 获取输入文件名（不含扩展名）
            input_filename = os.path.splitext(os.path.basename(input_srt_path))[0]
            
            # 2. 定义所有文件路径
            output_file_path_zh = os.path.join(output_dir, f'{input_filename}_jp.srt')
            output_file_path_jp = os.path.join(output_dir, f'{input_filename}_zh.srt')
            output_file_path_kashi = os.path.join(output_dir, f'{input_filename}_kashi.txt')
            jp_kashi_path = os.path.join(output_dir, f'{input_filename}_a_jp_kashi.txt')
            zh_kashi_path = os.path.join(output_dir, f'{input_filename}_a_zh_kashi.txt')
            ruby_input_path = os.path.join(output_dir, f'{input_filename}_sunshj.top_ruby.txt')
            ruby_modi_path = os.path.join(output_dir, f'{input_filename}_sunshj.top_ruby_modi.txt')
            ruby_before_k1_path = os.path.join(output_dir, f'{input_filename}_ruby_before_k1.txt')
            jp_ruby_srt_path = os.path.join(output_dir, f'{input_filename}_jp_ruby.srt')
            jp_ruby_ass_path = os.path.join(output_dir, f'{input_filename}_jp_ruby.ass')
            zh_ass_path = os.path.join(output_dir, f'{input_filename}_zh.ass')
            merged_ass_path = os.path.join(output_dir, f'{input_filename}_merged.ass')
            timeline_with_index_path = os.path.join(output_dir, f'{input_filename}_timeline_index.srt')
            jp_ruby_html_path = os.path.join(output_dir, f'{input_filename}_jp_ruby.html')
            
            self.update_progress(10, "执行step0操作...")
            # 3. 执行step0.py中的操作
            blocks, lang_type = self.validate_srt_structure(input_srt_path)
            self.add_blank_line_to_file(input_srt_path)
            self.process_file_zh(blocks, lang_type, output_file_path_zh)
            self.process_file_jp(blocks, lang_type, output_file_path_jp)
            self.process_file_to_kashi(blocks, lang_type, output_file_path_kashi)

            # 生成纯数字字幕文本（第一语言行和字幕序号块同一内容）
            self.process_file_to_timeline_with_index(blocks, timeline_with_index_path)

            # 如果是单语文件，直接生成ASS文件并返回
            #if lang_type == 'single':
                #self.update_progress(50, "单语文件处理...")
                #self.srt_to_ass(output_file_path_zh, merged_ass_path)
                #self.update_progress(100, "处理完成！")
                #self.log(f"单语文件处理完成！生成的文件：{merged_ass_path}")
                #return merged_ass_path
            
            self.update_progress(30, "执行step1操作...")
            # 4. 执行step1.py中的操作（仅双语文件需要）
            results = []
            
            # 处理文件操作
            results.append(self.handle_operation(self.remove_trailing_empty_lines, output_file_path_zh))
            results.append(self.handle_operation(self.remove_trailing_empty_lines, output_file_path_jp))
            results.append(self.handle_operation(self.add_blank_line_to_file, output_file_path_zh))
            results.append(self.handle_operation(self.add_blank_line_to_file, output_file_path_jp))
            
            # 匹配行数
            try:
                self.add_blank_lines_to_match_lines(output_file_path_zh, output_file_path_jp)
                results.append({
                    'operation': 'add_blank_lines_to_match_lines',
                    'file': f'{output_file_path_zh} 和 {output_file_path_jp}',
                    'success': True,
                    'error': None
                })
            except Exception as e:
                results.append({
                    'operation': 'add_blank_lines_to_match_lines',
                    'file': f'{output_file_path_zh} 和 {output_file_path_jp}',
                    'success': False,
                    'error': str(e)
                })
            
            # 提取文本
            results.append(self.handle_operation(self.process_file_to_nohonngokashi, output_file_path_zh, jp_kashi_path))
            results.append(self.handle_operation(self.process_file_to_nohonngokashi, output_file_path_jp, zh_kashi_path))
            
            # 检查是否有失败的操作
            failed_operations = [res for res in results if not res['success']]
            if failed_operations:
                error_msg = f"操作执行完成，但存在 {len(failed_operations)} 个失败项：\n"
                for fail in failed_operations:
                    error_msg += f"- 操作 {fail['operation']}（文件：{fail['file']}）失败：{fail['error']}\n"
                raise Exception(error_msg)
            
            self.update_progress(50, "进行日文假名标注...")
            # 5. 自动处理日文假名标注
            if not os.path.exists(jp_kashi_path):
                raise Exception(f"错误：文件 {jp_kashi_path} 不存在！")
            
            # 读取日文文本
            with open(jp_kashi_path, 'r', encoding='utf-8-sig') as f:
                jp_lines = [line.strip() for line in f.readlines() if line.strip()]

            # 读取中文文本（双语时）
            zh_lines = []
            if lang_type == 'double':
                with open(zh_kashi_path, 'r', encoding='utf-8-sig') as f:
                    zh_lines = [line.strip() for line in f.readlines() if line.strip()]

            # 对每行进行注音处理
            ruby_lines = []
            total_count = 0

            for i, jp_line in enumerate(jp_lines):
                # 根据注音模式选择处理方式
                if phonetic_mode == "english":
                    ruby_html, word_count, found_count = english_text_to_ruby(jp_line)
                    total_count += found_count
                elif phonetic_mode == "chinese":
                    ruby_html, hanzi_count, pinyin_count = chinese_text_to_ruby(jp_line)
                    total_count += pinyin_count
                else:
                    ruby_html, kanji_count = self.japanese_to_kana_html(jp_line)
                    total_count += kanji_count

                ruby_lines.append(ruby_html)

            # 记录日志
            if phonetic_mode == "english":
                self.log(f"英语音标标注完成，找到 {total_count} 个音标")
            elif phonetic_mode == "chinese":
                self.log(f"中文拼音标注完成，添加了 {total_count} 个拼音")
            else:
                self.log(f"假名标注完成，共处理了 {total_count} 个汉字")

            # 写入到sunshj.top_ruby.txt
            with open(ruby_input_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(ruby_lines))
            
            self.update_progress(70, "执行step2操作...")
            # 6. 执行step2.py中的操作
            # 匹配行数
            self.add_blank_lines_to_match_lines(output_file_path_zh, output_file_path_jp)
            
            # 复制文件
            shutil.copyfile(output_file_path_zh, jp_ruby_srt_path)
            
            # 静态汉字映射替换 - 使用所有规则（先默认规则，后用户规则）
            rules = ReplacementRules.get_all_rules()
            # 恢复规则中的转义字符
            replacements = []
            for old, new in rules:
                replacements.append((old.replace("&#124;", "|"), new.replace("&#124;", "|")))
            
            self.replace_multiple_text(ruby_input_path, ruby_modi_path, replacements)

            # 保存经过用户规则和默认规则替换后的ruby文本（替换{\k1}之前）
            self.save_ruby_before_k1(ruby_modi_path, ruby_before_k1_path)

            # 添加k1标记
            self.add_k1_to_lines(ruby_modi_path)

            # 替换行
            self.replace_lines(ruby_modi_path, output_file_path_zh, jp_ruby_srt_path)
            
            # 再次对齐时轴
            self.add_blank_lines_to_match_lines(output_file_path_zh, output_file_path_jp)
            self.replace_lines(zh_kashi_path, output_file_path_zh, output_file_path_jp)
            
            # 转换为ass格式
            self.srt_to_ass(jp_ruby_srt_path, jp_ruby_ass_path)
            self.srt_to_ass(output_file_path_jp, zh_ass_path)
            
            # 合并ass文件（第一个元素为占位符，实际使用内置内容）
            input_files = ['placeholder.ass', jp_ruby_ass_path, zh_ass_path]
            self.merge_ass_files(input_files, merged_ass_path)

            # 生成HTML格式的注音文件（使用ruby_before_k1_path，通过转换程序将{\k1}格式转换为HTML ruby格式）
            self.save_ruby_html(ruby_before_k1_path, jp_ruby_html_path, lang_type, zh_kashi_path)

            self.update_progress(100, "处理完成！")
            #self.log(f"所有操作完成！生成的文件：{merged_ass_path}")

            # 根据导出格式决定保留哪些文件
            kept_files = []
            temp_files = []

            if export_format == "全部格式导出":
                # 保留所有文件
                kept_files = [
                    output_file_path_zh,  # 第一语言
                    output_file_path_jp,  # 第二语言
                    jp_kashi_path,  # 第一语言纯文本
                    zh_kashi_path,  # 第二语言纯文本
                    merged_ass_path,  # 注音ass格式
                    jp_ruby_html_path,  # 注音html格式
                    timeline_with_index_path,  # 纯时间轴
                    # ruby_before_k1_path  # ruby替换后文本 - 不再输出此文件
                ]#output_file_path_zh, output_file_path_jp,
                temp_files = [output_file_path_kashi, ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path, zh_ass_path]
            elif export_format == "默认导出":
                kept_files = [merged_ass_path,output_file_path_zh, output_file_path_jp]
                temp_files = [output_file_path_kashi, zh_kashi_path, jp_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, timeline_with_index_path, jp_ruby_html_path]
            elif export_format == "注音ass格式":
                kept_files = [merged_ass_path]
                temp_files = [output_file_path_zh, output_file_path_jp,output_file_path_kashi, zh_kashi_path, jp_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, timeline_with_index_path, jp_ruby_html_path]
            elif export_format == "第一语言":
                kept_files = [output_file_path_zh]
                temp_files = [output_file_path_jp, output_file_path_kashi, zh_kashi_path, jp_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, merged_ass_path, timeline_with_index_path, jp_ruby_html_path]
            elif export_format == "第二语言":
                kept_files = [output_file_path_jp]
                temp_files = [output_file_path_zh, output_file_path_kashi, zh_kashi_path, jp_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, merged_ass_path, timeline_with_index_path, jp_ruby_html_path]
            elif export_format == "第一语言纯文本":
                kept_files = [jp_kashi_path]
                temp_files = [output_file_path_zh, output_file_path_jp, output_file_path_kashi, zh_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, merged_ass_path, timeline_with_index_path, jp_ruby_html_path]
            elif export_format == "第二语言纯文本":
                kept_files = [zh_kashi_path]
                temp_files = [output_file_path_zh, output_file_path_jp, output_file_path_kashi, jp_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, merged_ass_path, timeline_with_index_path, jp_ruby_html_path]
            elif export_format == "注音html格式":
                kept_files = [jp_ruby_html_path]
                temp_files = [output_file_path_zh, output_file_path_jp, output_file_path_kashi, zh_kashi_path, jp_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, merged_ass_path, timeline_with_index_path]
            elif export_format == "纯时间轴":
                kept_files = [timeline_with_index_path]
                temp_files = [output_file_path_zh, output_file_path_jp, output_file_path_kashi, zh_kashi_path, jp_kashi_path,
                             ruby_input_path, ruby_modi_path, ruby_before_k1_path, jp_ruby_srt_path, jp_ruby_ass_path,
                             zh_ass_path, merged_ass_path, jp_ruby_html_path]

            # 清理保留文件的开头空行
            files_to_clean = [input_srt_path] + kept_files
            for file_path in files_to_clean:
                try:
                    if os.path.exists(file_path):
                        self.remove_leading_empty_lines(file_path)
                except Exception as e:
                    self.log(f"清理文件 {file_path} 开头空行时出错: {e}")

            # 保留的文件添加到日志
            self.log(f"处理完成！导出格式：{export_format}")
            self.log(f"保留输出文件:")
            for file_path in kept_files:
                if os.path.exists(file_path):
                    self.log(f"- {file_path}")
            self.log(f"✅结果文件目录: {output_dir}")

            # 清理临时文件
            for temp_file in temp_files:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except Exception as e:
                    self.log(f"清理临时文件 {temp_file} 时出错: {e}")

            # 额外确保删除ruby_before_k1_path文件（无论在哪种导出格式下都不应该保留）
            try:
                if os.path.exists(ruby_before_k1_path):
                    os.remove(ruby_before_k1_path)
                    self.log(f"已删除不需要的ruby_before_k1文件: {ruby_before_k1_path}")
            except Exception as e:
                self.log(f"删除ruby_before_k1文件时出错: {e}")

            return merged_ass_path
            
        except Exception as e:
            error_msg = f"处理过程中发生错误：{str(e)}"
            self.log(error_msg)
            # 抛出原始异常，避免在上层重复记录错误
            raise

# 主界面类
class SubtitleToolGUI:
    def __init__(self, root):
        self.root = root

        self.root.title("日文汉字注音工具v1.0.2")
        self.root.geometry("800x700")

        # 设置窗口图标
        set_window_icon(self.root)

        self.font = set_ui_font()
        self.root.option_add("*Font", self.font)

        # 创建字幕处理器
        self.processor = SubtitleProcessor(logger=self.log_message, progress_callback=self.update_progress)

        # 注音模式选择变量（单选按钮组）
        # 值: "japanese" / "english" / "chinese"
        self.phonetic_mode = tk.StringVar(value="japanese")

        # 初始化各注音功能
        self.english_db_available = init_english_phonetic_db()
        self.chinese_pinyin_available = init_chinese_pinyin()

        # 创建界面元素
        self.create_widgets()

        # 设置拖拽功能
        self.setup_drag_drop()

        # 添加启动日志
        #self.log_message("程序启动完成，自动填充功能已启用")
    
    def create_widgets(self):
        # 输入文件选择区域
        input_frame = ttk.LabelFrame(self.root, text="输入文件")
        input_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.input_path_var = tk.StringVar()
        self.input_entry = ttk.Entry(input_frame, textvariable=self.input_path_var)
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)
        ttk.Button(input_frame, text="浏览...", command=self.browse_input_file).pack(side=tk.RIGHT, padx=5, pady=5)

        # 输出目录选择区域
        output_frame = ttk.LabelFrame(self.root, text="输出目录")
        output_frame.pack(fill=tk.X, padx=10, pady=5)

        # 输出目录输入框和按钮
        output_input_frame = ttk.Frame(output_frame)
        output_input_frame.pack(fill=tk.X, padx=5, pady=5)

        self.output_path_var = tk.StringVar()
        ttk.Entry(output_input_frame, textvariable=self.output_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(output_input_frame, text="浏览...", command=self.browse_output_dir).pack(side=tk.RIGHT)

        # 自动设置选项
        auto_frame = ttk.Frame(output_frame)
        auto_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        self.auto_set_output_var = tk.BooleanVar(value=True)
        auto_checkbox = ttk.Checkbutton(
            auto_frame,
            text="自动设置为输入文件所在目录",
            variable=self.auto_set_output_var,
            command=self.on_auto_setting_changed
        )
        auto_checkbox.pack(side=tk.LEFT)

        # 添加提示标签
        info_label = ttk.Label(auto_frame, text="💡 取消勾选可手动指定输出目录", foreground="#666666", font=("", 9))
        info_label.pack(side=tk.RIGHT)

        # 绑定输入路径变化事件，实现自动填充输出路径
        self.input_path_var.trace_add("write", self.auto_set_output_dir)

        # 导出格式选择区域
        format_frame = ttk.LabelFrame(self.root, text="导出格式选择")
        format_frame.pack(fill=tk.X, padx=10, pady=5)

        self.export_format_var = tk.StringVar(value="默认导出")
        format_options = [
            "默认导出",
            "注音ass格式",
            "第一语言",
            "第二语言",
            "第一语言纯文本",
            "第二语言纯文本",
            "注音html格式",
            "纯时间轴",
            "全部格式导出"
        ]

        ttk.Label(format_frame, text="选择导出格式:").pack(side=tk.LEFT, padx=5, pady=5)
        format_combobox = ttk.Combobox(format_frame, textvariable=self.export_format_var,
                                     values=format_options, state="readonly", width=15)
        format_combobox.pack(side=tk.LEFT, padx=5, pady=5)

        # 注音模式单选按钮组（与导出格式选择在同一行）
        ttk.Label(format_frame, text=" | ").pack(side=tk.LEFT, padx=5)

        self.japanese_radio = ttk.Radiobutton(
            format_frame,
            text="日语注音",
            variable=self.phonetic_mode,
            value="japanese",
            command=self.on_phonetic_mode_change
        )
        self.japanese_radio.pack(side=tk.LEFT, padx=5, pady=5)

        self.english_radio = ttk.Radiobutton(
            format_frame,
            text="英语注音",
            variable=self.phonetic_mode,
            value="english",
            command=self.on_phonetic_mode_change
        )
        self.english_radio.pack(side=tk.LEFT, padx=5, pady=5)
        # 如果英语数据库不可用，禁用英语注音
        if not self.english_db_available:
            self.english_radio.config(state=tk.DISABLED)

        self.chinese_radio = ttk.Radiobutton(
            format_frame,
            text="中文注音",
            variable=self.phonetic_mode,
            value="chinese",
            command=self.on_phonetic_mode_change
        )
        self.chinese_radio.pack(side=tk.LEFT, padx=5, pady=5)
        # 如果中文拼音库不可用，禁用中文注音
        if not self.chinese_pinyin_available:
            self.chinese_radio.config(state=tk.DISABLED)

        # 按钮区域
        button_frame = ttk.Frame(self.root)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.process_btn = ttk.Button(button_frame, text="开始处理", command=self.start_processing)
        self.process_btn.pack(side=tk.LEFT, padx=5)
        
        self.swap_btn = ttk.Button(button_frame, text="双语互换", command=self.swap_subtitle_lines)
        self.swap_btn.pack(side=tk.LEFT, padx=5)
        
        self.merge_btn = ttk.Button(button_frame, text="合并双语", command=self.merge_subtitles)
        self.merge_btn.pack(side=tk.LEFT, padx=5)
        
        self.rules_btn = ttk.Button(button_frame, text="自定义注音", command=self.open_rules_window)
        self.rules_btn.pack(side=tk.LEFT, padx=5)

        self.open_dir_btn = ttk.Button(button_frame, text="输出目录", command=self.open_output_dir)
        self.open_dir_btn.pack(side=tk.LEFT, padx=5)
        

        self.help_btn = ttk.Button(button_frame, text="帮助", command=self.show_help)
        self.help_btn.pack(side=tk.LEFT, padx=5)
  
        # 进度条
        self.progress_var = tk.DoubleVar()
        progress_frame = ttk.Frame(self.root)
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(progress_frame, text="处理进度:").pack(side=tk.LEFT)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5)
        
        # 状态标签
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var).pack(anchor=tk.W, padx=10, pady=5)
        
        # 日志区域
        log_frame = ttk.LabelFrame(self.root, text="日志")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
    
    def browse_input_file(self):
        """浏览选择输入文件"""
        try:
            file_path = filedialog.askopenfilename(
                title="选择SRT字幕文件",
                filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
            )
            if file_path:
                self.input_path_var.set(file_path)
        except Exception as e:
            self.log_message(f"浏览输入文件错误: {str(e)}")
    
    def browse_output_dir(self):
        """浏览选择输出目录"""
        try:
            dir_path = filedialog.askdirectory(title="选择输出目录")
            if dir_path:
                self.output_path_var.set(dir_path)
        except Exception as e:
            self.log_message(f"浏览输出目录错误: {str(e)}")
    
    def auto_set_output_dir(self, *args):
        """自动设置输出目录为输入文件所在目录，只要输入文件路径一填充就更新"""
        try:
            # 检查是否启用自动设置
            if not hasattr(self, 'auto_set_output_var') or not self.auto_set_output_var.get():
                return

            input_path = self.input_path_var.get().strip()

            # 忽略提示文本
            if input_path == "拖拽SRT文件到此处或点击浏览...":
                return

            if input_path:
                # 即使文件不存在，也可以获取目录路径
                output_dir = os.path.dirname(input_path)
                if output_dir:
                    # 检查当前输出目录是否已经手动设置过
                    current_output = self.output_path_var.get().strip()

                    # 如果输出目录为空或者与输入文件的目录不同，则自动设置
                    if not current_output or current_output != output_dir:
                        self.output_path_var.set(output_dir)

                        if os.path.exists(input_path):
                            # 使用 after 方法延迟执行，确保在拖拽日志之后显示
                            self.root.after(10, lambda: self.log_message(f"✅ 自动设置输出目录: {output_dir}"))
                        else:
                            self.root.after(10, lambda: self.log_message(f"⚠️ 文件不存在，但已设置输出目录: {output_dir}"))

                    # 验证输出目录是否可写
                    if os.path.exists(output_dir) and not os.access(output_dir, os.W_OK):
                        self.root.after(10, lambda: self.log_message(f"⚠️ 输出目录没有写入权限: {output_dir}"))

            else:
                # 输入路径为空时，如果启用自动设置，则清空输出目录
                if self.auto_set_output_var.get():
                    self.output_path_var.set("")
                    self.log_message("📝 输入路径为空，已清空输出目录")

        except Exception as e:
            self.log_message(f"❌ 自动设置输出目录错误: {str(e)}")

    def on_auto_setting_changed(self):
        """当自动设置选项改变时的回调"""
        try:
            if self.auto_set_output_var.get():
                # 启用自动设置时，立即执行一次自动设置
                self.auto_set_output_dir()
                self.log_message("✅ 已启用自动设置输出目录")
            else:
                self.log_message("📝 已禁用自动设置输出目录，可手动指定输出目录")
        except Exception as e:
            self.log_message(f"❌ 切换自动设置选项错误: {str(e)}")
    
    def log_message(self, message):
        """添加日志信息"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
    
    def update_progress(self, value, message=None):
        """更新进度条"""
        self.progress_var.set(value)
        if message:
            self.status_var.set(message)
        self.root.update_idletasks()

    def show_help(self):
        """显示帮助窗口"""
        try:
            # 创建帮助窗口
            help_window = tk.Toplevel(self.root)
            help_window.title("软件使用帮助")
            help_window.geometry("900x700")
            help_window.transient(self.root)
            help_window.grab_set()

            # 设置窗口图标
            set_window_icon(help_window)

            # 添加全屏功能
            help_window.fullscreen = False
            def toggle_help_fullscreen(event=None):
                help_window.fullscreen = not help_window.fullscreen
                help_window.attributes("-fullscreen", help_window.fullscreen)
                return "break"

            # 创建主框架
            main_frame = ttk.Frame(help_window)
            main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            # 标题区域
            title_frame = ttk.Frame(main_frame)
            title_frame.pack(fill=tk.X, pady=(0, 10))

            # 左侧标题区域
            left_frame = ttk.Frame(title_frame)
            left_frame.pack(side=tk.LEFT)

            title_label = ttk.Label(left_frame, text="日文汉字注音工具 - 使用帮助", font=("", 16, "bold"))
            title_label.pack(side=tk.LEFT)

            # 版本信息紧跟标题
            #version_label = ttk.Label(left_frame, text="v0.1", foreground="#1E88E5", font=("", 12, "bold"))
            #version_label.pack(side=tk.LEFT, padx=(10, 0))

            # 右侧控制区域
            right_frame = ttk.Frame(title_frame)
            right_frame.pack(side=tk.RIGHT)

            # 全屏按钮
            ttk.Button(right_frame, text="全屏(F11)", command=toggle_help_fullscreen).pack(side=tk.RIGHT, padx=(0, 10))

            # 创建笔记本控件（标签页）
            notebook = ttk.Notebook(main_frame)
            notebook.pack(fill=tk.BOTH, expand=True)

            # 快速入门标签页
            self._create_quick_start_tab(notebook)

            # 功能详解标签页
            self._create_features_tab(notebook)

            # 窗口控制标签页
            self._create_window_control_tab(notebook)

            # 输出格式说明标签页
            self._create_formats_tab(notebook)

            # 版本更新标签页
            self._create_version_update_tab(notebook)

            # 常见问题标签页
            self._create_faq_tab(notebook)

            # 关于软件标签页
            self._create_about_tab(notebook)

            # 底部按钮区域
            button_frame = ttk.Frame(main_frame)
            button_frame.pack(fill=tk.X, pady=(10, 0))

            # 创建关闭按钮并居中显示
            close_btn = ttk.Button(button_frame, text="关闭", command=help_window.destroy)
            close_btn.pack(anchor=tk.CENTER, pady=5)

            # 绑定键盘快捷键
            help_window.bind("<F11>", toggle_help_fullscreen)
            help_window.bind("<Escape>", lambda e: help_window.destroy())

        except Exception as e:
            self.log_message(f"打开帮助窗口错误: {str(e)}")

    def _create_quick_start_tab(self, notebook):
        """创建快速入门标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="快速入门")

        # 创建滚动文本框
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("", 11), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 快速入门内容
        content = """🎯 快速入门指南

欢迎使用日文汉字注音工具！本工具专为处理日语字幕文件而设计，支持多种输出格式和智能功能。

📋 基本使用步骤：

1️⃣ 选择输入文件
   • 点击"浏览..."按钮选择SRT字幕文件
   • 或直接拖拽SRT文件到输入框
   • 支持单语（纯日语）和双语（日语+中文）字幕

2️⃣ 设置输出目录
   • 程序会自动设置为输入文件所在目录
   • 可手动选择其他输出目录
   • 可取消勾选"自动设置"来手动指定

3️⃣ 选择导出格式
   • 默认导出：生成所有格式文件
   • 注音ass格式：带假名注音的ASS字幕
   • 第一语言：提取日语字幕
   • 第二语言：提取中文字幕
   • 纯文本格式：提取纯文本内容
   • 注音html格式：网页版注音显示
   • 纯时间轴：只保留时间信息

4️⃣ 开始处理
   • 点击"开始处理"按钮
   • 观察进度条和日志信息
   • 处理完成后可打开输出目录查看结果

🔧 常用功能：

• 双语字幕行互换：交换日语和中文字幕的位置
• 自定义多音字注音规则：设置特殊汉字的假名读音
• 合并双语字幕：将两个单语字幕合并为双语字幕
• 拖拽支持：直接拖拽文件到程序界面

💡 小贴士：

• 支持UTF-8编码的SRT文件
• 建议使用双语字幕获得最佳效果
• 可以批量处理多个文件（逐个处理）
• 所有操作都有详细的日志记录
• “replacement_rules.txt”切勿删除，自定义多音字注音规则文本源 
• 将得到的“注音ass格式”文件用Aegisup软件打开
  如果是中日双语字幕，把译文（中文）全选，样式列中的“Default”全部改为“Default_ZH”
  在软件菜单栏“自动化”—>“Apply karaoke template—>应用卡拉OK模版”
  
🎉 开始使用：

现在您可以选择一个SRT字幕文件开始体验本工具的强大功能！"""

        # 插入内容
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)

    def _create_features_tab(self, notebook):
        """创建功能详解标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="功能详解")

        # 创建滚动文本框
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("", 11), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 功能详解内容
        content = """🔧 功能详解

本工具提供了丰富的日语字幕处理功能，以下是各功能的详细说明：

📁 文件输入输出
• 支持拖拽操作：直接将SRT文件拖拽到输入框
• 智能编码检测：自动处理UTF-8、UTF-8-BOM等编码
• 自动输出目录：根据输入文件自动设置输出目录
• 批量处理支持：可连续处理多个文件

🎯 核心处理功能

1. 日语假名标注
   • 使用Sudachi分词器进行精确分词
   • 自动为汉字添加假名注音
   • 支持自定义多音字规则
   • 智能处理片假名和平假名

2. 字幕格式转换
   • SRT转ASS格式
   • 添加样式和特效
   • 保持时间轴精确同步
   • 支持双语显示

3. 文本提取处理
   • 提取纯文本内容
   • 去除时间轴和序号
   • 保持原文结构
   • 支持单语和双语提取

🎨 高级功能

• 双语字幕行互换
  - 交换日语和中文字幕位置
  - 保持时间轴不变
  - 适用于不同语言习惯

• 自定义多音字注音规则
  - 添加特殊汉字读音
  - 导入导出规则文件
  - 实时预览效果
  - 支持批量管理

• 合并双语字幕
  - 将两个单语字幕合并
  - 自动校验文件兼容性
  - 智能匹配时间轴
  - 生成标准双语格式

🔍 质量控制

• 文件格式校验
  - SRT格式完整性检查
  - 时间轴合法性验证
  - 字幕块数量匹配
  - 编码兼容性检测

• 智能错误处理
  - 详细错误日志
  - 自动修复常见问题
  - 用户友好的错误提示
  - 处理过程可视化

⚙️ 界面特性

• 现代化界面设计
• 实时进度显示
• 详细操作日志
• 全屏模式支持
• 拖拽操作支持
• 智能提示系统"""

        # 插入内容
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)

    def _create_formats_tab(self, notebook):
        """创建输出格式说明标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="输出格式")

        # 创建滚动文本框
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("", 11), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 输出格式说明内容
        content = """📋 输出格式详解

本工具支持多种输出格式，满足不同使用场景的需求：

🎯 默认导出
• 文件：生成所有格式的文件
• 用途：一次性获得所有可能需要的格式
• 特点：包含注音ASS、双语SRT、纯文本、HTML等
• 推荐：首次使用或不确定需要哪种格式时

📺 视频字幕格式

1. 注音ASS格式 (*.ass)
   • 文件名：{输入文件名}_merged.ass
   • 特点：包含假名注音的高级字幕格式
   • 用途：支持注音显示的视频播放器
   • 优势：样式丰富，支持特效和多层显示

2. 第一语言 (*.srt)
   • 文件名：{输入文件名}_jp.srt
   • 特点：纯日语字幕文件
   • 用途：日语学习或纯日语环境
   • 格式：标准SRT格式，兼容性好

3. 第二语言 (*.srt)
   • 文件名：{输入文件名}_zh.srt
   • 特点：纯中文字幕文件
   • 用途：中文观看或翻译参考
   • 格式：标准SRT格式

4. 纯时间轴 (*.srt)
   • 文件名：{输入文件名}_timeline_index.srt
   • 特点：只保留时间信息和序号
   • 用途：时间轴模板或同步参考
   • 内容：序号、时间轴、字幕块编号

📄 文本格式

5. 第一语言纯文本 (*.txt)
   • 文件名：{输入文件名}_a_jp_kashi.txt
   • 特点：纯日语文本，无时间轴
   • 用途：文本分析、学习材料制作
   • 格式：每行一句，保持原始顺序

6. 第二语言纯文本 (*.txt)
   • 文件名：{输入文件名}_a_zh_kashi.txt
   • 特点：纯中文文本，无时间轴
   • 用途：翻译对照、内容提取
   • 格式：每行一句，对应日语文本

🌐 网页格式

7. 注音HTML格式 (*.html)
   • 文件名：{输入文件名}_jp_ruby.html
   • 特点：网页版假名注音显示
   • 用途：在线学习、网页展示
   • 功能：可调节注音大小、颜色、样式
   • 特色：支持Ruby标签，浏览器直接打开

📊 文件管理规则

• 相同格式重复导出：新文件替换旧文件
• 不同格式导出：文件可以并存
• 默认导出：生成所有格式，覆盖同名文件
• 智能保护：单一格式导出时保留其他格式文件

💡 选择建议

• 视频播放：推荐注音ASS格式
• 学习日语：推荐注音HTML格式
• 翻译工作：推荐双语SRT格式
• 文本处理：推荐纯文本格式
• 时间同步：推荐纯时间轴格式"""

        # 插入内容
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)

    def _create_faq_tab(self, notebook):
        """创建常见问题标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="常见问题")

        # 创建滚动文本框
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("", 11), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 常见问题内容
        content = """❓ 常见问题解答

以下是用户经常遇到的问题和解决方案：

🔧 安装和运行问题

Q: 程序无法启动，提示缺少模块？
A: 请确保安装了所有必需的Python库：
   • tkinter (通常随Python安装)
   • tkinterdnd2 (拖拽支持)
   • sudachipy (日语分词)
   • pykakasi (假名转换)
   使用命令：pip install tkinterdnd2 sudachipy pykakasi

Q: 程序启动很慢？
A: 首次启动时需要加载日语词典，这是正常现象。
   后续启动会更快。

📁 文件处理问题

Q: 提示"文件编码不支持"？
A: 请确保SRT文件使用UTF-8编码保存。
   可以用记事本打开文件，另存为时选择UTF-8编码。

Q: 处理后的文件乱码？
A: 这通常是编码问题。请：
   1. 检查原文件编码
   2. 使用支持UTF-8的播放器
   3. 确认系统语言设置

Q: 双语字幕只显示一种语言？
A: 请检查：
   1. 原文件是否为标准双语格式
   2. 每个字幕块是否包含两行内容
   3. 选择正确的导出格式

🎯 功能使用问题

Q: 注音音标与原文行间距过大，在Aegisub中调整样式名为Default-furigana的“垂直边距”无效
A: 通过[编辑]-[查找替换]-勾选[使用正则表达式]-勾选[文本]：
   1. 假如要修改 Default-furigana的文本{\\an5\pos(881,915)}わたし 中的915（注音Y轴坐标）
   2. [查找目标]输入框中输入 \\\pos\((\d+),915\) [替换为]输入框输入 \\\pos\($1,950) 
   3. Y轴坐标正数向下移动，负数方向上移动
   4. 替换完成后一定不要再运行 Apply karaoke template—应用卡拉OK模版！！！不然相当于又撤销了刚才的替换

Q: 假名注音不准确？
A: 可以通过以下方式改善：
   1. 使用"自定义多音字注音规则"功能
   2. 添加特殊词汇的正确读音
   3. 检查原文是否有错别字

Q: ASS字幕在播放器中显示异常？
A: 请：
   1. 使用支持ASS格式的播放器（如VLC、PotPlayer）
   2. 检查播放器字幕设置
   3. 确认字体安装情况

Q: 合并字幕失败？
A: 常见原因：
   1. 两个文件字幕块数量不匹配
   2. 时间轴格式不一致
   3. 文件编码不兼容
   解决：使用程序的自动校验功能

⚙️ 性能和优化

Q: 处理大文件很慢？
A: 这是正常现象，处理时间取决于：
   • 文件大小和字幕数量
   • 计算机性能
   • 日语文本复杂度
   建议：耐心等待，观察进度条

Q: 内存占用过高？
A: 处理大文件时会占用较多内存，这是正常的。
   处理完成后内存会自动释放。

🎨 界面和操作

Q: 如何使用拖拽功能？
A: 直接将SRT文件拖拽到输入框即可，
   支持从文件管理器拖拽。

Q: 全屏模式如何退出？
A: 按F11键或ESC键退出全屏模式。

Q: 日志信息太多，如何清理？
A: 重新启动程序会清空日志，
   或者滚动到底部查看最新信息。

🔍 故障排除

Q: 程序崩溃或无响应？
A: 请：
   1. 检查文件是否损坏
   2. 重启程序
   3. 检查系统资源
   4. 更新到最新版本

Q: 输出文件为空？
A: 可能原因：
   1. 输入文件格式不正确
   2. 权限不足无法写入
   3. 磁盘空间不足
   检查日志信息获取详细错误

💡 使用技巧

• 建议先用小文件测试功能
• 定期备份重要的字幕文件
• 使用"默认导出"获得所有格式
• 自定义规则可以导出备份
• 观察日志信息了解处理过程

如果问题仍未解决，请检查日志信息或联系技术支持。"""

        # 插入内容
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)

    def _create_about_tab(self, notebook):
        """创建关于软件标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="关于软件")

        # 创建滚动文本框
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("", 11), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 关于软件内容
        content = """📱 关于软件

🎯 软件简介

日文汉字注音工具是一款专业的日语字幕处理软件，专为日语学习者、翻译工作者和视频制作者设计。

✨ 主要特色

• 智能假名标注：使用先进的日语分词技术
• 多格式输出：支持ASS、SRT、HTML、TXT等格式
• 双语处理：完美支持日中双语字幕
• 用户友好：现代化界面，操作简单直观
• 高度定制：支持自定义注音规则和样式

🔧 技术架构

• 编程语言：Python 3.x
• 界面框架：Tkinter
• 日语处理：Sudachi + PyKakasi
• 字幕解析：自研SRT解析引擎
• 文本处理：正则表达式 + 自然语言处理

📚 核心库依赖

• tkinter：图形用户界面
• tkinterdnd2：拖拽功能支持
• sudachipy：日语形态素分析
• pykakasi：假名转换处理
• re：正则表达式处理

🎨 版本信息

• 当前版本：v0.1
• 发布日期：2025年10月9日
• 更新内容：
  - 新增帮助系统
  - 优化处理性能
  - 改进用户界面
  - 增强错误处理
  - 添加更多输出格式

🏆 功能亮点

1. 智能分词技术
   • 基于Sudachi词典的精确分词
   • 支持现代日语和古典日语
   • 自动识别专有名词

2. 假名注音系统
   • 自动为汉字添加假名
   • 支持音读、训读识别
   • 可自定义特殊读音

3. 多格式支持
   • ASS高级字幕格式
   • 标准SRT字幕格式
   • HTML网页注音格式
   • 纯文本提取格式

4. 智能文件管理
   • 自动输出目录设置
   • 智能文件冲突处理
   • 批量处理支持

📄 使用许可

本软件仅供学习和个人使用，请勿用于商业用途。
使用本软件处理的内容请遵守相关版权法律法规。

🔒 隐私保护

• 本软件不收集用户个人信息
• 所有处理均在本地完成
• 不会上传任何文件到网络
• 用户数据完全保密

🛠️ 技术支持

如遇到问题或有改进建议，请：
• 查看帮助文档和常见问题
• 检查软件日志信息
• 确保使用最新版本
• 提供详细的错误描述
• 反馈邮箱：

💝 致谢

感谢以下开源项目的支持：
• Sudachi：日语形态素分析器
• PyKakasi：假名转换库
• Tkinter：Python GUI框架
• 所有测试用户的反馈和建议

🎉 结语

希望这款工具能够帮助您更好地处理日语字幕，
提升日语学习和工作效率！

感谢您的使用！"""

        # 插入内容
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)

    def _create_window_control_tab(self, notebook):
        """创建窗口控制标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="窗口控制")

        # 创建滚动文本框
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("", 11), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 窗口控制功能内容
        content = """🪟 窗口控制功能详解

本软件提供了完整的现代化窗口控制功能，让您能够灵活地管理应用程序窗口。

🎛️ **标准窗口控制功能**

✨ **基础窗口操作**：
• 🔲 **最大化**：点击窗口标题栏右上角的最大化按钮（□），窗口将扩展到全屏大小，充分利用屏幕空间
• 🔳 **还原**：当窗口处于最大化状态时，点击还原按钮（⧉），窗口将恢复到之前的大小和位置
• ➖ **最小化**：点击最小化按钮（－），窗口将缩小到任务栏，程序继续在后台运行
• ❌ **关闭**：点击关闭按钮（✕），将完全退出程序并释放所有资源

🚀 **高级窗口功能**

🔧 **全屏模式**：
• 🎯 **快速全屏**：按下 F11 键可快速切换到全屏模式，隐藏标题栏和边框
• 🔄 **退出全屏**：再次按下 F11 键或 ESC 键退出全屏模式
• 💡 **使用场景**：适合需要大屏幕工作空间的场景，如查看大量日志信息或处理复杂字幕

🎨 **窗口美化特性**

✨ **视觉效果**：
• 🖼️ **自定义图标**：所有窗口都显示统一的应用程序图标，提升视觉识别度
• 🎭 **现代化界面**：采用现代化的扁平设计风格，界面简洁美观
• 🌈 **主题一致性**：所有子窗口保持与主窗口一致的视觉风格

🔧 **智能窗口管理**

📐 **自动布局**：
• 📍 **智能定位**：新窗口自动居中显示，确保最佳的用户体验
• 📏 **合理尺寸**：根据内容自动调整窗口大小，避免过大或过小
• 🔗 **窗口关联**：子窗口与主窗口保持关联，关闭主窗口时自动关闭所有子窗口

⚡ **快捷操作**

⌨️ **键盘快捷键**：
• F11：切换全屏模式
• ESC：退出全屏模式或关闭当前窗口
• Alt + F4：关闭当前窗口（系统标准）
• Win + ↑：最大化窗口（系统标准）
• Win + ↓：还原/最小化窗口（系统标准）

🖱️ **鼠标操作**：
• 双击标题栏：快速最大化/还原窗口
• 拖拽标题栏：移动窗口位置
• 拖拽窗口边缘：调整窗口大小
• 右键标题栏：显示系统窗口菜单

🎯 **特殊窗口功能**

🔍 **帮助窗口**：
• 📚 支持全屏浏览，方便查看详细帮助信息
• 🔄 可在多个标签页间快速切换
• 📖 提供完整的功能说明和使用指南

🛠️ **工具窗口**：
• ⚙️ 自定义规则窗口支持全屏编辑
• 🔗 合并字幕窗口提供大屏幕操作空间
• 📊 所有工具窗口都支持灵活的大小调整

💡 **使用技巧**

🎨 **最佳实践**：
• 🖥️ 在大屏幕上使用最大化模式获得最佳体验
• 📱 在小屏幕上使用全屏模式节省空间
• 🔄 使用快捷键提高操作效率
• 📐 根据工作内容调整合适的窗口大小

⚠️ **注意事项**：
• 🔒 模态窗口（如设置窗口）需要先关闭才能操作主窗口
• 💾 关闭窗口前确保已保存重要数据
• 🔄 全屏模式下可能隐藏部分系统界面元素
• 📊 某些功能在最小化状态下可能暂停更新

🎉 **总结**

本软件的窗口控制系统经过精心设计，提供了：
• 🎯 直观易用的标准窗口操作
• ⚡ 高效便捷的快捷键支持
• 🎨 美观统一的视觉体验
• 🔧 灵活强大的高级功能

无论您是初次使用还是资深用户，都能找到适合自己的窗口操作方式！"""

        # 插入内容
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)

    def _create_version_update_tab(self, notebook):
        """创建版本更新标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="版本更新")

        # 创建滚动文本框
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        text_widget = tk.Text(text_frame, wrap=tk.WORD, font=("", 11), state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 版本更新内容
        content = """📋 版本更新日志

🎯 **当前版本：v0.1.2

本版本为初始发布版本，包含了完整的日语字幕处理功能。

---

🚀 v0.1.2
• HTML输出格式兼容双语/单语文本输出；注音+原文+译文自定义大小、颜色等！
• 字幕字体更改为开源字体思源黑体系列中等字体

### 🎉 发布说明

这是日文汉字注音工具的首个正式版本，经过充分的测试和优化，提供了完整的日语字幕处理功能。

🎯 **主要亮点**：
• 🚀 **功能完整**：涵盖了日语字幕处理的所有核心需求
• 🎨 **界面美观**：现代化的用户界面设计
• 🛡️ **稳定可靠**：经过大量测试，确保稳定性
• 📚 **文档完善**：提供详细的使用说明和帮助

## 🚀 v0.1 (2025年10月9日) - 初始版本

### ✨ 新增功能

🎯 **核心处理功能**：
• 📝 **SRT字幕解析**：完整支持标准SRT格式字幕文件
• 🔤 **日语假名标注**：使用Sudachi分词器进行精确的假名标注
• 🎨 **多格式输出**：支持ASS、SRT、HTML、TXT等多种输出格式
• 🌐 **双语字幕处理**：完美支持日中双语字幕的处理和转换

🛠️ **高级功能**：
• ⚙️ **自定义多音字规则**：支持用户自定义特殊汉字的假名读音
• 🔗 **双语字幕合并**：将两个单语字幕文件合并为双语字幕
• 🔄 **字幕行互换**：支持双语字幕中日语和中文行的位置交换
• 📁 **批量处理**：支持连续处理多个字幕文件

🎨 **用户界面**：
• 🖥️ **现代化界面**：采用Tkinter现代化设计风格
• 🖱️ **拖拽支持**：支持直接拖拽SRT文件到程序界面
• 📊 **实时进度显示**：处理过程中显示详细的进度信息
• 📝 **详细日志**：提供完整的操作日志和错误提示

🪟 **窗口管理**：
• 🔲 **标准窗口控制**：支持最大化、最小化、关闭等标准操作
• 🖼️ **全屏模式**：支持F11全屏模式，适合大屏幕操作
• 🎭 **自定义图标**：所有窗口显示统一的应用程序图标
• 📐 **智能布局**：窗口自动居中，合理的尺寸设置

### 🔧 技术特性

💻 **核心技术**：
• 🧠 **Sudachi分词**：使用业界领先的日语分词技术
• 🔤 **PyKakasi转换**：高质量的假名转换处理
• 📄 **SRT解析引擎**：自研的SRT格式解析和验证系统
• 🎯 **正则表达式**：强大的文本处理和替换功能

🛡️ **质量保证**：
• ✅ **格式校验**：完整的SRT文件格式校验系统
• 🔍 **错误检测**：智能的错误检测和修复建议
• 📊 **数据验证**：时间轴、序号、内容的完整性验证
• 🔒 **编码支持**：完美支持UTF-8编码，兼容BOM格式

### 🎯 输出格式支持

📺 **视频字幕格式**：
• 🎬 **ASS格式**：带假名注音的高级字幕格式
• 📽️ **SRT格式**：标准字幕格式，分离日语和中文
• ⏱️ **时间轴格式**：纯时间轴模板文件

📄 **文本格式**：
• 📝 **纯文本**：提取的日语和中文文本内容
• 🌐 **HTML格式**：网页版假名注音显示
• 📋 **自定义格式**：支持用户自定义的输出格式

### 🔍 文件管理

📁 **智能路径管理**：
• 🎯 **自动输出目录**：根据输入文件自动设置输出目录
• 📂 **手动路径选择**：支持用户手动指定输出路径
• 🔄 **文件覆盖保护**：智能的文件覆盖提示和保护

📊 **处理统计**：
• 📈 **实时进度**：显示处理进度和剩余时间
• 📋 **处理报告**：详细的处理结果统计
• 🔍 **错误报告**：完整的错误信息和解决建议

### 🎨 用户体验

🖱️ **操作便利性**：
• 🎯 **一键处理**：简单的一键式处理流程
• 🖱️ **拖拽操作**：支持文件拖拽，操作更直观
• ⌨️ **快捷键**：丰富的键盘快捷键支持
• 💡 **智能提示**：贴心的操作提示和帮助信息

📚 **帮助系统**：
• 📖 **完整帮助**：详细的使用说明和功能介绍
• ❓ **常见问题**：常见问题的解答和解决方案
• 🔧 **窗口控制说明**：详细的窗口操作指南
• 📋 **格式说明**：各种输出格式的详细说明

### 🛠️ 系统要求

💻 **运行环境**：
• 🖥️ **操作系统**：Windows 10/11, macOS 10.14+, Linux
• 🐍 **Python版本**：Python 3.8+ (开发环境)
• 💾 **内存要求**：建议4GB以上RAM
• 💿 **存储空间**：至少100MB可用空间

📦 **依赖库**：
• tkinter：图形用户界面
• tkinterdnd2：拖拽功能支持
• sudachipy：日语形态素分析
• pykakasi：假名转换处理
• pysrt：SRT文件处理

💡 **使用建议**：
• 建议首次使用时阅读快速入门指南
• 推荐使用UTF-8编码的SRT文件
• 可以先用小文件测试各种功能
• 遇到问题请查看常见问题解答

🔮 **未来计划**：
• 持续优化处理性能
• 增加更多输出格式支持
• 改进用户界面体验
• 添加更多高级功能

---

## 📞 技术支持

如果您在使用过程中遇到任何问题，请：
• 📖 首先查看帮助文档和常见问题
• 🔍 检查软件日志信息
• 📝 提供详细的错误描述
• 💾 确保使用最新版本

感谢您使用日文汉字注音工具！"""

        # 插入内容
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)

    def on_phonetic_mode_change(self):
        """注音模式单选按钮切换事件"""
        mode = self.phonetic_mode.get()

        if mode == "japanese":
            self.log_message("✓ 已选择日语注音模式（默认）")
        elif mode == "english":
            self.log_message("✓ 已选择英语注音模式")
            if not self.english_db_available:
                messagebox.showwarning(
                    "警告",
                    f"英语音标数据库 '{PHONETIC_DB_FILE}' 不存在或为空！\n"
                    "英语注音功能将无法正常工作。"
                )
        elif mode == "chinese":
            self.log_message("✓ 已选择中文注音模式")
            if not self.chinese_pinyin_available:
                messagebox.showwarning(
                    "警告",
                    "pypinyin库未安装或初始化失败！\n"
                    "中文注音功能将无法正常工作。"
                )

    def open_rules_window(self):
        """打开替换规则窗口"""
        try:
            # 创建规则窗口，设置为模态窗口
            rules_window = ReplacementRulesWindow(self.root)
            self.root.wait_window(rules_window)  # 等待窗口关闭
        except Exception as e:
            self.log_message(f"打开替换规则窗口错误: {str(e)}")

    def merge_subtitles(self):
        """合并双语字幕功能"""
        try:
            # 创建合并字幕窗口
            merge_window = tk.Toplevel(self.root)
            merge_window.title("合并双语字幕")
            merge_window.geometry("700x750")
            merge_window.transient(self.root)
            merge_window.grab_set()

            # 设置窗口图标
            set_window_icon(merge_window)

            # 添加全屏功能
            merge_window.fullscreen = False
            def toggle_fullscreen(event=None):
                merge_window.fullscreen = not merge_window.fullscreen
                merge_window.attributes("-fullscreen", merge_window.fullscreen)
                return "break"

            def exit_fullscreen(event=None):
                merge_window.fullscreen = False
                merge_window.attributes("-fullscreen", False)
                return "break"

            merge_window.bind("<F11>", toggle_fullscreen)
            merge_window.bind("<Escape>", exit_fullscreen)

            # 主框架
            main_frame = ttk.Frame(merge_window)
            main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

            # 标题和控制按钮区域
            title_frame = ttk.Frame(main_frame)
            title_frame.pack(fill=tk.X, pady=(0, 10))

            title_label = ttk.Label(title_frame, text="合并双语字幕", font=("", 14, "bold"))
            title_label.pack(side=tk.LEFT)

            # 右侧控制按钮
            control_frame = ttk.Frame(title_frame)
            control_frame.pack(side=tk.RIGHT)

            ttk.Button(control_frame, text="全屏(F11)", command=toggle_fullscreen).pack(side=tk.RIGHT, padx=2)

            # 说明文字
            info_text = """此功能将两个单语字幕文件合并为一个双语字幕文件。
合并前会自动校验：
• 文件是否存在 • 文件编码兼容性检测 • 文件行数是否相同 • 字幕块数量是否匹配
• 逐块校验序号一致性 • 逐块校验时间轴匹配 • SRT格式完整性校验"""

            info_label = ttk.Label(main_frame, text=info_text, justify=tk.LEFT)
            info_label.pack(pady=(0, 15), anchor=tk.W)

            # 第一语言文件选择
            file1_frame = ttk.LabelFrame(main_frame, text="第一语言字幕文件")
            file1_frame.pack(fill=tk.X, pady=5)

            file1_var = tk.StringVar()
            file1_entry = ttk.Entry(file1_frame, textvariable=file1_var)
            file1_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)

            # 设置拖拽提示
            file1_entry.insert(0, "拖拽SRT文件到此处或点击浏览...")
            file1_entry.config(foreground='gray')

            def browse_file1():
                # 根据主界面输入路径定位初始目录
                initial_dir = os.path.dirname(self.input_path_var.get()) if self.input_path_var.get().strip() else None
                file_path = filedialog.askopenfilename(
                    title="选择第一语言字幕文件",
                    initialdir=initial_dir,
                    filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
                )
                if file_path:
                    file1_var.set(file_path)
                    file1_entry.config(foreground='black')

            ttk.Button(file1_frame, text="浏览...", command=browse_file1).pack(side=tk.RIGHT, padx=5, pady=5)

            # 文件1拖拽功能
            def on_file1_drop(event):
                file_path = event.data.strip('{}')
                if file_path.lower().endswith('.srt'):
                    file1_var.set(file_path)
                    file1_entry.config(foreground='black')
                else:
                    messagebox.showerror("错误", "请拖拽SRT字幕文件")

            def on_file1_focus_in(event):
                if file1_entry.get() == "拖拽SRT文件到此处或点击浏览..." and file1_entry.cget('foreground') == 'gray':
                    file1_entry.delete(0, tk.END)
                    file1_entry.config(foreground='black')

            def on_file1_focus_out(event):
                if not file1_entry.get():
                    file1_entry.insert(0, "拖拽SRT文件到此处或点击浏览...")
                    file1_entry.config(foreground='gray')

            file1_entry.bind("<FocusIn>", on_file1_focus_in)
            file1_entry.bind("<FocusOut>", on_file1_focus_out)

            # 设置拖拽
            try:
                from tkinterdnd2 import DND_FILES
                file1_entry.drop_target_register(DND_FILES)
                file1_entry.dnd_bind('<<Drop>>', on_file1_drop)
            except ImportError:
                pass  # 如果没有tkinterdnd2，跳过拖拽功能

            # 第二语言文件选择
            file2_frame = ttk.LabelFrame(main_frame, text="第二语言字幕文件")
            file2_frame.pack(fill=tk.X, pady=5)

            file2_var = tk.StringVar()
            file2_entry = ttk.Entry(file2_frame, textvariable=file2_var)
            file2_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)

            # 设置拖拽提示
            file2_entry.insert(0, "拖拽SRT文件到此处或点击浏览...")
            file2_entry.config(foreground='gray')

            def browse_file2():
                # 根据主界面输入路径定位初始目录
                initial_dir = os.path.dirname(self.input_path_var.get()) if self.input_path_var.get().strip() else None
                file_path = filedialog.askopenfilename(
                    title="选择第二语言字幕文件",
                    initialdir=initial_dir,
                    filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
                )
                if file_path:
                    file2_var.set(file_path)
                    file2_entry.config(foreground='black')

            ttk.Button(file2_frame, text="浏览...", command=browse_file2).pack(side=tk.RIGHT, padx=5, pady=5)

            # 文件2拖拽功能
            def on_file2_drop(event):
                file_path = event.data.strip('{}')
                if file_path.lower().endswith('.srt'):
                    file2_var.set(file_path)
                    file2_entry.config(foreground='black')
                else:
                    messagebox.showerror("错误", "请拖拽SRT字幕文件")

            def on_file2_focus_in(event):
                if file2_entry.get() == "拖拽SRT文件到此处或点击浏览..." and file2_entry.cget('foreground') == 'gray':
                    file2_entry.delete(0, tk.END)
                    file2_entry.config(foreground='black')

            def on_file2_focus_out(event):
                if not file2_entry.get():
                    file2_entry.insert(0, "拖拽SRT文件到此处或点击浏览...")
                    file2_entry.config(foreground='gray')

            file2_entry.bind("<FocusIn>", on_file2_focus_in)
            file2_entry.bind("<FocusOut>", on_file2_focus_out)

            # 设置拖拽
            try:
                from tkinterdnd2 import DND_FILES
                file2_entry.drop_target_register(DND_FILES)
                file2_entry.dnd_bind('<<Drop>>', on_file2_drop)
            except ImportError:
                pass  # 如果没有tkinterdnd2，跳过拖拽功能

            # 输出文件选择
            output_frame = ttk.LabelFrame(main_frame, text="输出文件")
            output_frame.pack(fill=tk.X, pady=5)

            # 输出文件输入框和按钮
            output_input_frame = ttk.Frame(output_frame)
            output_input_frame.pack(fill=tk.X, padx=5, pady=5)

            output_var = tk.StringVar()
            output_entry = ttk.Entry(output_input_frame, textvariable=output_var)
            output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

            def browse_output():
                # 智能选择初始目录：优先使用主界面输出路径，其次使用当前输出文件目录
                initial_dir = None
                current_output = output_var.get().strip()
                main_output_dir = self.output_path_var.get().strip()

                if current_output and os.path.dirname(current_output):
                    initial_dir = os.path.dirname(current_output)
                elif main_output_dir:
                    initial_dir = main_output_dir

                file_path = filedialog.asksaveasfilename(
                    title="保存合并后的字幕文件",
                    initialdir=initial_dir,
                    defaultextension=".srt",
                    filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
                )
                if file_path:
                    output_var.set(file_path)
                    log_message(f"✅ 已设置输出文件: {os.path.basename(file_path)}")

            def open_output_directory():
                """打开输出目录"""
                output_path = output_var.get().strip()
                if output_path:
                    output_dir = os.path.dirname(output_path)
                    if os.path.exists(output_dir):
                        try:
                            os.startfile(output_dir)
                        except Exception as e:
                            messagebox.showerror("错误", f"无法打开目录: {str(e)}")
                    else:
                        messagebox.showerror("错误", "输出目录不存在")
                else:
                    messagebox.showwarning("提示", "请先指定输出文件路径")

            # 按钮区域
            button_container = ttk.Frame(output_input_frame)
            button_container.pack(side=tk.RIGHT)

            ttk.Button(button_container, text="浏览...", command=browse_output).pack(side=tk.LEFT, padx=2)
            ttk.Button(button_container, text="打开输出目录", command=open_output_directory).pack(side=tk.LEFT, padx=2)

            # 自动设置选项
            auto_output_frame = ttk.Frame(output_frame)
            auto_output_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

            merge_auto_set_var = tk.BooleanVar(value=True)
            auto_output_checkbox = ttk.Checkbutton(
                auto_output_frame,
                text="自动设置为输入文件所在目录",
                variable=merge_auto_set_var
            )
            auto_output_checkbox.pack(side=tk.LEFT)

            # 添加提示标签
            auto_info_label = ttk.Label(auto_output_frame, text="💡 取消勾选可手动指定输出目录", foreground="#666666", font=("", 9))
            auto_info_label.pack(side=tk.RIGHT)

            # 自动设置输出文件名（智能选择输出目录）
            def auto_set_output(*args):
                # 检查是否启用自动设置
                if not merge_auto_set_var.get():
                    return

                file1 = file1_var.get().strip()
                file2 = file2_var.get().strip()

                # 清理提示文本
                if file1 == "拖拽SRT文件到此处或点击浏览...":
                    file1 = ""
                if file2 == "拖拽SRT文件到此处或点击浏览...":
                    file2 = ""

                if file1 and file2:
                    # 检查是否为同一文件
                    if os.path.abspath(file1) == os.path.abspath(file2):
                        log_message("⚠️ 警告：第一语言和第二语言选择了同一文件，无需合并")
                        return

                    # 智能选择输出目录：优先使用主界面输出目录，其次使用第一个文件的目录
                    output_dir = None
                    main_output_dir = self.output_path_var.get().strip()

                    if main_output_dir and os.path.exists(main_output_dir):
                        output_dir = main_output_dir
                        log_message(f"📁 使用主界面输出目录: {output_dir}")
                    else:
                        output_dir = os.path.dirname(file1)
                        log_message(f"📁 使用第一语言文件目录: {output_dir}")

                    name1 = os.path.splitext(os.path.basename(file1))[0]
                    name2 = os.path.splitext(os.path.basename(file2))[0]

                    # 生成输出文件名
                    output_name = f"{name1}_{name2}_merged.srt"
                    output_path = os.path.join(output_dir, output_name)

                    # 只有在输出路径为空或者启用自动设置时才更新
                    current_output = output_var.get().strip()
                    if not current_output or merge_auto_set_var.get():
                        output_var.set(output_path)
                        log_message(f"✅ 自动设置输出文件: {output_name}")

                        # 验证输出目录权限
                        if not os.access(output_dir, os.W_OK):
                            log_message(f"⚠️ 输出目录没有写入权限: {output_dir}")

            def on_merge_auto_setting_changed():
                """当合并窗口自动设置选项改变时的回调"""
                if merge_auto_set_var.get():
                    auto_set_output()
                    log_message("✅ 已启用自动设置输出文件路径")
                else:
                    log_message("📝 已禁用自动设置，可手动指定输出文件路径")

            # 绑定自动设置选项变化事件
            merge_auto_set_var.trace_add("write", lambda *args: on_merge_auto_setting_changed())

            file1_var.trace_add("write", auto_set_output)
            file2_var.trace_add("write", auto_set_output)

            # 开始合并按钮区域（移到输出文件下方）
            merge_button_frame = ttk.Frame(main_frame)
            merge_button_frame.pack(fill=tk.X, pady=10)

            # 定义start_merge函数（需要在按钮创建前定义）
            def start_merge():
                # 清理提示文本
                if file1_var.get().strip() == "拖拽SRT文件到此处或点击浏览...":
                    file1_var.set("")
                if file2_var.get().strip() == "拖拽SRT文件到此处或点击浏览...":
                    file2_var.set("")

                file1 = file1_var.get().strip()
                file2 = file2_var.get().strip()
                output = output_var.get().strip()

                if not file1:
                    messagebox.showerror("错误", "请选择第一语言字幕文件")
                    return
                if not file2:
                    messagebox.showerror("错误", "请选择第二语言字幕文件")
                    return
                if not output:
                    messagebox.showerror("错误", "请指定输出文件路径")
                    return

                # 增加同文件名合并校验
                if os.path.abspath(file1) == os.path.abspath(file2):
                    messagebox.showerror("错误", "第一语言和第二语言选择了同一文件，无需合并！\n\n请选择不同的字幕文件进行合并。")
                    return

                # 清空日志
                log_text.config(state=tk.NORMAL)
                log_text.delete(1.0, tk.END)
                log_text.config(state=tk.DISABLED)

                # 重置进度
                update_progress(0, "开始合并...")

                log_message("🔄 开始合并字幕文件...")
                log_message(f"📁 第一语言文件：{file1}")
                log_message(f"📁 第二语言文件：{file2}")
                log_message(f"💾 输出文件：{output}")
                log_message("=" * 60)

                try:
                    # 第一步：SRT格式校验（引入主界面校验逻辑）
                    update_progress(10, "校验第一语言文件格式...")
                    log_message("🔍 步骤1：校验第一语言文件SRT格式...")

                    try:
                        # 创建临时处理器实例，避免在主界面显示日志
                        temp_processor = SubtitleProcessor()
                        temp_processor.log_callback = None  # 禁用日志回调
                        blocks1, lang_type1 = temp_processor.validate_srt_structure(file1)
                        log_message(f"✅ 第一语言文件格式校验通过，检测到 {len(blocks1)} 个字幕块")
                    except Exception as e:
                        error_msg = f"❌ 第一语言文件SRT格式校验失败：{str(e)}"
                        log_message(error_msg)
                        update_progress(0, "校验失败")
                        messagebox.showerror("错误", error_msg)
                        return

                    update_progress(20, "校验第二语言文件格式...")
                    log_message("🔍 步骤2：校验第二语言文件SRT格式...")

                    try:
                        # 创建临时处理器实例，避免在主界面显示日志
                        temp_processor = SubtitleProcessor()
                        temp_processor.log_callback = None  # 禁用日志回调
                        blocks2, lang_type2 = temp_processor.validate_srt_structure(file2)
                        log_message(f"✅ 第二语言文件格式校验通过，检测到 {len(blocks2)} 个字幕块")
                    except Exception as e:
                        error_msg = f"❌ 第二语言文件SRT格式校验失败：{str(e)}"
                        log_message(error_msg)
                        update_progress(0, "校验失败")
                        messagebox.showerror("错误", error_msg)
                        return

                    # 第二步：兼容性校验
                    update_progress(40, "执行兼容性校验...")
                    log_message("🔍 步骤3：执行文件兼容性校验...")

                    is_valid, errors, warnings = self.processor.validate_subtitle_files_for_merge(file1, file2)

                    if warnings:
                        for warning in warnings:
                            log_message(f"⚠️ {warning}")

                    if not is_valid:
                        error_msg = "兼容性校验失败：\n" + "\n".join(errors)
                        log_message("❌ 兼容性校验失败：")
                        for error in errors:
                            log_message(f"   {error}")
                        update_progress(0, "校验失败")
                        messagebox.showerror("错误", error_msg)
                        return

                    log_message("✅ 兼容性校验通过，文件可以安全合并")

                    # 第三步：执行合并
                    update_progress(70, "执行文件合并...")
                    log_message("🔄 步骤4：执行文件合并...")

                    success, message = self.processor.merge_subtitle_files(file1, file2, output)

                    if success:
                        update_progress(100, "合并完成！")
                        log_message("✅ 字幕合并成功！")
                        log_message(f"💾 输出文件：{output}")
                        if warnings:
                            log_message("⚠️ 注意事项：")
                            for warning in warnings:
                                log_message(f"   {warning}")

                        # 显示成功消息（照搬主界面逻辑）
                        success_msg = "字幕合并完成！"
                        if warnings:
                            success_msg += f"\n\n注意：检测到 {len(warnings)} 个警告，请查看日志"
                        messagebox.showinfo("成功", success_msg)
                    else:
                        update_progress(0, "合并失败")
                        log_message(f"❌ 合并失败：{message}")
                        messagebox.showerror("错误", f"合并失败：{message}")

                except Exception as e:
                    error_msg = f"合并过程中发生未预期错误：{str(e)}"
                    log_message(f"❌ {error_msg}")
                    update_progress(0, "处理失败")
                    messagebox.showerror("错误", error_msg)

            # 创建开始合并按钮
            merge_btn = ttk.Button(merge_button_frame, text="开始合并", command=start_merge)
            merge_btn.pack(side=tk.LEFT, padx=5)

            # 进度条区域
            progress_frame = ttk.Frame(main_frame)
            progress_frame.pack(fill=tk.X, pady=5)

            ttk.Label(progress_frame, text="处理进度:").pack(side=tk.LEFT)
            progress_var = tk.DoubleVar()
            progress_bar = ttk.Progressbar(progress_frame, variable=progress_var, maximum=100)
            progress_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5)

            # 状态标签
            status_var = tk.StringVar(value="就绪")
            ttk.Label(main_frame, textvariable=status_var).pack(anchor=tk.W, pady=5)

            # 日志区域（照搬主界面逻辑）
            log_frame = ttk.LabelFrame(main_frame, text="处理日志")
            log_frame.pack(fill=tk.BOTH, expand=True, pady=10)

            log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=10)
            log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            log_scrollbar = ttk.Scrollbar(log_frame, command=log_text.yview)
            log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            log_text.config(yscrollcommand=log_scrollbar.set)

            def log_message(message):
                """日志消息显示（照搬主界面逻辑）"""
                log_text.config(state=tk.NORMAL)
                log_text.insert(tk.END, message + "\n")
                log_text.see(tk.END)
                log_text.config(state=tk.DISABLED)
                merge_window.update()

            def update_progress(value, message=None):
                """更新进度条和状态"""
                progress_var.set(value)
                if message:
                    status_var.set(message)
                merge_window.update()



        except Exception as e:
            messagebox.showerror("错误", f"打开合并窗口失败: {str(e)}")

    def open_output_dir(self):
        """打开输出目录"""
        try:
            output_dir = self.output_path_var.get()
            if not output_dir or not os.path.exists(output_dir):
                messagebox.showwarning("警告", "输出目录不存在")
                return
            
            # 根据操作系统打开目录
            if platform.system() == "Windows":
                os.startfile(output_dir)
            elif platform.system() == "Darwin":  # macOS
                subprocess.Popen(["open", output_dir])
            else:  # Linux
                subprocess.Popen(["xdg-open", output_dir])
        except Exception as e:
            self.log_message(f"打开输出目录错误: {str(e)}")
    
    def swap_subtitle_lines(self):
        """互换双语字幕行 - 只交换字幕内容行，保持序号和时间轴不变"""
        try:
            input_path = self.input_path_var.get()
            if not input_path or not os.path.exists(input_path):
                messagebox.showwarning("警告", "请先选择有效的输入文件")
                return

            # 读取原始文件内容（使用utf-8-sig处理BOM）
            with codecs.open(input_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                lines = f.readlines()

            # 创建临时文件存储处理结果
            temp_path = input_path + ".swap.tmp"

            with codecs.open(temp_path, 'w', encoding='utf-8', errors='ignore') as f_out:
                i = 0
                while i < len(lines):
                    # 跳过空行
                    if not lines[i].strip():
                        f_out.write(lines[i])
                        i += 1
                        continue

                    # 检查是否为序号行
                    if lines[i].strip().isdigit():
                        # 序号行
                        index_line = lines[i]
                        i += 1

                        # 时间轴行
                        if i >= len(lines):
                            f_out.write(index_line)
                            break
                        time_line = lines[i]
                        i += 1

                        # 第一行字幕
                        if i >= len(lines):
                            f_out.write(index_line)
                            f_out.write(time_line)
                            break
                        subtitle1 = lines[i]
                        i += 1

                        # 第二行字幕 - 修复逻辑
                        subtitle2 = ""
                        # 检查是否还有下一行，且不是空行，且不是数字（下一个字幕块的序号）
                        if (i < len(lines) and
                            lines[i].strip() and
                            not lines[i].strip().isdigit()):
                            subtitle2 = lines[i]
                            i += 1

                        # 写入序号行和时间轴行
                        f_out.write(index_line)
                        f_out.write(time_line)

                        # 交换两行字幕内容
                        if subtitle2:
                            # 确保第二行字幕有换行符
                            if not subtitle2.endswith('\n'):
                                subtitle2 += '\n'
                            f_out.write(subtitle2)
                        if subtitle1:
                            # 确保第一行字幕有换行符
                            if not subtitle1.endswith('\n'):
                                subtitle1 += '\n'
                            f_out.write(subtitle1)

                        # 处理字幕块后的空行
                        # 如果当前位置是空行，写入空行
                        if i < len(lines) and not lines[i].strip():
                            f_out.write(lines[i])
                            i += 1
                        # 如果下一行是数字（下一个字幕块的序号），添加空行分隔
                        elif i < len(lines) and lines[i].strip().isdigit():
                            f_out.write('\n')
                        # 如果已经到文件末尾，不需要添加额外的空行
                    else:
                        # 非序号行，直接写入
                        f_out.write(lines[i])
                        i += 1

            # 替换原文件
            os.replace(temp_path, input_path)
            self.log_message("双语字幕行已互换（只交换字幕内容，保持序号和时间轴不变）")
            messagebox.showinfo("成功", "双语字幕行已互换")

        except Exception as e:
            self.log_message(f"互换字幕行错误: {str(e)}")
            messagebox.showerror("错误", f"互换字幕行失败: {str(e)}")
    
    def start_processing(self):
        """开始处理字幕"""
        try:
            input_path = self.input_path_var.get()
            output_dir = self.output_path_var.get()
            
            if not input_path or not os.path.exists(input_path):
                messagebox.showwarning("警告", "请选择有效的输入文件")
                return
            
            if not output_dir:
                output_dir = os.path.dirname(input_path)
                self.output_path_var.set(output_dir)
            
            # 禁用处理按钮防止重复点击
            self.process_btn.config(state=tk.DISABLED)
            self.log_message("开始处理字幕...")
            
            # 在新线程中处理，避免界面冻结
            import threading
            thread = threading.Thread(
                target=self._processing_thread,
                args=(input_path, output_dir)
            )
            thread.daemon = True
            thread.start()
            
        except Exception as e:
            self.log_message(f"开始处理错误: {str(e)}")
            self.process_btn.config(state=tk.NORMAL)
    
    def _processing_thread(self, input_path, output_dir):
        """处理线程"""
        try:
            # 获取选择的导出格式
            export_format = self.export_format_var.get()

            # 获取注音模式
            phonetic_mode = self.phonetic_mode.get()

            # SubtitleProcessor 会输出详细的处理过程和文件列表
            result_path = self.processor.process_subtitles(input_path, output_dir, export_format, phonetic_mode)

            # 询问是否打开输出目录
            def show_completion_dialog():
                response = messagebox.askyesno(
                    "处理完成",
                    "字幕处理完成！\n\n是否打开输出文件夹查看结果？",
                    icon='info'
                )
                if response:  # 用户选择"是"
                    self.open_output_dir()

            self.root.after(0, show_completion_dialog)

        except Exception as e:
            # SubtitleProcessor 已经记录了详细错误，这里只显示消息框
            error_msg = f"处理失败: {str(e)}"
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
        finally:
            # 恢复按钮状态
            self.root.after(0, lambda: self.process_btn.config(state=tk.NORMAL))

    def setup_drag_drop(self):
        """设置拖拽功能"""
        if DRAG_DROP_AVAILABLE:
            try:
                # 为输入框设置拖拽支持
                self.input_entry.drop_target_register(DND_FILES)
                self.input_entry.dnd_bind('<<Drop>>', self.on_file_drop)

                # 添加拖拽提示
                self.input_entry.config(
                    state='normal'
                )
                # 设置提示文本（当输入框为空时显示）
                if not self.input_path_var.get():
                    self.input_entry.insert(0, "拖拽SRT文件到此处或点击浏览...")
                    self.input_entry.config(foreground='gray')

                # 绑定焦点事件来处理提示文本
                self.input_entry.bind('<FocusIn>', self.on_entry_focus_in)
                self.input_entry.bind('<FocusOut>', self.on_entry_focus_out)

                #self.log_message("拖拽功能已启用 - 可以直接拖拽SRT文件到输入框")
            except Exception as e:
                self.log_message(f"拖拽功能初始化失败: {str(e)}")
        #else:
            #self.log_message("拖拽功能不可用 - 请安装 tkinterdnd2 库以启用拖拽功能")

    def on_file_drop(self, event):
        """处理文件拖拽事件"""
        try:
            # 获取拖拽的文件路径
            # 处理包含空格的路径：如果路径被大括号包围，则直接使用；否则按换行符分割
            file_data = event.data.strip()

            if file_data.startswith('{') and file_data.endswith('}'):
                # 路径被大括号包围，直接移除大括号
                file_path = file_data.strip('{}')
            else:
                # 按换行符分割多个文件，取第一个
                files = file_data.split('\n')
                if files:
                    file_path = files[0].strip().strip('{}')
                else:
                    return

            if file_path:
                # 直接设置文件路径，不进行校验（校验统一在"开始处理"按钮中进行）
                self.input_path_var.set(file_path)
                self.log_message(f"已通过拖拽设置输入文件: {file_path}")

                # 清除提示文本样式
                self.input_entry.config(foreground='black')
        except Exception as e:
            self.log_message(f"处理拖拽文件时出错: {str(e)}")

    def on_entry_focus_in(self, event):
        """输入框获得焦点时的处理"""
        if self.input_entry.get() == "拖拽SRT文件到此处或点击浏览..." and self.input_entry.cget('foreground') == 'gray':
            self.input_entry.delete(0, tk.END)
            self.input_entry.config(foreground='black')

    def on_entry_focus_out(self, event):
        """输入框失去焦点时的处理"""
        if not self.input_entry.get():
            self.input_entry.insert(0, "拖拽SRT文件到此处或点击浏览...")
            self.input_entry.config(foreground='gray')

# 主函数
def main():
    try:
        # 程序启动时加载规则文件
        ReplacementRules.load_rules_from_file()

        # 根据是否支持拖拽来创建不同的根窗口
        if DRAG_DROP_AVAILABLE:
            root = TkinterDnD.Tk()
        else:
            root = tk.Tk()

        app = SubtitleToolGUI(root)
        root.mainloop()
    except Exception as e:
        print(f"程序运行错误: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()