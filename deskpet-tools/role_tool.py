# -*- coding: utf-8 -*-
"""桌宠角色工具 · GUI 主程序（粉白主题）

两个功能：
  ① 视频 → 动作：把绿幕角色视频转化为标准化桌宠动作（帧图 + 位移轨迹）
     —— 可选「位移锚点」：脚底贴地窄带 / 角色中心
  ② 动作 → 角色包：把一组动作组装成角色包（role.json + 帧图），
     放到 desktop-pet/skins/ 即可被桌宠识别并切换皮肤。

依赖：pip install -r requirements.txt
运行：python role_tool.py
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)

import video_to_action as vta
import package_role as pkg

_ROOT = os.path.dirname(_BASE)
DESKTOP_PET = os.path.join(_ROOT, "desktop-pet")
DEFAULT_ASSETS = os.path.join(DESKTOP_PET, "assets")
DEFAULT_SKINS = os.path.join(DESKTOP_PET, "skins")

# ---- 主题色（粉白） ----
BG = "#FFF7F9"
CARD = "#FFFFFF"
CARD_EDGE = "#F4D9E4"
PRIMARY = "#FF5C8A"
DARK = "#D6336C"
TEXT = "#4A4A4A"
MUTED = "#9A9A9A"
FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")

# 动作类型 / 锚点文案
MOTION_ITEMS = [
    ("static", "站姿动作（思考 / 开心 / 哭泣等）"),
    ("vertical", "位移 · 垂直（跳跃类）"),
    ("dual", "位移 · 双向（打滚类）"),
]
ANCHOR_ITEMS = [
    ("foot", "脚底贴地窄带（贴地不动点）"),
    ("center", "角色中心（内容包围盒中心）"),
]


class ActionDialog(tk.Toplevel):
    """添加动作对话框：动作 key + 显示名 + 帧图目录。"""

    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("添加动作")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self._build()
        self.geometry("+%d+%d" % (parent.winfo_rootx() + 80,
                                  parent.winfo_rooty() + 80))

    def _build(self):
        pad = {"padx": 14, "pady": 10}
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill="both", expand=True, **pad)

        tk.Label(frm, text="动作 key（桌宠内部标识，决定归属哪个动作位）",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self.key_var = tk.StringVar(value="think")
        self.key_combo = ttk.Combobox(frm, textvariable=self.key_var,
                                      state="readonly", width=34)
        self.key_combo["values"] = list(pkg.DEFAULT_ACTION_LABELS.keys())
        self.key_combo.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.key_combo.bind("<<ComboboxSelected>>", self._on_key)

        tk.Label(frm, text="动作显示名（右键菜单 / AI 标签显示的名字）",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self.label_var = tk.StringVar()
        self.label_ent = ttk.Entry(frm, textvariable=self.label_var, width=36)
        self.label_ent.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.dir_var = tk.StringVar()
        tk.Label(frm, text="帧图目录（含 frame_0000.png…，位移动作含 traj.json）",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Entry(frm, textvariable=self.dir_var, width=24).grid(
            row=5, column=0, sticky="w")
        ttk.Button(frm, text="浏览…", command=self._browse).grid(
            row=5, column=1, sticky="e", padx=(8, 0))

        self.info = tk.Label(frm, text="", bg=BG, fg=MUTED,
                             font=("Microsoft YaHei UI", 9))
        self.info.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btns = tk.Frame(frm, bg=BG)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="确定", style="Accent.TButton",
                   command=self._ok).pack(side="left")

        self._on_key()

    def _on_key(self, _=None):
        self.label_var.set(pkg.DEFAULT_ACTION_LABELS.get(self.key_var.get(), ""))
        self._refresh_info()

    def _browse(self):
        d = filedialog.askdirectory(title="选择动作帧图目录")
        if d:
            self.dir_var.set(d)
            self._refresh_info()

    def _refresh_info(self):
        d = self.dir_var.get().strip()
        if not os.path.isdir(d):
            self.info.config(text="")
            return
        pngs = [f for f in os.listdir(d) if f.lower().endswith(".png")]
        traj = os.path.isfile(os.path.join(d, "traj.json"))
        self.info.config(text=f"检测到 {len(pngs)} 帧 PNG" + ("，含 traj.json" if traj else ""))

    def _ok(self):
        key = self.key_var.get().strip()
        label = self.label_var.get().strip() or pkg.DEFAULT_ACTION_LABELS.get(key, key)
        d = self.dir_var.get().strip()
        if not pkg.re_ok(key):
            messagebox.showwarning("提示", f"动作 key 非法：{key}", parent=self)
            return
        if not os.path.isdir(d):
            messagebox.showwarning("提示", "请选择有效的帧图目录", parent=self)
            return
        self.callback((key, label, d))
        self.destroy()


class RoleTool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("桌宠角色工具")
        self.geometry("860x600")
        self.minsize(760, 560)
        self.configure(bg=BG)
        self._busy = False
        self._actions_meta = []          # [(key, label, dir)]

        self._setup_style()
        self._build_header()
        self._build_body()
        self._build_footer()

    # ---------------- 样式 ----------------
    def _setup_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=FONT, background=BG, foreground=TEXT)
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TFrame", background=CARD)
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("CardMuted.TLabel", background=CARD, foreground=MUTED,
                     font=("Microsoft YaHei UI", 9))
        st.configure("Primary.TLabel", background=PRIMARY, foreground="#FFFFFF",
                     font=("Microsoft YaHei UI", 15, "bold"))
        st.configure("Sub.TLabel", background=PRIMARY, foreground="#FFE3EC",
                     font=("Microsoft YaHei UI", 9))

        st.configure("TButton", background="#FFFFFF", foreground=TEXT,
                     bordercolor=CARD_EDGE, focusthickness=0, padding=(12, 5))
        st.map("TButton",
               background=[("active", "#FFEDF3"), ("pressed", "#FFE0EA")],
               bordercolor=[("active", PRIMARY)])
        st.configure("Accent.TButton", background=PRIMARY, foreground="#FFFFFF",
                     padding=(18, 6))
        st.map("Accent.TButton",
               background=[("active", DARK), ("pressed", DARK)],
               foreground=[("active", "#FFFFFF")])

        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background="#FBE3EC", foreground=TEXT,
                     padding=(20, 8), font=FONT_B, borderwidth=0)
        st.map("TNotebook.Tab",
               background=[("selected", CARD)],
               foreground=[("selected", DARK)])

        st.configure("TLabelframe", background=CARD, bordercolor=CARD_EDGE,
                     relief="solid", borderwidth=1)
        st.configure("TLabelframe.Label", background=CARD, foreground=DARK,
                     font=FONT_B)

        st.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=CARD_EDGE,
                     padding=4)
        st.configure("TCombobox", fieldbackground="#FFFFFF",
                     bordercolor=CARD_EDGE, padding=4, arrowcolor=DARK)
        st.configure("TProgressbar", background=PRIMARY, troughcolor="#FFE3EC",
                     bordercolor=CARD_EDGE, lightcolor=PRIMARY, darkcolor=PRIMARY)

    # ---------------- 头部 ----------------
    def _build_header(self):
        bar = tk.Frame(self, bg=PRIMARY, height=64)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="桌宠角色工具", bg=PRIMARY, fg="#FFFFFF",
                 font=("Microsoft YaHei UI", 16, "bold")).pack(
            side="left", padx=(18, 0), pady=10)
        tk.Label(bar, text="  视频 → 动作   ·   动作 → 角色包",
                 bg=PRIMARY, fg="#FFE3EC",
                 font=("Microsoft YaHei UI", 9)).pack(side="left", pady=12)

    # ---------------- 主体 ----------------
    def _build_body(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=12)
        self._build_tab_video()
        self._build_tab_package()

    # ---------------- 底部状态栏 ----------------
    def _build_footer(self):
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=14, pady=(0, 10))
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(foot, textvariable=self.status_var, anchor="w", bg=BG,
                 fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.version_lbl = tk.Label(
            foot, text=f"输出目录：{DEFAULT_SKINS}", bg=BG, fg=MUTED,
            font=("Microsoft YaHei UI", 9)).pack(side="right")

    # ---------------- 通用后台任务 ----------------
    def _run_async(self, fn, on_ok, busy_widgets):
        if self._busy:
            messagebox.showinfo("提示", "已有任务在处理中，请稍候")
            return
        self._busy = True
        for w in busy_widgets:
            w.config(state="disabled")
        q = queue.Queue()

        def _worker():
            try:
                q.put(("ok", fn()))
            except Exception as e:
                q.put(("err", str(e)))

        threading.Thread(target=_worker, daemon=True).start()

        def _poll():
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                self.after(120, _poll)
                return
            self._busy = False
            for w in busy_widgets:
                w.config(state="normal")
            if kind == "ok":
                on_ok(payload)
            else:
                self.status_var.set("失败：" + str(payload)[:60])
                messagebox.showerror("处理失败", str(payload))

        self.after(120, _poll)

    # ============================================================
    #  Tab ① 视频 → 动作
    # ============================================================
    def _build_tab_video(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=" ① 视频 → 动作 ")
        self.video_progress = ttk.Progressbar(tab, mode="determinate", maximum=100)

        # 卡片：视频
        c1 = ttk.LabelFrame(tab, text="绿幕视频", padding=14)
        c1.pack(fill="x", padx=8, pady=(8, 0))
        self.video_path = tk.StringVar()
        r1 = ttk.Frame(c1)
        r1.pack(fill="x")
        ttk.Entry(r1, textvariable=self.video_path).pack(side="left", fill="x",
                                                         expand=True, padx=(0, 8))
        ttk.Button(r1, text="浏览…", command=lambda: self._pick_video()).pack(side="left")

        # 卡片：动作类型 + 锚点
        c2 = ttk.LabelFrame(tab, text="动作类型与位移锚点", padding=14)
        c2.pack(fill="x", padx=8, pady=(8, 0))
        self.video_motion = tk.StringVar(value="static")
        self.video_anchor = tk.StringVar(value="foot")
        mrow = ttk.Frame(c2)
        mrow.pack(fill="x", pady=(0, 6))
        ttk.Label(mrow, text="动作类型", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.motion_combo = ttk.Combobox(mrow, textvariable=self.video_motion,
                                         state="readonly", width=30)
        self.motion_combo["values"] = [f"{k}：{v}" for k, v in MOTION_ITEMS]
        self.motion_combo.current(0)
        self.motion_combo.bind("<<ComboboxSelected>>", lambda e: self._on_motion())
        self.motion_combo.pack(side="left")

        arow = ttk.Frame(c2)
        arow.pack(fill="x")
        ttk.Label(arow, text="位移锚点", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.anchor_combo = ttk.Combobox(arow, textvariable=self.video_anchor,
                                         state="readonly", width=30)
        self.anchor_combo["values"] = [f"{k}：{v}" for k, v in ANCHOR_ITEMS]
        self.anchor_combo.current(0)
        self.anchor_combo.bind("<<ComboboxSelected>>", lambda e: self._update_anchor_hint())
        self.anchor_combo.pack(side="left")

        self.anchor_hint = tk.Label(c2, text="", bg=CARD, fg=MUTED,
                                    font=("Microsoft YaHei UI", 9), anchor="w")
        self.anchor_hint.pack(fill="x", pady=(8, 0))
        self._on_motion()

        # 卡片：输出
        c3 = ttk.LabelFrame(tab, text="输出", padding=14)
        c3.pack(fill="x", padx=8, pady=(8, 0))
        nrow = ttk.Frame(c3)
        nrow.pack(fill="x", pady=(0, 6))
        ttk.Label(nrow, text="动作名称", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.video_name = tk.StringVar()
        ttk.Entry(nrow, textvariable=self.video_name).pack(side="left", fill="x",
                                                           expand=True, padx=(0, 8))
        ttk.Label(nrow, text="如：思考（决定输出目录名）", style="CardMuted.TLabel").pack(side="left")

        orow = ttk.Frame(c3)
        orow.pack(fill="x")
        ttk.Label(orow, text="输出目录", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.video_out = tk.StringVar()
        ttk.Entry(orow, textvariable=self.video_out).pack(side="left", fill="x",
                                                          expand=True, padx=(0, 8))
        ttk.Button(orow, text="浏览…", command=lambda: self._pick_out_dir()).pack(side="left")

        # 底部
        base = ttk.Frame(tab)
        base.pack(fill="both", expand=True, padx=8, pady=(10, 8))
        self.video_progress.pack(fill="x", pady=(0, 8))
        self.video_btn = ttk.Button(base, text="开始处理", style="Accent.TButton",
                                    command=self._start_video)
        self.video_btn.pack(fill="x")

        self.video_out.set(os.path.join(DEFAULT_ASSETS, "思考"))

    def _pick_video(self):
        p = filedialog.askopenfilename(
            title="选择绿幕视频",
            filetypes=[("视频", "*.mp4 *.avi *.mov *.mkv"), ("所有文件", "*.*")])
        if p:
            self.video_path.set(p)

    def _pick_out_dir(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.video_out.set(p)

    def _on_motion(self):
        t = self.video_motion.get().split("：")[0]
        if t == "static":
            self.anchor_combo.config(state="disabled")
            self.video_anchor.set("foot")
            self.anchor_hint.config(
                text="站姿动作固定「脚底贴地」锚点，无需选择；帧图脚底对齐正面站位。")
        else:
            self.anchor_combo.config(state="readonly")
            self.video_anchor.set("center" if t == "dual" else "foot")
            self._update_anchor_hint()

    def _update_anchor_hint(self):
        a = self.video_anchor.get().split("：")[0]
        t = self.video_motion.get().split("：")[0]
        if a == "foot":
            hint = "贴地不动点：垂直轨迹 = 脚离地高度（适合「跳跃」等整体离地动作）"
        else:
            hint = "中心不动点：水平+垂直轨迹 = 角色中心偏移（适合「打滚 / 起伏」动作）"
        if t == "vertical":
            hint += "；将输出仅含 dy 的 traj.json"
        elif t == "dual":
            hint += "；将输出含 dx+dy 的 traj.json"
        self.anchor_hint.config(text=hint)

    def _start_video(self):
        video = self.video_path.get().strip()
        if not video:
            messagebox.showwarning("提示", "请先选择绿幕视频")
            return
        t = self.video_motion.get().split("：")[0]
        a = self.video_anchor.get().split("：")[0]
        name = self.video_name.get().strip() or "动作"
        out = self.video_out.get().strip()
        if not out:
            out = os.path.join(DEFAULT_ASSETS, name)
            self.video_out.set(out)

        def _do():
            n, tc = vta.process_video(
                video, out, motion=t, anchor=a,
                progress=lambda d, total, m: self._video_progress_update(
                    d, total, m, self.video_progress))
            return n, tc, out

        def _ok(res):
            n, tc, o = res
            self.status_var.set(f"完成：{n} 帧，轨迹 {tc} 条 → {o}")
            messagebox.showinfo("完成",
                                f"处理完成！\n\n{n} 帧帧图" + (f"、{tc} 条轨迹" if tc else "")
                                + f"\n输出目录：{o}\n\n可直接用作「② 打包角色包」的动作素材。")

        self._run_async(_do, _ok, [self.video_btn])

    @staticmethod
    def _video_progress_update(done, total, msg, bar):
        try:
            bar.config(maximum=max(1, total), value=done)
        except Exception:
            pass

    # ============================================================
    #  Tab ② 动作 → 角色包
    # ============================================================
    def _build_tab_package(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=" ② 动作 → 角色包 ")

        # 左：角色信息 + 动作列表
        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        c1 = ttk.LabelFrame(left, text="角色信息", padding=12)
        c1.pack(fill="x")
        self.p_role_id = tk.StringVar()
        self.p_role_name = tk.StringVar()
        self.p_pet_name = tk.StringVar()
        self.p_author = tk.StringVar()
        self.p_version = tk.StringVar(value="1.0.0")

        grid = ttk.Frame(c1)
        grid.pack(fill="x")
        fields = [
            ("角色 id", self.p_role_id, "英文/数字，唯一，也是文件夹名"),
            ("角色名称", self.p_role_name, ""),
            ("默认宠物名", self.p_pet_name, "切换后桌宠的宠物名"),
            ("作者", self.p_author, ""),
            ("版本", self.p_version, ""),
        ]
        for i, (lab, var, ph) in enumerate(fields):
            ttk.Label(grid, text=lab, style="Card.TLabel").grid(
                row=i, column=0, sticky="w", pady=2, padx=(0, 10))
            ent = ttk.Entry(grid, textvariable=var)
            ent.grid(row=i, column=1, sticky="ew", pady=2)
            if ph:
                ttk.Label(grid, text=ph, style="CardMuted.TLabel").grid(
                    row=i, column=2, sticky="w", padx=(8, 0))
        grid.columnconfigure(1, weight=1)

        c2 = ttk.LabelFrame(left, text="动作列表", padding=12)
        c2.pack(fill="both", expand=True, pady=(8, 0))
        self.act_list = tk.Listbox(
            c2, height=8, bg="#FFFFFF", fg=TEXT, selectbackground=PRIMARY,
            selectforeground="#FFFFFF", relief="flat", highlightthickness=1,
            highlightcolor=CARD_EDGE, highlightbackground=CARD_EDGE,
            font=("Microsoft YaHei UI", 9), activestyle="none")
        self.act_list.pack(fill="both", expand=True)
        btns = ttk.Frame(c2)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="＋ 添加动作", command=self._add_action).pack(
            side="left", padx=(0, 6))
        ttk.Button(btns, text="－ 删除选中", command=self._del_action).pack(
            side="left", padx=(0, 6))
        ttk.Button(btns, text="↺ 导入已有角色包…", command=self._import_role).pack(
            side="right")

        # 右：prompt + 输出
        right = ttk.Frame(tab)
        right.pack(side="right", fill="both", expand=True)

        c3 = ttk.LabelFrame(right, text="角色专属 prompt（可选，留空则沿用人格提示词）",
                            padding=12)
        c3.pack(fill="both", expand=True)
        self.prompt_text = tk.Text(c3, height=9, bg="#FFFFFF", fg=TEXT,
                                   relief="flat", highlightthickness=1,
                                   highlightcolor=CARD_EDGE,
                                   highlightbackground=CARD_EDGE,
                                   font=("Microsoft YaHei UI", 9),
                                   insertbackground=PRIMARY, wrap="word")
        self.prompt_text.pack(fill="both", expand=True)
        pr = ttk.Frame(c3)
        pr.pack(fill="x", pady=(6, 0))
        ttk.Button(pr, text="从文件加载…", command=self._load_prompt_file).pack(
            side="left", padx=(0, 6))
        ttk.Button(pr, text="清空", command=lambda: self.prompt_text.delete("1.0", "end")).pack(
            side="left")

        c4 = ttk.LabelFrame(right, text="输出", padding=12)
        c4.pack(fill="x", pady=(8, 0))
        self.p_out = tk.StringVar(value=DEFAULT_SKINS)
        orow = ttk.Frame(c4)
        orow.pack(fill="x")
        ttk.Entry(orow, textvariable=self.p_out).pack(side="left", fill="x",
                                                      expand=True, padx=(0, 8))
        ttk.Button(orow, text="浏览…", command=lambda: self._pick_pack_dir()).pack(side="left")
        ttk.Label(c4, text=f"放进取即可被桌宠「设置 → 皮肤」识别。",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(4, 0))
        self.pack_btn = ttk.Button(c4, text="打包角色包", style="Accent.TButton",
                                   command=self._start_package)
        self.pack_btn.pack(fill="x", pady=(8, 0))

    def _pick_pack_dir(self):
        p = filedialog.askdirectory(title="选择角色包输出目录")
        if p:
            self.p_out.set(p)

    def _add_action(self):
        ActionDialog(self, self._on_action_added)

    def _on_action_added(self, item):
        key, label, d = item
        for i, (k, l, _dd) in enumerate(self._actions_meta):
            if k == key:
                self._actions_meta[i] = item
                self._render_actions()
                return
        self._actions_meta.append(item)
        self._render_actions()

    def _del_action(self):
        sel = self.act_list.curselection()
        if sel:
            self._actions_meta.pop(sel[0])
            self._render_actions()

    def _render_actions(self):
        self.act_list.delete(0, "end")
        for key, label, d in self._actions_meta:
            if os.path.isdir(d):
                pngs = len([f for f in os.listdir(d) if f.lower().endswith(".png")])
                traj = " +traj" if os.path.isfile(os.path.join(d, "traj.json")) else ""
            else:
                pngs = 0
                traj = "（目录缺失）"
            self.act_list.insert("end",
                                 f"{key:<10} {label:<8}  {pngs}帧{traj}   {d}")

    def _import_role(self):
        from pet import skins as _  # noqa 仅示意：直接扫描 skins 目录
        path = filedialog.askdirectory(title="选择已有角色包文件夹（含 role.json）")
        if not path:
            return
        rp = os.path.join(path, "role.json")
        if not os.path.isfile(rp):
            messagebox.showwarning("提示", "该文件夹没有 role.json", parent=self)
            return
        try:
            import json
            with open(rp, encoding="utf-8") as f:
                role = json.load(f)
        except (OSError, ValueError) as e:
            messagebox.showerror("读取失败", str(e), parent=self)
            return
        if not isinstance(role, dict):
            messagebox.showwarning("提示", "role.json 格式不正确", parent=self)
            return
        self.p_role_id.set(role.get("id", ""))
        self.p_role_name.set(role.get("name", ""))
        self.p_pet_name.set(role.get("default_pet_name", ""))
        self.p_author.set(role.get("author", ""))
        self.p_version.set(role.get("version", "1.0.0"))
        self._actions_meta = []
        for key, info in (role.get("actions") or {}).items():
            if not isinstance(info, dict):
                continue
            d = os.path.join(path, info.get("dir") or key)
            self._actions_meta.append(
                (key, info.get("label") or key, d))
        self._render_actions()
        pp = os.path.join(path, role.get("prompt_file") or "prompt.txt")
        self.prompt_text.delete("1.0", "end")
        if os.path.isfile(pp):
            try:
                with open(pp, encoding="utf-8") as f:
                    self.prompt_text.insert("1.0", f.read())
            except OSError:
                pass
        self.status_var.set(f"已导入角色包：{path}")

    def _load_prompt_file(self):
        p = filedialog.askopenfilename(title="选择 prompt 文本",
                                       filetypes=[("文本", "*.txt"), ("所有文件", "*.*")])
        if p:
            try:
                with open(p, encoding="utf-8") as f:
                    self.prompt_text.delete("1.0", "end")
                    self.prompt_text.insert("1.0", f.read())
            except OSError as e:
                messagebox.showerror("读取失败", str(e))

    def _start_package(self):
        role_id = self.p_role_id.get().strip()
        name = self.p_role_name.get().strip() or role_id
        if not role_id:
            messagebox.showwarning("提示", "请填写角色 id")
            return
        if not self._actions_meta:
            messagebox.showwarning("提示", "请至少添加一个动作")
            return
        out = self.p_out.get().strip() or DEFAULT_SKINS
        prompt_text = self.prompt_text.get("1.0", "end").strip()
        meta = list(self._actions_meta)

        def _do():
            rp = pkg.package_role(
                role_id, name, self.p_author.get().strip(),
                self.p_version.get().strip() or "1.0.0",
                self.p_pet_name.get().strip() or name,
                meta, out, action_tags=None, chatlines=None,
                prompt_text=prompt_text,
                progress=lambda d, total, m: self.status_var.set(m))
            return rp

        def _ok(rp):
            self.status_var.set(f"角色包已生成：{rp}")
            if messagebox.askyesno("完成",
                                   f"角色包已生成：\n{rp}\n\n"
                                   "是否现在打开 skins 目录查看？"):
                os.startfile(os.path.dirname(rp))

        self._run_async(_do, _ok, [self.pack_btn])


def main():
    app = RoleTool()
    app.mainloop()


if __name__ == "__main__":
    main()