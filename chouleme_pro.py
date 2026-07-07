# ===================== 导入 =====================
import os
import re
import sys
import random
import threading
import configparser
import subprocess
from typing import Optional, Dict, List
from pathlib import Path
import json
import traceback
import tempfile
import shutil
import queue
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===================== CustomTkinter 现代化UI框架 =====================
import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk

# ===================== Windows高DPI适配 =====================
def set_high_dpi_awareness():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

# ===================== 全局异常捕获 =====================
def global_except_hook(exc_type, exc_value, exc_traceback):
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("启动失败", f"程序崩溃：\n{error_msg[:500]}")
    except Exception:
        print(error_msg)
    sys.exit(1)

# 时间工具（日志时间戳）
from datetime import datetime

# ===================== 常量定义 =====================
APP_TITLE = "抽了么 Pro"
APP_SUBTITLE = "批量视频抽帧 + 智能去重工具"
MIN_BATCH_COUNT = 3
MAX_BATCH_COUNT = 20
DEFAULT_BATCH_COUNT = 5
DEFAULT_CRF = 23
DEFAULT_PRESET = "fast"
CONFIG_FILE = "video_sampler_pro_config.ini"

CUT_BASE_FRAMES = 10
CUT_STEP_FRAMES = 5
FRAMES_PER_CUT_POINT = 1

FFMPEG_TIMEOUT = 600
ENCODER_TEST_TIMEOUT = 5
ENCODER_CACHE_TTL = 86400
DEFAULT_PARALLEL = 2
MAX_PARALLEL = 4

MIN_SEGMENT_DURATION = 0.5

DEDUP_MODE_NONE = "none"
DEDUP_MODE_BEFORE = "before"
DEDUP_MODE_AFTER = "after"

