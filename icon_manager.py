import os
import sys
import tkinter as tk
from tkinter import PhotoImage
import base64

class IconManager:
    """图标管理器 - 处理应用程序图标的加载和设置"""

    def __init__(self):
        self.icon_path = None
        self.icon_data = None
        self._load_icon()

    def get_resource_path(self, relative_path):
        """获取资源文件的绝对路径，支持开发环境和打包后的EXE"""
        try:
            # PyInstaller创建临时文件夹，并将路径存储在_MEIPASS中
            base_path = sys._MEIPASS
        except Exception:
            # 开发环境中使用当前目录
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    def _load_icon(self):
        """加载图标文件"""
        try:
            # 首先尝试从文件系统加载
            self.icon_path = self.get_resource_path("app_icon.ico")
            if os.path.exists(self.icon_path):
                return

            # 如果文件不存在，使用内嵌的base64图标数据
            self._create_default_icon()
        except Exception as e:
            print(f"图标加载失败: {e}")
            self._create_default_icon()

    def _create_default_icon(self):
        """创建默认图标（如果图标文件不存在）"""
        try:
            # 创建一个简单的默认图标
            import tempfile

            # 创建临时图标文件
            temp_dir = tempfile.gettempdir()
            self.icon_path = os.path.join(temp_dir, "app_default_icon.ico")

            # 如果临时图标不存在，创建一个
            if not os.path.exists(self.icon_path):
                self._generate_simple_icon()
        except Exception as e:
            print(f"创建默认图标失败: {e}")
            self.icon_path = None

    def _generate_simple_icon(self):
        """生成一个简单的图标文件"""
        try:
            # 使用PIL创建一个简单的图标
            try:
                from PIL import Image, ImageDraw

                # 创建32x32的图标
                img = Image.new('RGBA', (32, 32), (70, 130, 180, 255))  # 钢蓝色背景
                draw = ImageDraw.Draw(img)

                # 绘制一个简单的"字"字图标
                draw.rectangle([8, 8, 24, 24], fill=(255, 255, 255, 255))
                draw.rectangle([10, 10, 22, 22], fill=(70, 130, 180, 255))
                draw.rectangle([12, 14, 20, 16], fill=(255, 255, 255, 255))
                draw.rectangle([12, 18, 20, 20], fill=(255, 255, 255, 255))

                # 保存为ICO格式
                img.save(self.icon_path, format='ICO', sizes=[(32, 32)])

            except ImportError:
                # 如果PIL不可用，创建一个最基本的图标文件
                self._create_minimal_icon()

        except Exception as e:
            print(f"生成图标失败: {e}")
            self.icon_path = None

    def _create_minimal_icon(self):
        """创建最小化的图标文件（不依赖PIL）"""
        try:
            # 创建一个最基本的ICO文件头
            ico_data = b'\x00\x00\x01\x00\x01\x00\x20\x20\x00\x00\x01\x00\x08\x00\x68\x05\x00\x00\x16\x00\x00\x00'
            ico_data += b'\x28\x00\x00\x00\x20\x00\x00\x00\x40\x00\x00\x00\x01\x00\x08\x00\x00\x00\x00\x00'
            ico_data += b'\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00'

            # 添加调色板（256色）
            for i in range(256):
                ico_data += bytes([i, i, i, 0])  # 灰度调色板

            # 添加图像数据（32x32像素）
            for y in range(32):
                for x in range(32):
                    if 8 <= x <= 24 and 8 <= y <= 24:
                        ico_data += b'\xFF'  # 白色
                    else:
                        ico_data += b'\x46'  # 钢蓝色

            # 添加掩码数据
            ico_data += b'\x00' * 128  # 32x32位掩码

            with open(self.icon_path, 'wb') as f:
                f.write(ico_data)

        except Exception as e:
            print(f"创建最小图标失败: {e}")
            self.icon_path = None

    def set_window_icon(self, window):
        """为窗口设置图标"""
        try:
            if self.icon_path and os.path.exists(self.icon_path):
                window.iconbitmap(self.icon_path)
                return True
        except Exception as e:
            print(f"设置窗口图标失败: {e}")
        return False

    def get_icon_path(self):
        """获取图标文件路径"""
        return self.icon_path if self.icon_path and os.path.exists(self.icon_path) else None

# 全局图标管理器实例
_icon_manager = None

def get_icon_manager():
    """获取全局图标管理器实例"""
    global _icon_manager
    if _icon_manager is None:
        _icon_manager = IconManager()
    return _icon_manager

def set_window_icon(window):
    """为窗口设置图标的便捷函数"""
    return get_icon_manager().set_window_icon(window)

def get_icon_path():
    """获取图标路径的便捷函数"""
    return get_icon_manager().get_icon_path()