#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包配置脚本
使用PyInstaller打包日英中文注音工具
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# 自动切换到虚拟环境（如果当前不在虚拟环境中）
def ensure_venv():
    """确保在虚拟环境中运行"""
    base_dir = Path(__file__).parent
    venv_python = base_dir / ".venv" / "Scripts" / "python.exe"

    # 检查是否已经在虚拟环境中
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        # 已经在虚拟环境中
        return

    # 检查虚拟环境是否存在
    if venv_python.exists():
        print("检测到全局Python环境，自动切换到虚拟环境...")
        print(f"虚拟环境路径: {venv_python}")

        # 使用虚拟环境的Python重新运行当前脚本
        result = subprocess.run([str(venv_python)] + sys.argv, cwd=base_dir)
        sys.exit(result.returncode)
    else:
        print(f"[警告] 虚拟环境不存在: {venv_python}")
        print("[警告] 将使用当前Python环境，可能缺少某些依赖")

# 在导入其他模块前先确保虚拟环境
ensure_venv()

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

class BuildConfig:
    """打包配置管理器"""

    def __init__(self):
        self.app_name = "日英中文注音工具"
        self.version = "2.0.0"
        self.base_dir = Path(__file__).parent
        self.dist_dir = self.base_dir / "dist"
        self.build_dir = self.base_dir / "build"
        self.spec_file = self.base_dir / "phonetic_tool.spec"

    def get_dependency_paths(self):
        """自动检测依赖路径"""
        paths = {}

        # 检测 pykakasi 数据路径
        try:
            import pykakasi
            pykakasi_dir = Path(pykakasi.__file__).parent
            pykakasi_data = pykakasi_dir / "data"
            if pykakasi_data.exists():
                paths['pykakasi'] = str(pykakasi_data)
                print(f"[OK] pykakasi数据路径: {pykakasi_data}")
            else:
                print(f"[ERROR] pykakasi数据路径不存在: {pykakasi_data}")
        except ImportError:
            print("[ERROR] pykakasi未安装")

        # 检测 tkinterdnd2 路径
        try:
            import tkinterdnd2
            tkdnd_dir = Path(tkinterdnd2.__file__).parent / "tkdnd"
            if tkdnd_dir.exists():
                paths['tkinterdnd2'] = str(tkdnd_dir)
                print(f"[OK] tkinterdnd2路径: {tkdnd_dir}")
            else:
                print(f"[WARN] tkinterdnd2/tkdnd路径不存在: {tkdnd_dir}")
        except ImportError:
            print("[WARN] tkinterdnd2未安装")

        # 检测 pypinyin（中文拼音）
        try:
            import pypinyin
            print(f"[OK] pypinyin已安装: {pypinyin.__version__}")
            paths['pypinyin'] = True
        except ImportError:
            print("[WARN] pypinyin未安装，中文注音功能将不可用")

        # 检测数据库文件
        db_file = self.base_dir / "phonetic_accurate_db.sqlite"
        if db_file.exists():
            size_mb = db_file.stat().st_size / (1024 * 1024)
            paths['database'] = str(db_file)
            print(f"[OK] 英语音标数据库: {db_file.name} ({size_mb:.1f}MB)")
        else:
            print(f"[ERROR] 数据库文件不存在: {db_file}")

        return paths

    def create_spec_file(self, paths):
        """创建自定义的spec文件"""

        # 构建数据文件列表
        datas = []

        # pykakasi 数据
        if 'pykakasi' in paths:
            # 使用repr()来正确转义路径
            datas.append(f'({repr(paths["pykakasi"])}, "pykakasi/data")')

        # tkinterdnd2
        if 'tkinterdnd2' in paths:
            datas.append(f'({repr(paths["tkinterdnd2"])}, "tkinterdnd2/tkdnd")')

        # 数据库文件
        if 'database' in paths:
            datas.append(f'({repr(paths["database"])}, ".")')

        # 打包图标文件到根目录，确保自定义图标能正确加载
        icon_files = ['app_icon.ico', 'app_icon.png']
        for icon in icon_files:
            icon_path = self.base_dir / icon
            if icon_path.exists():
                datas.append(f'({repr(str(icon_path))}, ".")')

        # icon_manager.py 也需要打包
        icon_manager = self.base_dir / "icon_manager.py"
        if icon_manager.exists():
            datas.append(f'({repr(str(icon_manager))}, ".")')

        datas_str = ",\n        ".join(datas)

        spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['test_ui_test2_copy_6.py'],
    pathex=[],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports=[
        'pysrt',
        'chardet',
        'icon_manager',
        'tkinterdnd2',
        'tkinterdnd2.TkinterDnD',
        'pykakasi',
        'sudachipy',
        'sudachidict_core',
        'pypinyin',
        'sqlite3',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL.ImageQt',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'unittest',
        'doctest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 目录模式配置
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # 关键：使用目录模式
    name='{self.app_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico'
)