# ===================== 资源路径 =====================
def get_resource_path(relative_path: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# ===================== 硬件加速编码器映射 =====================
HARDWARE_ENCODERS = {
    "cpu": {"name": "CPU软件编码", "vcodec": "libx264"},
    "nvidia": {"name": "NVIDIA NVENC硬件加速", "vcodec": "h264_nvenc"},
    "intel": {"name": "Intel QSV硬件加速", "vcodec": "h264_qsv"},
    "amd": {"name": "AMD AMF硬件加速", "vcodec": "h264_amf"},
    "mac": {"name": "Apple VideoToolbox硬件加速", "vcodec": "h264_videotoolbox"}
}

ENCODER_PARAMS = {
    "cpu": {
        "preset": "veryfast",
        "extra_args": ["-crf", "23", "-threads", "0"],
    },
    "nvidia": {
        "preset": "p5",
        "extra_args": [
            "-rc", "vbr", "-cq", "23", "-b:v", "0",
            "-maxrate", "5M", "-bufsize", "10M",
            "-tune", "hq", "-rc-lookahead", "20",
        ],
    },
    "intel": {
        "preset": "medium",
        "extra_args": ["-global_quality", "23", "-look_ahead", "20"],
    },
    "amd": {
        "preset": "balanced",
        "extra_args": ["-rc", "vbr_peak", "-qp_i", "23", "-qp_p", "25", "-quality", "balanced"],
    },
    "mac": {
        "preset": "medium",
        "extra_args": ["-q:v", "65"],
    },
}

# ===================== 主处理器类（现代化UI） =====================
class BatchVideoSamplerPro:
    """批量视频抽帧 + 智能去重处理器 - 现代化UI版"""

    def __init__(self):
        set_high_dpi_awareness()
        sys.excepthook = global_except_hook

        # ===== CustomTkinter 初始化 =====
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        self.root = ctk.CTk()
        self.root.title(APP_TITLE)
        self.root.configure(fg_color="#0F172A")
        
        # 全屏启动
        try:
            self.root.state('zoomed')
        except tk.TclError:
            self.root.geometry("1280x820")
        self.root.minsize(1100, 720)

        self.temp_dir = tempfile.mkdtemp()
        self._extract_ffmpeg()

        # === 核心变量 ===
        self.input_videos: List[str] = []
        self.output_folder = tk.StringVar()
        self.file_prefix = tk.StringVar()
        self.batch_count = tk.IntVar(value=DEFAULT_BATCH_COUNT)
        self.processed_count = tk.IntVar(value=0)
        self.total_count = tk.IntVar(value=0)
        self.overwrite_enable = tk.BooleanVar(value=False)
        self.hardware_accel = tk.StringVar(value="cpu")
        self.accel_display = tk.StringVar(value="CPU软件编码")
        self.parallel_count = tk.IntVar(value=DEFAULT_PARALLEL)
        self.rule_preview = tk.StringVar()
        self.spin_count_var = tk.StringVar(value=str(DEFAULT_BATCH_COUNT))
        self.par_spin_var = tk.StringVar(value=str(DEFAULT_PARALLEL))

        # === 去重功能变量 ===
        self.dedup_mode = tk.StringVar(value=DEDUP_MODE_NONE)
        self.dedup_time_shift = tk.BooleanVar(value=True)
        self.dedup_md5_change = tk.BooleanVar(value=True)
        self.dedup_fps_tweak = tk.BooleanVar(value=False)
        self.dedup_brightness = tk.BooleanVar(value=False)
        self.dedup_hue = tk.BooleanVar(value=False)
        self.dedup_mirror = tk.BooleanVar(value=False)
        self.dedup_rgb_shift = tk.BooleanVar(value=False)
        self.dedup_mask = tk.BooleanVar(value=False)
        self.dedup_mask_value = tk.DoubleVar(value=0.03)

        # 线程控制
        self._stop_event = threading.Event()
        self._process_lock = threading.Lock()
        self._processes_lock = threading.Lock()
        self._running = False
        self._processes: List[subprocess.Popen] = []

        self._ui_queue: queue.Queue = queue.Queue()

        self.pre_analysis_data: Dict[str, Dict] = {}
        self.video_info_cache: Dict[str, Dict] = {}

        # 初始化流程
        self.config = configparser.ConfigParser()
        self.available_encoders = self.detect_hardware_encoder()
        self.valid_encoders = self.validate_encoders()
        self.load_config()
        self.create_ui()
        self.update_rule_preview()
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        self._poll_ui_queue()

        self.batch_count.trace_add('write', self.limit_batch_count)
        self.batch_count.trace_add('write', lambda *args: self.update_rule_preview())
        self.processed_count.trace_add('write', self.update_progress_label)
        self.total_count.trace_add('write', self.update_progress_label)

    # ===================== FFmpeg 提取 =====================
    def _extract_ffmpeg(self) -> bool:
        # 优先使用系统 PATH 中的 ffmpeg / ffprobe
        path_ffmpeg = shutil.which("ffmpeg")
        path_ffprobe = shutil.which("ffprobe")
        if path_ffmpeg and path_ffprobe:
            self.ffmpeg_path = path_ffmpeg
            self.ffprobe_path = path_ffprobe
            self.log("使用系统 PATH 中的 FFmpeg", "info")
            return True

        # 否则使用本地 ffmpeg/ 目录（需自行放置或通过 scripts/download_ffmpeg.py 下载）
        try:
            ffmpeg_source = get_resource_path("ffmpeg/ffmpeg.exe")
            ffprobe_source = get_resource_path("ffmpeg/ffprobe.exe")
            ffmpeg_target = os.path.join(self.temp_dir, "ffmpeg.exe")
            ffprobe_target = os.path.join(self.temp_dir, "ffprobe.exe")

            if not os.path.exists(ffmpeg_target):
                shutil.copy2(ffmpeg_source, ffmpeg_target)
            if not os.path.exists(ffprobe_target):
                shutil.copy2(ffprobe_source, ffprobe_target)

            self.ffmpeg_path = ffmpeg_target
            self.ffprobe_path = ffprobe_target
            return True
        except (IOError, OSError) as e:
            print(f"FFmpeg 释放失败：{str(e)}")
            return False


    def _poll_ui_queue(self):
        try:
            while True:
                task = self._ui_queue.get_nowait()
                task()
        except queue.Empty:
            pass
        self.root.after(50, self._poll_ui_queue)

    def _queue_ui_update(self, callback):
        self._ui_queue.put(callback)

    # ===================== 现代化UI创建 =====================
    def create_ui(self):
        # ===== 颜色方案（暗色主题，剪映风格） =====
        self.colors = {
            "bg_dark": "#0F172A",
            "bg_card": "#1E293B",
            "bg_card_hover": "#273548",
            "bg_input": "#0F172A",
            "bg_sidebar": "#162032",
            "primary": "#3B82F6",
            "primary_hover": "#2563EB",
            "primary_light": "#1E3A5F",
            "success": "#10B981",
            "success_hover": "#059669",
            "success_light": "#064E3B",
            "danger": "#EF4444",
            "danger_hover": "#DC2626",
            "danger_light": "#7F1D1D",
            "warning": "#F59E0B",
            "warning_hover": "#D97706",
            "warning_light": "#78350F",
            "accent_purple": "#8B5CF6",
            "accent_purple_hover": "#7C3AED",
            "accent_purple_light": "#2E1065",
            "accent_teal": "#14B8A6",
            "accent_teal_hover": "#0D9488",
            "text_primary": "#F1F5F9",
            "text_secondary": "#94A3B8",
            "text_muted": "#64748B",
            "border": "#334155",
            "border_light": "#1E293B",
            "divider": "#1E293B",
            "progress_bg": "#1E293B",
        }

        # ===== 顶部标题栏 =====
        top_bar = ctk.CTkFrame(self.root, fg_color="#1E293B", height=56, corner_radius=0)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        # 左侧标题区
        title_left = ctk.CTkFrame(top_bar, fg_color="transparent")
        title_left.pack(side=tk.LEFT, padx=20, pady=6)

        # Logo品牌标识
        brand_frame = ctk.CTkFrame(
            title_left, width=38, height=38,
            fg_color="#3B82F6", corner_radius=10
        )
        brand_frame.pack(side=tk.LEFT, padx=(0, 12))
        brand_frame.pack_propagate(False)
        ctk.CTkLabel(
            brand_frame, text="抽",
            font=ctk.CTkFont(family="微软雅黑", size=16, weight="bold"),
            text_color="white"
        ).place(relx=0.5, rely=0.5, anchor="center")

        # 标题文字
        title_info = ctk.CTkFrame(title_left, fg_color="transparent")
        title_info.pack(side=tk.LEFT)
        ctk.CTkLabel(
            title_info, text=APP_TITLE,
            font=ctk.CTkFont(family="微软雅黑", size=16, weight="bold"),
            text_color="#F1F5F9"
        ).pack(anchor=tk.W)
        ctk.CTkLabel(
            title_info, text=APP_SUBTITLE,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            text_color="#94A3B8"
        ).pack(anchor=tk.W)

        # 右侧版本标识
        title_right = ctk.CTkFrame(top_bar, fg_color="transparent")
        title_right.pack(side=tk.RIGHT, padx=20)

        status_text = "✓ 免费开源版"
        status_color = "#10B981"
        badge_bg = "#064E3B"

        self.status_badge = ctk.CTkButton(
            title_right, text=status_text,
            font=ctk.CTkFont(family="微软雅黑", size=10, weight="bold"),
            fg_color=badge_bg, hover_color=badge_bg,
            text_color=status_color, command=lambda: None,
            height=32, corner_radius=8, border_width=0
        )
        self.status_badge.pack(pady=8)


        # 分隔线
        ctk.CTkFrame(self.root, fg_color="#334155", height=1, corner_radius=0).pack(fill=tk.X)

        # ===== 主体区域 =====
        main_container = ctk.CTkFrame(self.root, fg_color="transparent")
        main_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # 使用grid布局：左侧面板 + 右侧面板
        main_container.grid_columnconfigure(0, weight=3, minsize=300)
        main_container.grid_columnconfigure(1, weight=4, minsize=380)
        main_container.grid_rowconfigure(0, weight=1)

        # ===== 左侧面板：视频列表 =====
        left_panel = ctk.CTkFrame(main_container, fg_color="#1E293B", corner_radius=12)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left_panel.grid_columnconfigure(0, weight=1)
        left_panel.grid_rowconfigure(1, weight=1)
        left_panel.grid_rowconfigure(2, weight=0)

        # 左侧标题
        left_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        left_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            left_header, text="📁 源视频列表",
            font=ctk.CTkFont(family="微软雅黑", size=12, weight="bold"),
            text_color="#F1F5F9"
        ).pack(side=tk.LEFT)
        ctk.CTkLabel(
            left_header, text=f"{len(self.input_videos)} 个视频",
            font=ctk.CTkFont(family="微软雅黑", size=10),
            text_color="#64748B"
        ).pack(side=tk.RIGHT)

        # 视频列表（使用 tk.Listbox 配合暗色主题）
        list_frame = ctk.CTkFrame(left_panel, fg_color="#0F172A", corner_radius=8)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        self.video_listbox = tk.Listbox(
            list_frame, font=("微软雅黑", 10), width=30, height=8,
            selectmode=tk.EXTENDED, bd=0, relief=tk.FLAT, highlightthickness=0,
            bg="#0F172A", fg="#E2E8F0",
            selectbackground="#3B82F6", selectforeground="#FFFFFF",
            activestyle="none", selectborderwidth=0
        )
        self.video_listbox.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        self.video_listbox.bind("<Double-1>", self.preview_selected_video)
        
        list_scroll = ctk.CTkScrollbar(list_frame, command=self.video_listbox.yview)
        list_scroll.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 4))
        self.video_listbox.config(yscrollcommand=list_scroll.set)

        # 视频操作按钮
        btn_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 12))
        btn_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkButton(
            btn_frame, text="+ 添加视频", command=self.select_input_videos,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#3B82F6", hover_color="#2563EB",
            height=32, corner_radius=6
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")

        ctk.CTkButton(
            btn_frame, text="删除选中", command=self.delete_selected_video,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#475569", hover_color="#334155",
            height=32, corner_radius=6
        ).grid(row=0, column=1, padx=2, sticky="ew")

        ctk.CTkButton(
            btn_frame, text="清空", command=self.clear_video_list,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#334155", hover_color="#1E293B",
            height=32, corner_radius=6
        ).grid(row=0, column=2, padx=2, sticky="ew")

        ctk.CTkButton(
            btn_frame, text="打开输出", command=self.open_output_folder,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#14B8A6", hover_color="#0D9488",
            height=32, corner_radius=6
        ).grid(row=0, column=3, padx=(4, 0), sticky="ew")

        # ===== 右侧面板：设置区（可滚动） =====
        right_panel = ctk.CTkScrollableFrame(
            main_container, fg_color="#1E293B", corner_radius=12,
            scrollbar_fg_color="#334155", scrollbar_button_color="#475569",
            scrollbar_button_hover_color="#64748B"
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_panel.grid_columnconfigure(0, weight=1)

        # ----- 抽帧设置卡片 -----
        batch_card = ctk.CTkFrame(right_panel, fg_color="#0F172A", corner_radius=10)
        batch_card.grid(row=0, column=0, sticky="ew", pady=(6, 8))
        batch_card.grid_columnconfigure(0, weight=1)

        # 卡片标题
        card_header = ctk.CTkFrame(batch_card, fg_color="transparent")
        card_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 8))
        ctk.CTkLabel(
            card_header, text="⚙ 抽帧设置",
            font=ctk.CTkFont(family="微软雅黑", size=12, weight="bold"),
            text_color="#60A5FA"
        ).pack(side=tk.LEFT)

        # 第一行：生成数量 + 编码加速
        row1 = ctk.CTkFrame(batch_card, fg_color="transparent")
        row1.grid(row=1, column=0, sticky="ew", padx=14, pady=3)
        row1.grid_columnconfigure(0, weight=1)
        row1.grid_columnconfigure(1, weight=1)

        col_l = ctk.CTkFrame(row1, fg_color="transparent")
        col_l.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(
            col_l, text="生成数量",
            font=ctk.CTkFont(family="微软雅黑", size=10),
            text_color="#94A3B8"
        ).pack(anchor=tk.W, pady=(0, 3))
        
        spin_row = ctk.CTkFrame(col_l, fg_color="transparent")
        spin_row.pack(fill=tk.X)
        
        self.spin_count_entry = ctk.CTkEntry(
            spin_row, textvariable=self.spin_count_var,
            font=ctk.CTkFont(family="微软雅黑", size=13),
            fg_color="#0F172A", border_color="#334155",
            width=60, height=34, justify="center"
        )
        self.spin_count_entry.pack(side=tk.LEFT, padx=(0, 6))
        
        # 加减按钮
        def spin_up():
            v = min(MAX_BATCH_COUNT, int(self.spin_count_var.get() or DEFAULT_BATCH_COUNT) + 1)
            self.spin_count_var.set(str(v))
            self.batch_count.set(v)
            self.update_rule_preview()
        def spin_down():
            v = max(MIN_BATCH_COUNT, int(self.spin_count_var.get() or DEFAULT_BATCH_COUNT) - 1)
            self.spin_count_var.set(str(v))
            self.batch_count.set(v)
            self.update_rule_preview()
            
        ctk.CTkButton(
            spin_row, text="−", command=spin_down,
            width=28, height=28, fg_color="#334155", hover_color="#475569",
            font=ctk.CTkFont(size=14), corner_radius=6
        ).pack(side=tk.LEFT, padx=1)
        ctk.CTkButton(
            spin_row, text="+", command=spin_up,
            width=28, height=28, fg_color="#334155", hover_color="#475569",
            font=ctk.CTkFont(size=14), corner_radius=6
        ).pack(side=tk.LEFT, padx=1)
        
        ctk.CTkLabel(
            spin_row, text=f"  {MIN_BATCH_COUNT}-{MAX_BATCH_COUNT}个",
            font=ctk.CTkFont(family="微软雅黑", size=9),
            text_color="#64748B"
        ).pack(side=tk.LEFT, padx=(6, 0))
        
        self.spin_count_var.trace_add('write', lambda *a: self._sync_spin_to_batch())

        col_r = ctk.CTkFrame(row1, fg_color="transparent")
        col_r.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(
            col_r, text="编码加速",
            font=ctk.CTkFont(family="微软雅黑", size=10),
            text_color="#94A3B8"
        ).pack(anchor=tk.W, pady=(0, 3))
        
        self.accel_name_map = {HARDWARE_ENCODERS[key]["name"]: key for key in self.valid_encoders}
        self.accel_key_to_name = {key: HARDWARE_ENCODERS[key]["name"] for key in self.valid_encoders}
        encoder_names = [HARDWARE_ENCODERS[key]["name"] for key in self.valid_encoders]
        
        self.accel_combo = ctk.CTkComboBox(
            col_r, values=encoder_names,
            font=ctk.CTkFont(family="微软雅黑", size=11),
            fg_color="#0F172A", border_color="#334155",
            button_color="#3B82F6", button_hover_color="#2563EB",
            dropdown_fg_color="#1E293B", dropdown_hover_color="#273548",
            height=34, command=self.on_accel_selected
        )
        self.accel_combo.pack(fill=tk.X)
        
        initial_name = self.accel_key_to_name.get(
            self.hardware_accel.get(),
            HARDWARE_ENCODERS[self.valid_encoders[0]]["name"] if self.valid_encoders else "CPU软件编码"
        )
        if initial_name in encoder_names:
            self.accel_combo.set(initial_name)

        # 并行数量
        row1b = ctk.CTkFrame(batch_card, fg_color="transparent")
        row1b.grid(row=2, column=0, sticky="ew", padx=14, pady=3)
        ctk.CTkLabel(
            row1b, text="并行数量",
            font=ctk.CTkFont(family="微软雅黑", size=10),
            text_color="#94A3B8"
        ).pack(anchor=tk.W, pady=(0, 3))
        
        par_row = ctk.CTkFrame(row1b, fg_color="transparent")
        par_row.pack(fill=tk.X)
        
        self.par_spin_entry = ctk.CTkEntry(
            par_row, textvariable=self.par_spin_var,
            font=ctk.CTkFont(family="微软雅黑", size=13),
            fg_color="#0F172A", border_color="#334155",
            width=50, height=34, justify="center"
        )
        self.par_spin_entry.pack(side=tk.LEFT, padx=(0, 6))
        
        def par_up():
            v = min(MAX_PARALLEL, int(self.par_spin_var.get() or DEFAULT_PARALLEL) + 1)
            self.par_spin_var.set(str(v))
            self.parallel_count.set(v)
        def par_down():
            v = max(1, int(self.par_spin_var.get() or DEFAULT_PARALLEL) - 1)
            self.par_spin_var.set(str(v))
            self.parallel_count.set(v)
            
        ctk.CTkButton(
            par_row, text="−", command=par_down,
            width=28, height=28, fg_color="#334155", hover_color="#475569",
            font=ctk.CTkFont(size=14), corner_radius=6
        ).pack(side=tk.LEFT, padx=1)
        ctk.CTkButton(
            par_row, text="+", command=par_up,
            width=28, height=28, fg_color="#334155", hover_color="#475569",
            font=ctk.CTkFont(size=14), corner_radius=6
        ).pack(side=tk.LEFT, padx=1)
        
        ctk.CTkLabel(
            par_row, text=f"  1-{MAX_PARALLEL}个任务同时处理",
            font=ctk.CTkFont(family="微软雅黑", size=9),
            text_color="#64748B"
        ).pack(side=tk.LEFT, padx=(6, 0))
        
        self.par_spin_var.trace_add('write', lambda *a: self._sync_par_spin())

        # 分隔线
        ctk.CTkFrame(batch_card, fg_color="#1E293B", height=1).grid(row=3, column=0, sticky="ew", padx=14, pady=8)

        # 输出文件夹
        row2 = ctk.CTkFrame(batch_card, fg_color="transparent")
        row2.grid(row=4, column=0, sticky="ew", padx=14, pady=3)
        ctk.CTkLabel(
            row2, text="输出文件夹",
            font=ctk.CTkFont(family="微软雅黑", size=10),
            text_color="#94A3B8"
        ).pack(anchor=tk.W, pady=(0, 3))
        
        out_row = ctk.CTkFrame(row2, fg_color="transparent")
        out_row.pack(fill=tk.X)
        self.out_entry = ctk.CTkEntry(
            out_row, textvariable=self.output_folder,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#0F172A", border_color="#334155",
            height=34, placeholder_text="选择输出文件夹..."
        )
        self.out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ctk.CTkButton(
            out_row, text="浏览...", command=self.select_output_folder,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#475569", hover_color="#334155",
            width=70, height=34, corner_radius=6
        ).pack(side=tk.LEFT)

        # 文件名前缀
        row3 = ctk.CTkFrame(batch_card, fg_color="transparent")
        row3.grid(row=5, column=0, sticky="ew", padx=14, pady=3)
        ctk.CTkLabel(
            row3, text="文件名前缀",
            font=ctk.CTkFont(family="微软雅黑", size=10),
            text_color="#94A3B8"
        ).pack(anchor=tk.W, pady=(0, 3))
        
        prefix_row = ctk.CTkFrame(row3, fg_color="transparent")
        prefix_row.pack(fill=tk.X)
        self.prefix_entry = ctk.CTkEntry(
            prefix_row, textvariable=self.file_prefix,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#0F172A", border_color="#334155",
            height=34, placeholder_text="留空则使用视频文件名"
        )
        self.prefix_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        self.overwrite_cb = ctk.CTkCheckBox(
            prefix_row, text="覆盖已有", variable=self.overwrite_enable,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#3B82F6", hover_color="#2563EB",
            border_color="#475569", checkmark_color="white",
            width=20, height=20
        )
        self.overwrite_cb.pack(side=tk.LEFT)

        # 抽帧规则预览
        rule_frame = ctk.CTkFrame(batch_card, fg_color="#1E3A5F", corner_radius=8)
        rule_frame.grid(row=6, column=0, sticky="ew", padx=14, pady=(10, 12))
        ctk.CTkLabel(
            rule_frame, text="📋 抽帧规则",
            font=ctk.CTkFont(family="微软雅黑", size=10, weight="bold"),
            text_color="#60A5FA"
        ).pack(side=tk.LEFT, padx=10, pady=8)
        ctk.CTkLabel(
            rule_frame, textvariable=self.rule_preview,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            text_color="#93C5FD", wraplength=380, anchor=tk.W
        ).pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True, pady=8)

        # ----- 去重设置卡片 -----
        dedup_card = ctk.CTkFrame(right_panel, fg_color="#0F172A", corner_radius=10)
        dedup_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        dedup_card.grid_columnconfigure(0, weight=1)

        dedup_header = ctk.CTkFrame(dedup_card, fg_color="transparent")
        dedup_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 8))
        ctk.CTkLabel(
            dedup_header, text="🔍 智能去重",
            font=ctk.CTkFont(family="微软雅黑", size=12, weight="bold"),
            text_color="#A78BFA"
        ).pack(side=tk.LEFT)

        # 去重模式选择
        mode_frame = ctk.CTkFrame(dedup_card, fg_color="transparent")
        mode_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))

        modes = [
            (DEDUP_MODE_NONE, "不去重（仅删帧）", "仅对视频进行删帧处理"),
            (DEDUP_MODE_BEFORE, "抽帧前去重", "先去重 → 再删帧"),
            (DEDUP_MODE_AFTER, "抽帧后去重", "先删帧 → 再去重"),
        ]

        self.dedup_mode_btns = {}
        for val, title, desc in modes:
            mode_opt = ctk.CTkFrame(mode_frame, fg_color="#0F172A", corner_radius=8)
            mode_opt.pack(fill=tk.X, pady=2)
            
            rb = ctk.CTkRadioButton(
                mode_opt, text="", variable=self.dedup_mode, value=val,
                fg_color="#8B5CF6", hover_color="#7C3AED",
                border_color="#475569", width=18, height=18,
                command=self._on_dedup_mode_change
            )
            rb.pack(side=tk.LEFT, padx=(10, 8), pady=8)
            
            ctk.CTkLabel(
                mode_opt, text=title,
                font=ctk.CTkFont(family="微软雅黑", size=10, weight="bold"),
                text_color="#E2E8F0"
            ).pack(side=tk.LEFT, pady=(8, 0))
            ctk.CTkLabel(
                mode_opt, text=desc,
                font=ctk.CTkFont(family="微软雅黑", size=8),
                text_color="#64748B"
            ).pack(side=tk.LEFT, padx=8, pady=(8, 0))

        # 去重选项内容（可折叠）
        self.dedup_content = ctk.CTkFrame(dedup_card, fg_color="transparent")
        self.dedup_content.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.dedup_content.grid_columnconfigure((0, 1, 2), weight=1)

        # 基础去重
        col_basic = ctk.CTkFrame(self.dedup_content, fg_color="#2E1065", corner_radius=8)
        col_basic.grid(row=0, column=0, sticky="nsew", padx=(0, 3), pady=4)
        ctk.CTkLabel(
            col_basic, text="基础",
            font=ctk.CTkFont(family="微软雅黑", size=10, weight="bold"),
            text_color="#A78BFA"
        ).pack(anchor=tk.W, padx=10, pady=(8, 4))
        ctk.CTkCheckBox(
            col_basic, text="时间偏移", variable=self.dedup_time_shift,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#8B5CF6", hover_color="#7C3AED",
            border_color="#6D28D9", checkmark_color="white",
            width=18, height=18
        ).pack(anchor=tk.W, padx=10, pady=2)
        ctk.CTkCheckBox(
            col_basic, text="MD5修改", variable=self.dedup_md5_change,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#8B5CF6", hover_color="#7C3AED",
            border_color="#6D28D9", checkmark_color="white",
            width=18, height=18
        ).pack(anchor=tk.W, padx=10, pady=(2, 8))

        # 微调去重
        col_tweak = ctk.CTkFrame(self.dedup_content, fg_color="#1E3A5F", corner_radius=8)
        col_tweak.grid(row=0, column=1, sticky="nsew", padx=3, pady=4)
        ctk.CTkLabel(
            col_tweak, text="微调",
            font=ctk.CTkFont(family="微软雅黑", size=10, weight="bold"),
            text_color="#60A5FA"
        ).pack(anchor=tk.W, padx=10, pady=(8, 4))
        ctk.CTkCheckBox(
            col_tweak, text="帧率微调", variable=self.dedup_fps_tweak,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#3B82F6", hover_color="#2563EB",
            border_color="#1D4ED8", checkmark_color="white",
            width=18, height=18
        ).pack(anchor=tk.W, padx=10, pady=2)
        ctk.CTkCheckBox(
            col_tweak, text="亮度/对比度", variable=self.dedup_brightness,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#3B82F6", hover_color="#2563EB",
            border_color="#1D4ED8", checkmark_color="white",
            width=18, height=18
        ).pack(anchor=tk.W, padx=10, pady=2)
        ctk.CTkCheckBox(
            col_tweak, text="色调偏移", variable=self.dedup_hue,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#3B82F6", hover_color="#2563EB",
            border_color="#1D4ED8", checkmark_color="white",
            width=18, height=18
        ).pack(anchor=tk.W, padx=10, pady=(2, 8))

        # 重度去重
        col_heavy = ctk.CTkFrame(self.dedup_content, fg_color="#78350F", corner_radius=8)
        col_heavy.grid(row=0, column=2, sticky="nsew", padx=(3, 0), pady=4)
        ctk.CTkLabel(
            col_heavy, text="重度",
            font=ctk.CTkFont(family="微软雅黑", size=10, weight="bold"),
            text_color="#FBBF24"
        ).pack(anchor=tk.W, padx=10, pady=(8, 4))
        ctk.CTkCheckBox(
            col_heavy, text="水平镜像", variable=self.dedup_mirror,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#F59E0B", hover_color="#D97706",
            border_color="#B45309", checkmark_color="white",
            width=18, height=18
        ).pack(anchor=tk.W, padx=10, pady=2)
        ctk.CTkCheckBox(
            col_heavy, text="RGB偏移", variable=self.dedup_rgb_shift,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#F59E0B", hover_color="#D97706",
            border_color="#B45309", checkmark_color="white",
            width=18, height=18
        ).pack(anchor=tk.W, padx=10, pady=2)
        
        mask_row = ctk.CTkFrame(col_heavy, fg_color="transparent")
        mask_row.pack(anchor=tk.W, padx=10, pady=(2, 8))
        self.mask_check = ctk.CTkCheckBox(
            mask_row, text="蒙版", variable=self.dedup_mask,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#F59E0B", hover_color="#D97706",
            border_color="#B45309", checkmark_color="white",
            width=18, height=18,
            command=self._update_dedup_widgets
        )
        self.mask_check.pack(side=tk.LEFT)
        self.mask_entry = ctk.CTkEntry(
            mask_row, textvariable=self.dedup_mask_value,
            font=ctk.CTkFont(family="微软雅黑", size=9),
            fg_color="#0F172A", border_color="#334155",
            width=55, height=26
        )
        self.mask_entry.pack(side=tk.LEFT, padx=6)

        self._on_dedup_mode_change()

        # ===== 底部操作栏 =====
        bottom_bar = ctk.CTkFrame(self.root, fg_color="#1E293B", height=60, corner_radius=0)
        bottom_bar.pack(fill=tk.X, padx=0, pady=(0, 0))
        bottom_bar.pack_propagate(False)

        inner_bottom = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        inner_bottom.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # 左侧按钮组
        btn_group = ctk.CTkFrame(inner_bottom, fg_color="transparent")
        btn_group.pack(side=tk.LEFT)

        self.start_btn = ctk.CTkButton(
            btn_group, text="▶  开始处理", command=self.start_batch_process,
            font=ctk.CTkFont(family="微软雅黑", size=12, weight="bold"),
            fg_color="#10B981", hover_color="#059669",
            height=40, corner_radius=8, width=130
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_group, text="■  停止", command=self.stop_batch_process,
            font=ctk.CTkFont(family="微软雅黑", size=12, weight="bold"),
            fg_color="#EF4444", hover_color="#DC2626",
            height=40, corner_radius=8, width=100, state="disabled"
        )
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        self.clear_log_btn = ctk.CTkButton(
            btn_group, text="清空日志", command=self.clear_log,
            font=ctk.CTkFont(family="微软雅黑", size=10),
            fg_color="#334155", hover_color="#475569",
            height=40, corner_radius=8, width=90
        )
        self.clear_log_btn.pack(side=tk.LEFT, padx=8)

        # 右侧进度区
        progress_group = ctk.CTkFrame(inner_bottom, fg_color="transparent")
        progress_group.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))

        self.progress_label = ctk.CTkLabel(
            progress_group, text="0/0 (0%)",
            font=ctk.CTkFont(family="微软雅黑", size=12, weight="bold"),
            text_color="#F1F5F9"
        )
        self.progress_label.pack(side=tk.RIGHT, padx=(0, 12))

        self.progress_bar = ctk.CTkProgressBar(
            progress_group, fg_color="#1E293B", progress_color="#3B82F6",
            height=14, corner_radius=7
        )
        self.progress_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        self.progress_bar.set(0)

        # ===== 日志区 =====
        log_outer = ctk.CTkFrame(self.root, fg_color="#1E293B", corner_radius=12)
        log_outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 12))

        log_header = ctk.CTkFrame(log_outer, fg_color="transparent")
        log_header.pack(fill=tk.X, padx=14, pady=(10, 4))
        ctk.CTkLabel(
            log_header, text="📝 处理日志",
            font=ctk.CTkFont(family="微软雅黑", size=12, weight="bold"),
            text_color="#94A3B8"
        ).pack(side=tk.LEFT)

        log_text_frame = ctk.CTkFrame(log_outer, fg_color="#0F172A", corner_radius=8)
        log_text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.log_text = tk.Text(
            log_text_frame, font=("Consolas", 10), wrap=tk.WORD, state=tk.DISABLED,
            height=6, bd=0, relief=tk.FLAT, highlightthickness=0,
            bg="#0F172A", fg="#E2E8F0",
            padx=12, pady=8, spacing1=2, spacing3=2
        )
        self.log_text.pack(side=tk.LEFT, padx=(6, 0), pady=6, fill=tk.BOTH, expand=True)

        log_scroll = ctk.CTkScrollbar(log_text_frame, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=6, padx=(0, 4))
        self.log_text.config(yscrollcommand=log_scroll.set)

        # 日志颜色标签
        self.log_text.tag_configure("info", foreground="#E2E8F0")
        self.log_text.tag_configure("success", foreground="#10B981")
        self.log_text.tag_configure("warning", foreground="#F59E0B")
        self.log_text.tag_configure("error", foreground="#EF4444")
        self.log_text.tag_configure("dedup", foreground="#A78BFA")

        self.update_video_listbox()
        self.log("程序已启动，等待用户操作...", "success")
        if self.valid_encoders and len(self.valid_encoders) > 1:
            self.log(f"检测到可用硬件加速：{HARDWARE_ENCODERS[self.valid_encoders[1]]['name']}", "success")

    # ===================== Spin 同步 =====================
    def _sync_spin_to_batch(self):
        try:
            v = int(self.spin_count_var.get())
            v = max(MIN_BATCH_COUNT, min(MAX_BATCH_COUNT, v))
            self.batch_count.set(v)
        except ValueError:
            pass

    def _sync_par_spin(self):
        try:
            v = int(self.par_spin_var.get())
            v = max(1, min(MAX_PARALLEL, v))
            self.parallel_count.set(v)
        except ValueError:
            pass

    # ===================== 去重UI辅助 =====================
    def _on_dedup_mode_change(self):
        mode = self.dedup_mode.get()
        if mode == DEDUP_MODE_NONE:
            self.dedup_content.grid_remove()
        else:
            self.dedup_content.grid()

    def _update_dedup_widgets(self):
        if self.dedup_mask.get():
            self.mask_entry.configure(state="normal")
        else:
            self.mask_entry.configure(state="disabled")

    # ===================== 基础辅助功能 =====================
    def validate_spinbox(self, value: str) -> bool:
        if not value:
            return True
        try:
            ival = int(value)
            return MIN_BATCH_COUNT <= ival <= MAX_BATCH_COUNT
        except ValueError:
            return False

    def update_rule_preview(self):
        try:
            count = self.batch_count.get()
            short_samples = [2 + 2 * i for i in range(min(3, count))]
            mid_samples = [5 + 3 * i for i in range(min(3, count))]
            long_samples = [CUT_BASE_FRAMES + CUT_STEP_FRAMES * i for i in range(min(3, count))]

            preview_parts = []
            if short_samples:
                preview_parts.append(f"短视频＜60s: {', '.join(str(s) for s in short_samples)}" +
                                   (f"…({count}条)" if count > 3 else f"({count}条)"))
            if mid_samples:
                preview_parts.append(f"中等60~120s: {', '.join(str(s) for s in mid_samples)}" +
                                   (f"…({count}条)" if count > 3 else f"({count}条)"))
            if long_samples:
                preview_parts.append(f"长视频≥120s: {', '.join(str(s) for s in long_samples)}" +
                                   (f"…({count}条)" if count > 3 else f"({count}条)"))

            self.rule_preview.set(" | ".join(preview_parts))
        except tk.TclError:
            self.rule_preview.set("规则加载失败")

    def on_accel_selected(self, choice):
        selected_key = self.accel_name_map.get(choice, "cpu")
        self.hardware_accel.set(selected_key)
        self.save_config()
        self.log(f"已选择编码方式：{HARDWARE_ENCODERS[selected_key]['name']}", "info")

    def preview_selected_video(self, event=None):
        selected_indices = self.video_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("提示", "请先选中要预览的视频")
            return
        idx = selected_indices[0]
        video_path = self.input_videos[idx]
        if os.path.exists(video_path):
            try:
                os.startfile(video_path)
            except OSError as e:
                self.log(f"打开视频失败：{str(e)[:100]}", "warning")

    def log(self, message: str, tag: str = "info"):
        def _update_log():
            try:
                self.log_text.config(state=tk.NORMAL)
                timestamp = datetime.now().strftime("[%H:%M:%S]")
                self.log_text.insert(tk.END, f"{timestamp} {message}\n", tag)
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
            except tk.TclError:
                pass

        if hasattr(self, 'log_text') and self.log_text.winfo_exists():
            self._queue_ui_update(_update_log)
        else:
            print(message)

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.log("日志已清空", "success")

    def update_progress_label(self, *args):
        def _update():
            processed = self.processed_count.get()
            total = self.total_count.get()
            if total > 0:
                percent = int((processed / total) * 100)
                self.progress_label.configure(text=f"{processed}/{total} ({percent}%)")
                self.progress_bar.set(processed / total)
            else:
                self.progress_label.configure(text="0/0 (0%)")
                self.progress_bar.set(0)
        self._queue_ui_update(_update)

    def limit_batch_count(self, *args):
        try:
            count = self.batch_count.get()
            if count < MIN_BATCH_COUNT:
                self.batch_count.set(MIN_BATCH_COUNT)
                self.spin_count_var.set(str(MIN_BATCH_COUNT))
            elif count > MAX_BATCH_COUNT:
                self.batch_count.set(MAX_BATCH_COUNT)
                self.spin_count_var.set(str(MAX_BATCH_COUNT))
            else:
                self.spin_count_var.set(str(count))
        except (tk.TclError, ValueError):
            self.batch_count.set(DEFAULT_BATCH_COUNT)
            self.spin_count_var.set(str(DEFAULT_BATCH_COUNT))

    def select_input_videos(self):
        file_paths = filedialog.askopenfilenames(
            title="选择要批量处理的视频",
            filetypes=[
                ("视频文件", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.mpeg *.mpg *.m4v *.webm"),
                ("所有文件", "*.*")
            ]
        )
        if file_paths:
            new_videos = []
            for fp in file_paths:
                path = Path(fp)
                if fp not in self.input_videos and path.exists() and path.is_file():
                    new_videos.append(fp)

            if not new_videos:
                self.log("未添加新视频（文件已存在或无效）", "warning")
                return

            self.input_videos.extend(new_videos)
            self.update_video_listbox()

            if not self.output_folder.get():
                first_video_dir = os.path.dirname(new_videos[0])
                output_path = os.path.join(first_video_dir, "批量处理结果")
                self.output_folder.set(output_path)

            if not self.file_prefix.get():
                first_video_name = os.path.splitext(os.path.basename(new_videos[0]))[0]
                self.file_prefix.set(first_video_name)

            self.log(f"已添加{len(new_videos)}个视频，当前列表共{len(self.input_videos)}个视频", "success")
            self.save_config()

    def delete_selected_video(self):
        selected_indices = self.video_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("提示", "请先选中要删除的视频")
            return

        deleted_count = 0
        for idx in sorted(selected_indices, reverse=True):
            if 0 <= idx < len(self.input_videos):
                del self.input_videos[idx]
                deleted_count += 1

        self.update_video_listbox()
        self.log(f"已删除{deleted_count}个视频，当前列表共{len(self.input_videos)}个视频", "success")
        self.save_config()

    def clear_video_list(self):
        if not self.input_videos:
            messagebox.showinfo("提示", "视频列表已为空")
            return

        if messagebox.askyesno("确认", "是否清空所有视频？"):
            self.input_videos.clear()
            self.update_video_listbox()
            self.log("已清空所有视频", "success")
            self.save_config()

    def update_video_listbox(self):
        self.video_listbox.delete(0, tk.END)
        for idx, video_path in enumerate(self.input_videos):
            try:
                path = Path(video_path)
                name = path.name
                self.video_listbox.insert(tk.END, f"{idx+1:2d}. {name}")
            except OSError:
                self.video_listbox.insert(tk.END, video_path)

    def select_output_folder(self):
        folder_path = filedialog.askdirectory(title="选择批量输出根文件夹")
        if folder_path:
            self.output_folder.set(folder_path)
            self.save_config()
            self.log(f"输出文件夹已设置为：{folder_path}", "info")

    def open_output_folder(self):
        output_path = self.output_folder.get()
        if not output_path:
            messagebox.showwarning("提示", "请先设置输出文件夹！")
            return

        path = Path(output_path)
        if path.exists() and path.is_dir():
            try:
                if sys.platform == "win32":
                    os.startfile(output_path)
                else:
                    subprocess.run(["xdg-open", output_path], check=True)
            except (OSError, subprocess.SubprocessError) as e:
                self.log(f"打开文件夹失败：{str(e)[:100]}", "warning")
        else:
            messagebox.showwarning("提示", "输出文件夹不存在！")

    def on_window_close(self):
        if self._running:
            if not messagebox.askyesno("提示", "正在处理视频，是否确认退出？"):
                return
            self.stop_batch_process()

        self.terminate_all_processes()
        self.save_config()
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except OSError:
            pass
        self.root.destroy()

    def terminate_all_processes(self):
        with self._processes_lock:
            for proc in self._processes:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            self._processes.clear()

    def set_widgets_state(self, state: str):
        widgets_ctk = [
            self.spin_count_entry, self.par_spin_entry, self.prefix_entry,
            self.accel_combo, self.start_btn, self.clear_log_btn,
            self.out_entry
        ]
        for widget in widgets_ctk:
            try:
                if hasattr(widget, 'configure'):
                    widget.configure(state=state)
            except Exception:
                pass
        try:
            self.video_listbox.config(state=state)
        except tk.TclError:
            pass

    # ===================== 以下所有业务逻辑方法保持不变 =====================
    # 配置加载、编码器检测、视频预解析、抽帧算法、去重处理、FFmpeg调用等
    # 全部从原文件复制，一字不改

    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                self.config.read(CONFIG_FILE, encoding="utf-8")
                if "History" in self.config:
                    video_str = self.config["History"].get("input_videos", "")
                    if video_str:
                        self.input_videos = [
                            v for v in video_str.split("|")
                            if os.path.exists(v) and Path(v).is_file()
                        ]
                    self.output_folder.set(self.config["History"].get("output_folder", ""))
                    self.file_prefix.set(self.config["History"].get("file_prefix", ""))

                    try:
                        batch_count = self.config["History"].getint("batch_count", DEFAULT_BATCH_COUNT)
                        self.batch_count.set(max(MIN_BATCH_COUNT, min(MAX_BATCH_COUNT, batch_count)))
                        self.spin_count_var.set(str(self.batch_count.get()))
                    except (ValueError, TypeError):
                        self.batch_count.set(DEFAULT_BATCH_COUNT)
                        self.spin_count_var.set(str(DEFAULT_BATCH_COUNT))

                    self.overwrite_enable.set(self.config["History"].getboolean("overwrite_enable", False))

                    try:
                        parallel = self.config["History"].getint("parallel_count", DEFAULT_PARALLEL)
                        self.parallel_count.set(max(1, min(MAX_PARALLEL, parallel)))
                        self.par_spin_var.set(str(self.parallel_count.get()))
                    except (ValueError, TypeError):
                        self.parallel_count.set(DEFAULT_PARALLEL)
                        self.par_spin_var.set(str(DEFAULT_PARALLEL))

                if "Settings" in self.config:
                    accel = self.config["Settings"].get("hardware_accel", "cpu")
                    if accel in self.available_encoders:
                        self.hardware_accel.set(accel)

                if "DedupSettings" in self.config:
                    ds = self.config["DedupSettings"]
                    self.dedup_mode.set(ds.get("dedup_mode", DEDUP_MODE_NONE))
                    self.dedup_time_shift.set(ds.getboolean("dedup_time_shift", True))
                    self.dedup_md5_change.set(ds.getboolean("dedup_md5_change", True))
                    self.dedup_fps_tweak.set(ds.getboolean("dedup_fps_tweak", False))
                    self.dedup_brightness.set(ds.getboolean("dedup_brightness", False))
                    self.dedup_hue.set(ds.getboolean("dedup_hue", False))
                    self.dedup_mirror.set(ds.getboolean("dedup_mirror", False))
                    self.dedup_rgb_shift.set(ds.getboolean("dedup_rgb_shift", False))
                    self.dedup_mask.set(ds.getboolean("dedup_mask", False))
                    self.dedup_mask_value.set(ds.getfloat("dedup_mask_value", 0.03))

        except configparser.Error as e:
            print(f"配置文件加载失败：{str(e)[:100]}")
            self.input_videos = []

    def save_config(self):
        try:
            if "History" not in self.config:
                self.config["History"] = {}
            if "Settings" not in self.config:
                self.config["Settings"] = {}
            if "DedupSettings" not in self.config:
                self.config["DedupSettings"] = {}

            self.config["History"] = {
                "input_videos": "|".join(self.input_videos),
                "output_folder": self.output_folder.get(),
                "file_prefix": self.file_prefix.get(),
                "batch_count": str(self.batch_count.get()),
                "overwrite_enable": str(self.overwrite_enable.get()),
                "parallel_count": str(self.parallel_count.get())
            }

            self.config["Settings"]["hardware_accel"] = self.hardware_accel.get()

            self.config["DedupSettings"] = {
                "dedup_mode": self.dedup_mode.get(),
                "dedup_time_shift": str(self.dedup_time_shift.get()),
                "dedup_md5_change": str(self.dedup_md5_change.get()),
                "dedup_fps_tweak": str(self.dedup_fps_tweak.get()),
                "dedup_brightness": str(self.dedup_brightness.get()),
                "dedup_hue": str(self.dedup_hue.get()),
                "dedup_mirror": str(self.dedup_mirror.get()),
                "dedup_rgb_shift": str(self.dedup_rgb_shift.get()),
                "dedup_mask": str(self.dedup_mask.get()),
                "dedup_mask_value": str(self.dedup_mask_value.get()),
            }

            Path(CONFIG_FILE).parent.mkdir(exist_ok=True)

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                self.config.write(f)
        except (IOError, configparser.Error) as e:
            self.log(f"配置保存失败：{str(e)[:100]}", "warning")

    def _load_encoder_cache(self) -> Optional[List[str]]:
        try:
            if os.path.exists(CONFIG_FILE):
                cache_config = configparser.ConfigParser()
                cache_config.read(CONFIG_FILE, encoding="utf-8")
                if "EncoderCache" in cache_config:
                    cache_time = cache_config["EncoderCache"].getint("timestamp", 0)
                    if time.time() - cache_time < ENCODER_CACHE_TTL:
                        encoders = cache_config["EncoderCache"].get("available", "")
                        if encoders:
                            return encoders.split(",")
        except (configparser.Error, ValueError, OSError):
            pass
        return None

    def _save_encoder_cache(self, available: List[str]):
        try:
            if not os.path.exists(CONFIG_FILE):
                return
            cache_config = configparser.ConfigParser()
            cache_config.read(CONFIG_FILE, encoding="utf-8")
            if "EncoderCache" not in cache_config:
                cache_config["EncoderCache"] = {}
            cache_config["EncoderCache"]["timestamp"] = str(int(time.time()))
            cache_config["EncoderCache"]["available"] = ",".join(available)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                cache_config.write(f)
        except (configparser.Error, IOError):
            pass

    def detect_hardware_encoder(self) -> List[str]:
        cached = self._load_encoder_cache()
        if cached is not None:
            return cached

        if not Path(self.ffmpeg_path).exists():
            return ["cpu"]

        available = ["cpu"]
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        encoders_to_test = {
            "nvidia": "h264_nvenc",
            "intel": "h264_qsv",
            "amd": "h264_amf",
            "mac": "h264_videotoolbox"
        }

        for key, codec in encoders_to_test.items():
            try:
                cmd = [
                    self.ffmpeg_path, "-y", "-hide_banner", "-v", "error",
                    "-f", "lavfi", "-i", "color=size=640x480:duration=0.1:rate=10",
                    "-c:v", codec, "-f", "null", "-"
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8",
                    timeout=ENCODER_TEST_TIMEOUT,
                    startupinfo=startupinfo,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                if result.returncode == 0:
                    available.append(key)
            except (subprocess.SubprocessError, OSError):
                continue

        self._save_encoder_cache(available)
        return available

    def validate_encoders(self) -> List[str]:
        return [key for key in self.available_encoders if key in HARDWARE_ENCODERS]

    def check_ffmpeg_available(self) -> bool:
        if not Path(self.ffmpeg_path).exists():
            return False
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"], capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def pre_analyse_video(self, video_path: str) -> bool:
        if video_path in self.pre_analysis_data:
            return True

        self.log(f"正在预解析视频：{os.path.basename(video_path)}", "info")
        try:
            video_info = {"video_path": video_path}

            if Path(self.ffprobe_path).exists():
                try:
                    cmd = [
                        self.ffprobe_path, "-v", "quiet", "-print_format", "json",
                        "-show_streams", "-show_format", video_path
                    ]
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, encoding="utf-8",
                        check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    )
                    probe_data = json.loads(result.stdout)

                    video_stream = next(s for s in probe_data["streams"] if s["codec_type"] == "video")
                    audio_stream = next((s for s in probe_data["streams"] if s["codec_type"] == "audio"), None)

                    fps_str = video_stream.get("avg_frame_rate", "30/1")
                    if "/" in fps_str:
                        num, den = fps_str.split("/")
                        video_info["real_fps"] = float(num) / float(den) if den != "0" else 30.0
                    else:
                        video_info["real_fps"] = float(fps_str) if fps_str else 30.0

                    video_info["total_duration"] = float(probe_data["format"].get("duration", 0))
                    video_info["total_frames"] = int(video_stream.get("nb_frames",
                        int(video_info["real_fps"] * video_info["total_duration"])))
                    video_info["has_audio"] = audio_stream is not None

                except (subprocess.SubprocessError, json.JSONDecodeError, KeyError, StopIteration) as e:
                    self.log(f"ffprobe解析失败，降级使用ffmpeg：{str(e)[:100]}", "warning")
                    raise
            else:
                raise FileNotFoundError("ffprobe路径不存在")

        except Exception:
            try:
                cmd = [self.ffmpeg_path, "-i", video_path, "-hide_banner"]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8",
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                output = result.stderr

                fps_match = re.search(r"(\d+(?:\.\d+)?) fps", output)
                video_info["real_fps"] = float(fps_match.group(1)) if fps_match else 30.0

                duration_match = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", output)
                if duration_match:
                    h, m, s, ms = duration_match.groups()
                    video_info["total_duration"] = int(h)*3600 + int(m)*60 + int(s) + int(ms)/100
                else:
                    video_info["total_duration"] = 60.0

                video_info["total_frames"] = int(video_info["real_fps"] * video_info["total_duration"])
                video_info["has_audio"] = "Audio:" in output

            except (subprocess.SubprocessError, OSError) as e:
                self.log(f"预解析失败：{os.path.basename(video_path)}，错误：{str(e)[:200]}", "error")
                return False

        if video_info["total_duration"] < 3.0:
            self.log(f"视频时长过短（至少需要3秒）：{os.path.basename(video_path)}", "error")
            return False

        if video_info["real_fps"] <= 0:
            video_info["real_fps"] = 30.0

        self.pre_analysis_data[video_path] = video_info
        self.log(f"预解析完成：{os.path.basename(video_path)} - 帧数：{video_info['total_frames']}，时长：{video_info['total_duration']:.1f}秒，帧率：{video_info['real_fps']:.1f}fps", "success")
        return True

    def generate_cut_points(self, video_info: Dict, frames_to_cut: int) -> List[Dict]:
        total_duration = video_info["total_duration"]
        fps = max(1.0, video_info["real_fps"])
        one_frame_time = 1.0 / fps

        num_cuts = max(0, frames_to_cut)
        num_segments = num_cuts + 1

        if num_cuts <= 0:
            return [{"start": 0.0, "end": total_duration}]

        total_cut_time = num_cuts * one_frame_time * FRAMES_PER_CUT_POINT
        playable_duration = total_duration - total_cut_time

        if playable_duration <= 0:
            self.log(f"  [抽帧] 视频时长({total_duration:.1f}s)不足以容纳{num_cuts}帧删除，保留原视频", "warning")
            return [{"start": 0.0, "end": total_duration}]

        segment_length = playable_duration / num_segments

        if segment_length < MIN_SEGMENT_DURATION and num_cuts > 1:
            adjusted_cuts = int((total_duration - MIN_SEGMENT_DURATION) / (MIN_SEGMENT_DURATION + one_frame_time * FRAMES_PER_CUT_POINT))
            if adjusted_cuts >= 1:
                self.log(f"  [抽帧] 原计划删{num_cuts}帧，调整为删{adjusted_cuts}帧（每段需≥{MIN_SEGMENT_DURATION}s）", "info")
                num_cuts = adjusted_cuts
                num_segments = num_cuts + 1
                total_cut_time = num_cuts * one_frame_time * FRAMES_PER_CUT_POINT
                playable_duration = total_duration - total_cut_time
                segment_length = playable_duration / num_segments
            else:
                self.log(f"  [抽帧] 视频时长({total_duration:.1f}s)不足以容纳任何删帧，保留原视频", "warning")
                return [{"start": 0.0, "end": total_duration}]

        segments = []
        current_time = 0.0

        for i in range(num_segments):
            seg_start = current_time
            seg_end = current_time + segment_length
            cut_start = seg_end
            cut_end = cut_start + (one_frame_time * FRAMES_PER_CUT_POINT)

            actual_end = min(seg_end, total_duration)
            if actual_end > seg_start + 0.001:
                segments.append({
                    "start": round(seg_start, 6),
                    "end": round(actual_end, 6)
                })

            current_time = cut_end

        segments = [seg for seg in segments if seg["end"] - seg["start"] > 0.01]
        if not segments:
            segments.append({"start": 0.0, "end": total_duration})

        self.log(
            f"  [抽帧] 总删{num_cuts}帧(每处{FRAMES_PER_CUT_POINT}帧)，"
            f"分{num_segments}段，每段约{segment_length:.2f}s",
            "success"
        )

        return segments

    def _build_dedup_vf_filters(self) -> List[str]:
        filters = []
        if self.dedup_time_shift.get():
            filters.append("setpts=PTS+0.0001")
        if self.dedup_brightness.get():
            filters.append("eq=brightness=0.01:contrast=1.01")
        if self.dedup_hue.get():
            filters.append("hue=h=1")
        if self.dedup_mirror.get():
            filters.append("hflip")
        if self.dedup_rgb_shift.get():
            filters.append("rgbashift=rh=2:gh=-1:bh=1")
        if self.dedup_mask.get():
            filters.append(f"colorchannelmixer=aa={self.dedup_mask_value.get()}")
        return filters

    def _should_tweak_fps(self) -> bool:
        return self.dedup_mode.get() != DEDUP_MODE_NONE and self.dedup_fps_tweak.get()

    def _should_add_dedup_metadata(self) -> bool:
        return self.dedup_mode.get() != DEDUP_MODE_NONE and self.dedup_md5_change.get()

    def process_single_video_file(self, video_path: str, output_subfolder: str, params: Dict) -> bool:
        if self._stop_event.is_set():
            return False

        try:
            video_info = self.pre_analysis_data.get(video_path)
            if not video_info:
                self.log(f"未找到视频解析数据：{os.path.basename(video_path)}", "error")
                return False

            frames_to_cut = params["frames_to_cut"]
            output_name = params["output_name"]
            output_path = os.path.join(output_subfolder, output_name)

            if Path(output_path).exists():
                if not self.overwrite_enable.get():
                    self.log(f"文件已存在，跳过：{output_name}", "warning")
                    return "skipped"
                else:
                    try:
                        os.remove(output_path)
                    except OSError as e:
                        self.log(f"无法删除已存在文件：{output_name}", "warning")
                        return False

            mode = self.dedup_mode.get()

            if mode == DEDUP_MODE_BEFORE:
                return self._process_dedup_then_sampling(
                    video_path, video_info, output_path, output_name, frames_to_cut
                )
            elif mode == DEDUP_MODE_AFTER:
                return self._process_sampling_then_dedup(
                    video_path, video_info, output_path, output_name, frames_to_cut
                )
            else:
                return self._process_sampling_only(
                    video_path, video_info, output_path, output_name, frames_to_cut
                )

        except subprocess.TimeoutExpired:
            self.log(f"处理超时：{params['output_name']}", "error")
            return False
        except Exception as e:
            self.log(f"处理异常：{params['output_name']}，错误：{str(e)[:200]}", "error")
            self.log(f"异常详情：{traceback.format_exc()[:500]}", "error")
            return False

    def _process_sampling_only(self, video_path: str, video_info: Dict,
                                output_path: str, output_name: str,
                                frames_to_cut: int) -> bool:
        segments = self.generate_cut_points(video_info, frames_to_cut)
        filter_complex_str = self._build_concat_filter(segments, video_info)
        return self._run_ffmpeg_encode(
            video_path, output_path, output_name,
            filter_complex_str, video_info["has_audio"],
            map_video="[outv]", map_audio="[outa]" if video_info["has_audio"] else None
        )

    def _process_dedup_then_sampling(self, video_path: str, video_info: Dict,
                                      output_path: str, output_name: str,
                                      frames_to_cut: int) -> bool:
        self.log(f"  [{output_name}] 第1步：去重处理...", "dedup")

        dedup_filters = self._build_dedup_vf_filters()
        if not dedup_filters and not self._should_add_dedup_metadata() and not self._should_tweak_fps():
            return self._process_sampling_only(video_path, video_info, output_path, output_name, frames_to_cut)

        temp_dedup_path = os.path.join(self.temp_dir, f"dedup_{os.getpid()}_{random.randint(10000, 99999)}.mp4")
        success = self._run_dedup_pass(video_path, temp_dedup_path, output_name, dedup_filters, video_info["has_audio"])

        if not success:
            self._cleanup_temp(temp_dedup_path)
            return False

        self.log(f"  [{output_name}] 第2步：删帧处理...", "info")

        temp_info = self._quick_probe_video(temp_dedup_path)
        if not temp_info:
            self._cleanup_temp(temp_dedup_path)
            return False

        segments = self.generate_cut_points(temp_info, frames_to_cut)
        filter_complex_str = self._build_concat_filter(segments, temp_info)

        success = self._run_ffmpeg_encode(
            temp_dedup_path, output_path, output_name,
            filter_complex_str, temp_info["has_audio"],
            map_video="[outv]", map_audio="[outa]" if temp_info["has_audio"] else None
        )

        self._cleanup_temp(temp_dedup_path)
        return success

    def _process_sampling_then_dedup(self, video_path: str, video_info: Dict,
                                      output_path: str, output_name: str,
                                      frames_to_cut: int) -> bool:
        self.log(f"  [{output_name}] 第1步：删帧处理...", "info")

        segments = self.generate_cut_points(video_info, frames_to_cut)
        filter_complex_str = self._build_concat_filter(segments, video_info)

        temp_sampling_path = os.path.join(self.temp_dir, f"sampling_{os.getpid()}_{random.randint(10000, 99999)}.mp4")
        success = self._run_ffmpeg_encode(
            video_path, temp_sampling_path, output_name,
            filter_complex_str, video_info["has_audio"],
            map_video="[outv]", map_audio="[outa]" if video_info["has_audio"] else None
        )

        if not success:
            self._cleanup_temp(temp_sampling_path)
            return False

        self.log(f"  [{output_name}] 第2步：去重处理...", "dedup")

        dedup_filters = self._build_dedup_vf_filters()
        if not dedup_filters and not self._should_add_dedup_metadata() and not self._should_tweak_fps():
            try:
                shutil.move(temp_sampling_path, output_path)
                self.log(f"成功生成：{output_name}", "success")
                return True
            except OSError as e:
                self.log(f"文件移动失败：{str(e)[:100]}", "error")
                self._cleanup_temp(temp_sampling_path)
                return False

        temp_info = self._quick_probe_video(temp_sampling_path)
        success = self._run_dedup_pass(temp_sampling_path, output_path, output_name, dedup_filters, temp_info["has_audio"] if temp_info else video_info["has_audio"])

        self._cleanup_temp(temp_sampling_path)
        return success

    def _build_concat_filter(self, segments: List[Dict], video_info: Dict) -> str:
        filter_parts = []
        concat_inputs = []
        has_audio = video_info["has_audio"]

        for i, seg in enumerate(segments):
            start = max(0.0, seg['start'])
            end = min(video_info["total_duration"], seg['end'])
            if end - start < 0.001:
                continue

            filter_parts.append(f"[0:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[v{i}];")
            if has_audio:
                filter_parts.append(f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{i}];")
                concat_inputs.append(f"[v{i}][a{i}]")
            else:
                concat_inputs.append(f"[v{i}]")

        if not concat_inputs:
            return ""

        segment_count = len(concat_inputs)
        if has_audio:
            filter_parts.append(f"{''.join(concat_inputs)}concat=n={segment_count}:v=1:a=1[outv][outa]")
        else:
            filter_parts.append(f"{''.join(concat_inputs)}concat=n={segment_count}:v=1:a=0[outv]")

        return "".join(filter_parts)

    def _run_dedup_pass(self, input_path: str, output_path: str, output_name: str,
                        dedup_filters: List[str], has_audio: bool) -> bool:
        if not dedup_filters:
            vf_str = "null"
        else:
            vf_str = ",".join(dedup_filters)

        encoder_key = self.hardware_accel.get()
        if encoder_key not in self.valid_encoders:
            encoder_key = "cpu"
        encoder_info = HARDWARE_ENCODERS[encoder_key]
        encoder_params = ENCODER_PARAMS[encoder_key]

        cmd = [
            self.ffmpeg_path, "-y", "-v", "error", "-hide_banner",
            "-i", input_path,
            "-vf", vf_str,
            "-c:v", encoder_info["vcodec"],
            "-preset", encoder_params["preset"],
            *encoder_params["extra_args"],
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]

        if self._should_tweak_fps():
            cmd.extend(["-r", "30.001"])

        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "44100"])

        if self._should_add_dedup_metadata():
            cmd.extend(["-metadata", f"random={random.randint(1, 999999)}"])

        cmd.append(output_path)

        active_filters_desc = ",".join(dedup_filters) if dedup_filters else "无"
        self.log(f"  去重滤镜：{active_filters_desc}", "dedup")

        return self._execute_ffmpeg(cmd, output_path, output_name, encoder_key, use_vf=True)

    def _run_ffmpeg_encode(self, video_path: str, output_path: str, output_name: str,
                           filter_complex_str: str, has_audio: bool,
                           map_video: str = "[outv]", map_audio: str = None,
                           use_vf: bool = False) -> bool:
        encoder_key = self.hardware_accel.get()
        if encoder_key not in self.valid_encoders:
            encoder_key = "cpu"
        encoder_info = HARDWARE_ENCODERS[encoder_key]
        encoder_params = ENCODER_PARAMS[encoder_key]

        cmd = [
            self.ffmpeg_path, "-y", "-v", "error", "-hide_banner",
            "-i", video_path,
            "-filter_complex", filter_complex_str,
            "-map", map_video,
            "-c:v", encoder_info["vcodec"],
            "-preset", encoder_params["preset"],
            *encoder_params["extra_args"],
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+igndts"
        ]

        if map_audio:
            cmd.extend(["-map", map_audio, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "44100"])

        if self._should_add_dedup_metadata():
            cmd.extend(["-metadata", f"random={random.randint(1, 999999)}"])

        cmd.append(output_path)

        return self._execute_ffmpeg(cmd, output_path, output_name, encoder_key)

    def _execute_ffmpeg(self, cmd: list, output_path: str, output_name: str,
                        encoder_key: str, use_vf: bool = False) -> bool:
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )

        with self._processes_lock:
            self._processes.append(proc)

        try:
            stdout, stderr = proc.communicate(timeout=FFMPEG_TIMEOUT)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            proc.terminate()
            stdout, stderr = proc.communicate()
            returncode = -1

        with self._processes_lock:
            if proc in self._processes:
                self._processes.remove(proc)

        if returncode != 0:
            if encoder_key != "cpu":
                self.log(f"{HARDWARE_ENCODERS[encoder_key]['name']} 失败，自动切换 CPU 重试...", "info")
                retry_cmd = self._rebuild_cmd_for_cpu(cmd, use_vf)
                retry_proc = subprocess.Popen(
                    retry_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", startupinfo=startupinfo,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                with self._processes_lock:
                    self._processes.append(retry_proc)

                try:
                    retry_stdout, retry_stderr = retry_proc.communicate(timeout=FFMPEG_TIMEOUT)
                    if retry_proc.returncode == 0 and self.check_file_valid(output_path):
                        self.log(f"CPU编码重试成功：{output_name}", "success")
                        with self._processes_lock:
                            if retry_proc in self._processes:
                                self._processes.remove(retry_proc)
                        return True
                    else:
                        self.log(f"CPU重试也失败：{(retry_stderr or '未知错误')[:200]}", "error")
                        with self._processes_lock:
                            if retry_proc in self._processes:
                                self._processes.remove(retry_proc)
                        return False
                except subprocess.TimeoutExpired:
                    retry_proc.terminate()
                    with self._processes_lock:
                        if retry_proc in self._processes:
                            self._processes.remove(retry_proc)
                    return False
            else:
                self.log(f"处理失败：{output_name} (错误码：{returncode})", "error")
                self.log(f"错误详情：{(stderr or '无')[:300]}", "error")
                return False

        if not self.check_file_valid(output_path):
            self.log(f"生成的文件无效：{output_name}", "error")
            return False

        self.log(f"成功生成：{output_name}", "success")
        return True

    def _rebuild_cmd_for_cpu(self, original_cmd: list, use_vf: bool) -> list:
        cpu_params = ENCODER_PARAMS["cpu"]
        input_path = ""
        output_path = ""
        new_cmd = []

        skip_next = False
        for i, arg in enumerate(original_cmd):
            if skip_next:
                skip_next = False
                continue
            if arg == "-c:v":
                new_cmd.extend(["-c:v", "libx264"])
                skip_next = True
            elif arg == "-preset":
                new_cmd.extend(["-preset", cpu_params["preset"]])
                skip_next = True
            elif arg in ("-rc", "-cq", "-b:v", "-maxrate", "-bufsize", "-tune",
                        "-rc-lookahead", "-global_quality", "-look_ahead",
                        "-qp_i", "-qp_p", "-quality", "-q:v",
                        "-crf", "-threads"):
                skip_next = True
            elif arg == "color=size=640x480:duration=0.1:rate=10":
                continue
            elif arg == "-f" and i + 1 < len(original_cmd) and original_cmd[i + 1] == "null":
                skip_next = True
            else:
                new_cmd.append(arg)

        final_cmd = []
        inserted = False
        for arg in new_cmd:
            final_cmd.append(arg)
            if arg == "-preset" and not inserted:
                final_cmd.extend(cpu_params["extra_args"])
                inserted = True

        return final_cmd

    def _quick_probe_video(self, video_path: str) -> Optional[Dict]:
        if not Path(video_path).exists():
            return None
        try:
            if Path(self.ffprobe_path).exists():
                cmd = [
                    self.ffprobe_path, "-v", "quiet", "-print_format", "json",
                    "-show_streams", "-show_format", video_path
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8",
                    check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                probe_data = json.loads(result.stdout)
                video_stream = next((s for s in probe_data["streams"] if s["codec_type"] == "video"), None)
                audio_stream = next((s for s in probe_data["streams"] if s["codec_type"] == "audio"), None)

                if not video_stream:
                    return None

                fps_str = video_stream.get("avg_frame_rate", "30/1")
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    real_fps = float(num) / float(den) if den != "0" else 30.0
                else:
                    real_fps = float(fps_str) if fps_str else 30.0

                total_duration = float(probe_data["format"].get("duration", 0))
                total_frames = int(video_stream.get("nb_frames", int(real_fps * total_duration)))

                return {
                    "real_fps": real_fps,
                    "total_duration": total_duration,
                    "total_frames": total_frames,
                    "has_audio": audio_stream is not None,
                }
        except Exception:
            pass

        try:
            cmd = [self.ffmpeg_path, "-i", video_path, "-hide_banner"]
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            output = result.stderr
            fps_match = re.search(r"(\d+(?:\.\d+)?) fps", output)
            real_fps = float(fps_match.group(1)) if fps_match else 30.0
            duration_match = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", output)
            total_duration = 60.0
            if duration_match:
                h, m, s, ms = duration_match.groups()
                total_duration = int(h)*3600 + int(m)*60 + int(s) + int(ms)/100
            return {
                "real_fps": real_fps,
                "total_duration": total_duration,
                "total_frames": int(real_fps * total_duration),
                "has_audio": "Audio:" in output,
            }
        except Exception:
            return None

    def _cleanup_temp(self, path: str):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def check_file_valid(self, file_path: str) -> bool:
        return Path(file_path).exists() and Path(file_path).stat().st_size > 1024

    def generate_batch_params(self, video_path: str, video_name: str, count: int) -> List[Dict]:
        params_list = []
        prefix = self.file_prefix.get().strip()
        if not prefix:
            prefix = os.path.splitext(video_name)[0]

        prefix = re.sub(r'[\\/:*?"<>|]', '_', prefix)

        video_info = self.pre_analysis_data.get(video_path, {})
        duration = video_info.get("total_duration", 9999)

        if duration < 60:
            base_cut = 2
            step_cut = 2
            tier_label = f"短视频({duration:.0f}s < 60s)"
        elif duration < 120:
            base_cut = 5
            step_cut = 3
            tier_label = f"中等({duration:.0f}s, 60~120s)"
        else:
            base_cut = CUT_BASE_FRAMES
            step_cut = CUT_STEP_FRAMES
            tier_label = f"长视频({duration:.0f}s ≥ 120s)"

        self.log(f"  [删帧规则] {tier_label}：起始{base_cut}帧，每次+{step_cut}帧", "info")

        for idx in range(count):
            frames_to_cut = base_cut + step_cut * idx
            video_base = os.path.splitext(video_name)[0]
            video_base = re.sub(r'[\\/:*?"<>|]', '_', video_base)
            output_name = f"{prefix}_{video_base}_{idx+1:02d}.mp4"

            params_list.append({
                "index": idx + 1,
                "frames_to_cut": frames_to_cut,
                "output_name": output_name
            })
        return params_list

    def batch_process_thread(self):
        try:
            if not self.input_videos:
                self.log("请先添加至少一个视频文件", "error")
                return

            output_root = self.output_folder.get()
            if not output_root:
                self.log("请选择输出根文件夹", "error")
                return

            if not self.check_ffmpeg_available():
                self._queue_ui_update(lambda: messagebox.showerror("错误", "FFmpeg加载失败！"))
                return

            Path(output_root).mkdir(exist_ok=True)
            self.save_config()

            batch_count_per_video = self.batch_count.get()
            parallel_num = max(1, min(MAX_PARALLEL, self.parallel_count.get()))
            total_tasks = len(self.input_videos) * batch_count_per_video
            self.total_count.set(total_tasks)
            self.processed_count.set(0)

            def update_progress_bar():
                self.progress_bar.set(0)
            self._queue_ui_update(update_progress_bar)

            encoder_key = self.hardware_accel.get()
            if encoder_key not in self.valid_encoders:
                encoder_key = "cpu"
            encoder_name = HARDWARE_ENCODERS[encoder_key]["name"]

            mode = self.dedup_mode.get()
            mode_desc = {DEDUP_MODE_NONE: "不去重", DEDUP_MODE_BEFORE: "抽帧前去重", DEDUP_MODE_AFTER: "抽帧后去重"}
            dedup_detail = ""
            if mode != DEDUP_MODE_NONE:
                active_dedups = []
                if self.dedup_time_shift.get():
                    active_dedups.append("时间偏移")
                if self.dedup_md5_change.get():
                    active_dedups.append("MD5修改")
                if self.dedup_fps_tweak.get():
                    active_dedups.append("帧率微调")
                if self.dedup_brightness.get():
                    active_dedups.append("亮度/对比度")
                if self.dedup_hue.get():
                    active_dedups.append("色调偏移")
                if self.dedup_mirror.get():
                    active_dedups.append("镜像")
                if self.dedup_rgb_shift.get():
                    active_dedups.append("RGB偏移")
                if self.dedup_mask.get():
                    active_dedups.append("蒙版倒置")
                dedup_detail = f"（{', '.join(active_dedups) if active_dedups else '仅metadata'}）"

            self.log("=" * 50, "info")
            self.log(f"开始批量处理", "info")
            self.log(f"视频数量：{len(self.input_videos)}个", "info")
            self.log(f"每个视频生成：{batch_count_per_video}个抽帧视频", "info")
            self.log(f"总计任务：{total_tasks}个", "info")
            self.log(f"编码方式：{encoder_name}", "info")
            self.log(f"并行数量：{parallel_num}个任务同时处理", "info")
            self.log(f"去重模式：{mode_desc.get(mode, '未知')} {dedup_detail}", "dedup")
            self.log(f"输出路径：{output_root}", "info")
            self.log("=" * 50, "info")

            self._stop_event.clear()
            self._queue_ui_update(lambda: self.set_widgets_state(tk.DISABLED))

            all_tasks = []
            for video_idx, video_path in enumerate(self.input_videos):
                video_name = os.path.basename(video_path)
                video_folder_name = re.sub(r'[\\/:*?"<>|]', '_', os.path.splitext(video_name)[0])
                video_subfolder = os.path.join(output_root, video_folder_name)
                Path(video_subfolder).mkdir(exist_ok=True)

                self.log(f"\n预解析第{video_idx+1}/{len(self.input_videos)}个视频：{video_name}", "info")

                if not self.pre_analyse_video(video_path):
                    self.log(f"跳过该视频：{video_name}", "error")
                    with self._process_lock:
                        for _ in range(batch_count_per_video):
                            self.processed_count.set(self.processed_count.get() + 1)
                            current_val = self.processed_count.get()
                            self._queue_ui_update(lambda v=current_val: self.progress_bar.set(v / total_tasks if total_tasks > 0 else 0))
                    continue

                params_list = self.generate_batch_params(video_path, video_name, batch_count_per_video)
                for params in params_list:
                    all_tasks.append((video_path, video_subfolder, params, video_name))

            success_count = 0
            skip_count = 0
            fail_count = 0

            with ThreadPoolExecutor(max_workers=parallel_num) as executor:
                future_to_task = {}
                for task in all_tasks:
                    if self._stop_event.is_set():
                        break
                    video_path, video_subfolder, params, video_name = task
                    future = executor.submit(
                        self._process_task_wrapper,
                        video_path, video_subfolder, params, video_name
                    )
                    future_to_task[future] = params["output_name"]

                for future in as_completed(future_to_task):
                    if self._stop_event.is_set():
                        for f in future_to_task:
                            f.cancel()
                        break

                    output_name = future_to_task[future]
                    try:
                        result = future.result()
                        if result == "success":
                            success_count += 1
                        elif result == "skip":
                            skip_count += 1
                        else:
                            fail_count += 1
                    except Exception as e:
                        self.log(f"任务异常：{output_name}，{str(e)[:100]}", "error")
                        fail_count += 1

                    with self._process_lock:
                        self.processed_count.set(self.processed_count.get() + 1)
                        current_val = self.processed_count.get()
                        self._queue_ui_update(lambda v=current_val: self.progress_bar.set(v / total_tasks if total_tasks > 0 else 0))

            self.log("=" * 50, "info")
            if self._stop_event.is_set():
                self.log(f"用户手动停止了处理", "warning")
                self.log(f"已处理：{self.processed_count.get()}/{total_tasks}个任务", "info")
            else:
                self.log(f"全部处理完成！", "success")
                self.log(f"成功：{success_count} | 跳过：{skip_count} | 失败：{fail_count}", "success")
                self.log(f"输出路径：{output_root}", "info")

                def show_complete_dialog():
                    messagebox.showinfo(
                        "完成",
                        f"批量处理完成！\n成功 {success_count} | 跳过 {skip_count} | 失败 {fail_count}\n输出路径：{output_root}"
                    )
                self._queue_ui_update(show_complete_dialog)

        except Exception as e:
            self.log(f"批量处理异常：{str(e)}", "error")
            self.log(f"异常详情：{traceback.format_exc()}", "error")
            self._queue_ui_update(lambda: messagebox.showerror("错误", f"批量处理异常：{str(e)[:200]}"))
        finally:
            self._running = False
            self.terminate_all_processes()
            self.pre_analysis_data.clear()

            def reset_ui():
                self.set_widgets_state(tk.NORMAL)
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
            self._queue_ui_update(reset_ui)

    def _process_task_wrapper(self, video_path: str, output_subfolder: str, params: Dict, video_name: str) -> str:
        if self._stop_event.is_set():
            return "skip"
        result = self.process_single_video_file(video_path, output_subfolder, params)
        if result == "skipped":
            return "skip"
        return "success" if result else "fail"

    def start_batch_process(self):
        if self._running:
            messagebox.showwarning("提示", "正在处理中，请不要重复点击！")
            return

        if not self.input_videos:
            messagebox.showwarning("提示", "请先添加至少一个视频文件！")
            return

        if not self.output_folder.get():
            messagebox.showwarning("提示", "请先选择输出文件夹！")
            return

        self.clear_log()

        self._running = True
        self._processes.clear()
        self.pre_analysis_data.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(target=self.batch_process_thread, daemon=True).start()

    def stop_batch_process(self):
        if not self._running:
            return

        self.log("正在停止处理，请稍候...", "warning")
        self._stop_event.set()
        self.terminate_all_processes()
        self.stop_btn.configure(state="disabled")

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.on_window_close()


# ===================== 全局启动函数 =====================
def run_pro_main():
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if sys.platform == "win32":
        os.system("chcp 65001 >nul")

    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))
    else:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

    app = BatchVideoSamplerPro()
    app.run()


if __name__ == "__main__":
    run_pro_main()