# 收集所有文件到目录
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='{self.app_name}'
)
'''

        with open(self.spec_file, 'w', encoding='utf-8') as f:
            f.write(spec_content)

        print(f"[OK] 已创建spec文件: {self.spec_file}")

    def check_dependencies(self):
        """检查打包依赖"""
        print("=" * 60)
        print("[SEARCH] 检查打包依赖...")
        print("=" * 60)

        # 检查PyInstaller
        try:
            import PyInstaller
            print(f"[OK] PyInstaller: {PyInstaller.__version__}")
        except ImportError:
            print("[ERROR] PyInstaller未安装，请运行: pip install pyinstaller")
            return False

        # 检查主程序
        main_file = self.base_dir / "test_ui_test2_copy_6.py"
        if main_file.exists():
            print(f"[OK] 主程序: {main_file.name}")
        else:
            print(f"[ERROR] 主程序不存在: {main_file}")
            return False

        # 检查图标文件
        icon_file = self.base_dir / "app_icon.ico"
        if icon_file.exists():
            print(f"[OK] 图标文件: {icon_file.name}")
        else:
            print(f"[WARN] 图标文件不存在: {icon_file.name}")

        return True

    def clean_build_files(self):
        """清理构建文件"""
        print("\n" + "=" * 60)
        print("🧹 清理旧的构建文件...")
        print("=" * 60)

        dirs_to_clean = [self.build_dir, self.dist_dir]
        files_to_clean = [self.spec_file]

        for dir_path in dirs_to_clean:
            if dir_path.exists():
                shutil.rmtree(dir_path)
                print(f"[TRASH] 已删除目录: {dir_path.name}")

        for file_path in files_to_clean:
            if file_path.exists():
                file_path.unlink()
                print(f"[TRASH] 已删除文件: {file_path.name}")

    def copy_data_files(self, paths):
        """手动复制数据文件到dist目录"""
        dist_app = self.dist_dir / self.app_name

        if not dist_app.exists():
            print(f"[ERROR] dist目录不存在: {dist_app}")
            return

        # 复制数据库文件
        if 'database' in paths:
            db_src = Path(paths['database'])
            db_dst = dist_app / db_src.name
            shutil.copy(db_src, db_dst)
            size_mb = db_dst.stat().st_size / (1024 * 1024)
            print(f"   [OK] 数据库: {db_src.name} ({size_mb:.1f}MB)")

        # 复制pykakasi数据
        if 'pykakasi' in paths:
            pykakasi_dst = dist_app / "pykakasi" / "data"
            pykakasi_dst.mkdir(parents=True, exist_ok=True)
            shutil.copytree(paths['pykakasi'], pykakasi_dst, dirs_exist_ok=True)
            file_count = len(list(pykakasi_dst.glob('*')))
            print(f"   [OK] pykakasi数据: {file_count} 个文件")

        # 手动复制 tkinterdnd2（PyInstaller hook 可能失败）
        if 'tkinterdnd2' in paths:
            try:
                # 复制整个 tkinterdnd2 包
                import tkinterdnd2
                tkdnd2_src = Path(tkinterdnd2.__file__).parent
                tkdnd2_dst = dist_app / "_internal" / "tkinterdnd2"

                if tkdnd2_dst.exists():
                    shutil.rmtree(tkdnd2_dst)

                shutil.copytree(tkdnd2_src, tkdnd2_dst, dirs_exist_ok=True)
                print(f"   [OK] tkinterdnd2: 拖拽功能库已复制")
            except Exception as e:
                print(f"   [WARN] tkinterdnd2复制失败: {e}")

        # 复制图标文件到根目录（确保PyInstaller正确打包）
        for icon_name in ['app_icon.ico', 'app_icon.png']:
            icon_src = self.base_dir / icon_name
            if icon_src.exists():
                icon_dst = dist_app / icon_name
                if not icon_dst.exists():  # 避免重复复制
                    shutil.copy(icon_src, icon_dst)
                    print(f"   [OK] 图标: {icon_name}")

    def build_application(self):
        """构建应用程序"""
        print("\n" + "=" * 60)
        print(f"🚀 开始构建 {self.app_name} v{self.version}")
        print("=" * 60)

        # 自动清理旧的构建文件
        self.clean_build_files()

        # 检查依赖
        if not self.check_dependencies():
            print("\n[ERROR] 依赖检查失败，无法继续构建")
            return False

        print("\n" + "=" * 60)
        print("[SEARCH] 检测依赖路径...")
        print("=" * 60)

        # 获取依赖路径
        paths = self.get_dependency_paths()

        # 创建spec文件
        print("\n" + "=" * 60)
        print("[PACKAGE] 创建打包配置...")
        print("=" * 60)
        self.create_spec_file(paths)

        try:
            # 使用spec文件构建（添加 -y 选项自动覆盖）
            cmd = ["pyinstaller", "-y", str(self.spec_file)]

            print("\n" + "=" * 60)
            print(f"[PACKAGE] 执行构建命令...")
            print("=" * 60)
            print(f"命令: {' '.join(cmd)}")

            # 执行构建（使用当前Python环境的pyinstaller）
            # 使用 python -m PyInstaller 而不是直接调用 pyinstaller 命令
            # 这样可以确保使用当前激活的Python环境
            venv_pyinstaller_cmd = [sys.executable, "-m", "PyInstaller", "-y", str(self.spec_file)]
            print(f"使用Python: {sys.executable}")
            print(f"命令: {' '.join(venv_pyinstaller_cmd)}")

            result = subprocess.run(venv_pyinstaller_cmd, cwd=self.base_dir)

            if result.returncode == 0:
                print("\n" + "=" * 60)
                print("[OK] 构建成功！")
                print("=" * 60)

                # 手动复制关键数据文件（PyInstaller有时不能正确打包）
                print("\n[PACKAGE] 复制关键数据文件...")
                self.copy_data_files(paths)

                # 显示构建信息
                self.show_build_info()
                return True
            else:
                print("\n[ERROR] 构建失败！")
                return False

        except Exception as e:
            print(f"\n[ERROR] 构建过程中出错: {e}")
            return False

    def show_build_info(self):
        """显示构建信息"""
        print("\n" + "=" * 60)
        print("📦 构建信息")
        print("=" * 60)
        print(f"应用名称: {self.app_name}")
        print(f"版本号: {self.version}")
        print(f"打包模式: 目录模式 (onedir)")

        # 检查输出文件
        exe_file = self.dist_dir / self.app_name / f"{self.app_name}.exe"
        if exe_file.exists():
            size_mb = exe_file.stat().st_size / (1024 * 1024)
            print(f"\n[FOLDER] 输出目录: {self.dist_dir / self.app_name}")
            print(f"[FILE] 可执行文件: {exe_file.name}")
            print(f"[CHART] EXE大小: {size_mb:.1f} MB")

            # 计算整个dist目录大小
            total_size = 0
            dist_path = self.dist_dir / self.app_name
            file_count = 0
            for item in dist_path.rglob('*'):
                if item.is_file():
                    total_size += item.stat().st_size
                    file_count += 1
            total_size_mb = total_size / (1024 * 1024)
            print(f"[CHART] 总体积: {total_size_mb:.1f} MB")
            print(f"[FILE] 文件数量: {file_count} 个")

            # 验证关键文件
            print("\n" + "=" * 60)
            print("🔍 验证关键文件:")
            print("=" * 60)

            # 检查数据库
            db_file = dist_path / "phonetic_accurate_db.sqlite"
            if db_file.exists():
                db_size = db_file.stat().st_size / (1024 * 1024)
                print(f"   ✅ 英语音标数据库: {db_size:.1f}MB")
            else:
                print(f"   ❌ 英语音标数据库: 未找到")

            # 检查pykakasi数据
            pykakasi_dir = dist_path / "pykakasi" / "data"
            if pykakasi_dir.exists():
                print(f"   ✅ pykakasi数据: {len(list(pykakasi_dir.glob('*')))} 个文件")
            else:
                print(f"   ❌ pykakasi数据: 未找到")

            # 检查tkinterdnd2
            tkdnd_dir = dist_path / "_internal" / "tkinterdnd2"
            if tkdnd_dir.exists() and (tkdnd_dir / "tkdnd").exists():
                print(f"   ✅ tkinterdnd2拖拽库: 已包含")
            else:
                print(f"   ⚠️ tkinterdnd2拖拽库: 未找到")

            # 检查图标文件
            icon_file = dist_path / "app_icon.ico"
            if icon_file.exists():
                print(f"   ✅ 图标文件: 已包含")
            else:
                print(f"   ⚠️ 图标文件: 未找到")
        else:
            print(f"\n[ERROR] 输出文件不存在: {exe_file}")
            return

        print("\n" + "=" * 60)
        print("💡 使用说明:")
        print("=" * 60)
        print(f"1. 分发目录: dist\\{self.app_name}\\")
        print(f"2. 运行程序: dist\\{self.app_name}\\{self.app_name}.exe")
        print("3. 可以压缩整个文件夹为ZIP分发")
        print("4. 用户解压后直接运行EXE即可")

        print("\n" + "=" * 60)
        print("📋 功能支持:")
        print("=" * 60)
        print("✅ 日语汉字注音 (pykakasi + sudachipy)")
        print("✅ 英语单词音标 (385,521条音标数据)")
        print("✅ 中文汉字拼音 (pypinyin)")
        print("✅ 文件拖拽功能 (tkinterdnd2)")
        print("✅ 自定义多音字规则")
        print("✅ 导出ASS/HTML格式")

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="日英中文注音工具打包脚本")
    parser.add_argument("--clean", action="store_true", help="仅清理构建文件")
    parser.add_argument("--check", action="store_true", help="仅检查依赖")

    args = parser.parse_args()

    builder = BuildConfig()

    if args.clean:
        builder.clean_build_files()
        return

    if args.check:
        builder.check_dependencies()
        builder.get_dependency_paths()
        return

    # 构建应用程序
    success = builder.build_application()

    if success:
        print("\n" + "=" * 60)
        print("🎉 构建完成！")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("💥 构建失败！")
        print("=" * 60)
        sys.exit(1)

if __name__ == "__main__":
    main()
