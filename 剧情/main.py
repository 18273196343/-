import sys
import json
import os
import shutil
import math
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QPushButton, QLabel, QTextEdit,
                               QLineEdit, QMessageBox, QComboBox, QScrollArea, QGraphicsOpacityEffect, QFrame,
                               QFileDialog, QInputDialog, QListWidget, QDialog, QGraphicsDropShadowEffect)
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QIcon, QPainterPath
from PySide6.QtCore import Qt, QPropertyAnimation, QSequentialAnimationGroup, QParallelAnimationGroup, QUrl, QPoint, \
    QPointF, QSize, QRect
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from ceshi import AIGeneratorDialog
from PySide6.QtGui import QIcon
from PySide6.QtCore import QSize, QRect

# --- 全局根目录 ---
ROOT_DIR = "./MyStories"


def get_resource_path(relative_path):
    """获取资源文件的绝对路径，兼容开发环境和 PyInstaller 打包后的环境"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 创建的临时文件夹
        base_path = sys._MEIPASS
    else:
        # 正常的开发环境
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


def init_root_structure():
    # 使用可执行文件所在目录作为根目录
    if hasattr(sys, '_MEIPASS'):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.abspath(os.path.dirname(__file__))
    
    global ROOT_DIR
    ROOT_DIR = os.path.join(base_dir, "MyStories")
    
    if not os.path.exists(ROOT_DIR):
        os.makedirs(ROOT_DIR)


# ==========================================
# 智能路径解析器 (核心修复区)
# ==========================================
def resolve_asset_path(base_folder, rel_path):
    """
    智能解析资源路径，完美兼容大模型手滑生成的 'assets_' 前缀，
    同时向下兼容根目录或 assets 子目录的资源读取。
    """
    if not rel_path:
        return ""

    paths_to_try = [
        # 1. 直接拼合 (如果路径原本就是完全正确的)
        os.path.join(base_folder, rel_path),
        # 2. 修复大模型的瑕疵：把起始的 'assets_' 替换成正确的 'assets/'
        os.path.join(base_folder,
                     rel_path.replace("assets_", "assets/", 1) if rel_path.startswith("assets_") else rel_path),
        # 3. 强制去 assets/ 目录下找 (修复 BGM 播放等问题)
        os.path.join(base_folder, "assets", os.path.basename(rel_path)),
        # 4. 强制去根目录下找 (作为最后兜底)
        os.path.join(base_folder, os.path.basename(rel_path))
    ]

    for p in paths_to_try:
        if os.path.isfile(p):
            return p
    return ""


# ==========================================
# 高级确认弹窗
# ==========================================
class CustomConfirmDialog(QDialog):
    def __init__(self, parent=None, title="确认", message="确定执行此操作吗？"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(320, 160)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)

        self.setStyleSheet("""
            QDialog {
                background-color: #2D2D30;
                border: 2px solid #546E7A;
                border-radius: 12px;
            }
            QLabel {
                color: #ECEFF1;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton {
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 20px;
            }
            QPushButton#btn_cancel {
                background-color: #546E7A;
                color: white;
            }
            QPushButton#btn_cancel:hover {
                background-color: #78909C;
            }
            QPushButton#btn_delete {
                background-color: #E53935;
                color: white;
            }
            QPushButton#btn_delete:hover {
                background-color: #EF5350;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 20)

        msg_label = QLabel(message)
        msg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg_label)
        layout.addStretch()

        btn_layout = QHBoxLayout()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("btn_cancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_delete = QPushButton("🗑️ 确认删除")
        self.btn_delete.setObjectName("btn_delete")
        self.btn_delete.clicked.connect(self.accept)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addSpacing(20)
        btn_layout.addWidget(self.btn_delete)

        layout.addLayout(btn_layout)


# ==========================================
# 拖拽组件
# ==========================================
class DropImageLabel(QLabel):
    def __init__(self, placeholder_text, bg_color, story_folder):
        super().__init__(placeholder_text)
        self.story_folder = story_folder
        self.assets_dir = os.path.join(self.story_folder, "assets")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"border: 2px dashed #888; background-color: {bg_color}; color: #ddd; font-weight: bold; border-radius: 5px;")
        self.setAcceptDrops(True)
        self.setMinimumHeight(80)
        self.relative_path = ""

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if file_path.lower().endswith(('.png', '.jpg', '.jpeg')):
                filename = os.path.basename(file_path)
                dest_path = os.path.join(self.assets_dir, filename)
                shutil.copy(file_path, dest_path)
                self.relative_path = f"assets/{filename}"
                self.load_image(self.relative_path)

    def load_image(self, rel_path):
        self.relative_path = rel_path
        if not rel_path:
            self.clear()
            self.setText(self.text())  # 恢复初始文本
            return

        img_full_path = resolve_asset_path(self.story_folder, rel_path)

        if img_full_path:
            pixmap = QPixmap(img_full_path)
            self.setPixmap(pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        else:
            self.setText("图片丢失")


# ==========================================
# 播放器模块 (多角色支持，无入场动画)
# ==========================================
class StoryPlayer(QWidget):
    def __init__(self, story_folder):
        super().__init__()
        self.story_folder = story_folder

        data_file = os.path.join(self.story_folder, "story_data.json")
        with open(data_file, 'r', encoding='utf-8') as f:
            self.story_data = json.load(f)

        self.setWindowTitle(f"正在游玩：{self.story_data.get('title', '未知故事')}")

        screen = QApplication.primaryScreen().geometry()
        self.w, self.h = int(screen.width() * 0.8), int(screen.height() * 0.8)
        self.setFixedSize(self.w, self.h)
        self.setStyleSheet("background-color: #000;")

        self.char_ratio = 0.55  # 全局立绘比例
        self.is_animating = False

        # 1️⃣ 视觉画布
        self.visual_widget = QWidget(self)

        self.bg_label = QLabel(self.visual_widget)
        self.bg_label.setScaledContents(True)

        self.char_labels = {
            "left": QLabel(self.visual_widget),
            "center": QLabel(self.visual_widget),
            "right": QLabel(self.visual_widget)
        }
        for label in self.char_labels.values():
            label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            label.hide()

        # 2️⃣ UI 交互层
        self.ui_widget = QWidget(self)
        self.ui_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.ui_layout = QVBoxLayout(self.ui_widget)
        self.ui_layout.setContentsMargins(80, 40, 80, 40)
        self.ui_layout.addStretch()

        self.choices_layout = QVBoxLayout()
        self.choices_layout.setSpacing(12)
        self.choices_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.ui_layout.addLayout(self.choices_layout)
        self.ui_layout.addSpacing(15)

        self.text_panel = QFrame()
        self.text_panel.setFixedHeight(180)
        self.text_panel.setStyleSheet("""
            QFrame {
                background-color: rgba(245, 245, 245, 220); 
                border-radius: 12px; 
                border: 2px solid rgba(150, 150, 150, 180);
            }
        """)

        panel_layout = QHBoxLayout(self.text_panel)
        panel_layout.setContentsMargins(20, 20, 20, 20)
        panel_layout.setSpacing(25)

        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(136, 136)
        self.avatar_label.setStyleSheet("border: 2px solid #90A4AE; border-radius: 8px; background-color: #FFF;")
        self.avatar_label.setScaledContents(True)
        self.avatar_label.hide()
        panel_layout.addWidget(self.avatar_label)

        text_vbox = QVBoxLayout()
        text_vbox.setSpacing(5)

        self.speaker_label = QLabel("")
        self.speaker_label.setFixedHeight(30)
        self.speaker_label.setStyleSheet(
            "font-size: 22px; font-weight: 900; color: #1565C0; background: transparent; border: none;")

        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.text_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #212121; background: transparent; border: none; line-height: 1.5;")

        text_vbox.addWidget(self.speaker_label)
        text_vbox.addWidget(self.text_label)
        panel_layout.addLayout(text_vbox)

        self.ui_layout.addWidget(self.text_panel)

        # 3️⃣ 透明度转场效果 (保留整体的淡入淡出)
        self.opacity_effect_vis = QGraphicsOpacityEffect(self.visual_widget)
        self.visual_widget.setGraphicsEffect(self.opacity_effect_vis)

        self.opacity_effect_ui = QGraphicsOpacityEffect(self.ui_widget)
        self.ui_widget.setGraphicsEffect(self.opacity_effect_ui)

        self.back_btn = QPushButton("🔙 退出大厅", self)
        self.back_btn.setGeometry(20, 20, 150, 45)
        self.back_btn.setStyleSheet("""
            background-color: rgba(211, 47, 47, 200); 
            color: white; 
            border: 2px solid rgba(255,255,255,150); 
            border-radius: 8px; 
            font-weight: bold; font-size: 15px;
        """)
        self.back_btn.clicked.connect(self.close)

        self.scene_transition = QSequentialAnimationGroup()

        fade_out_group = QParallelAnimationGroup()
        f_out_vis = QPropertyAnimation(self.opacity_effect_vis, b"opacity")
        f_out_vis.setDuration(250)
        f_out_vis.setStartValue(1.0)
        f_out_vis.setEndValue(0.0)
        f_out_ui = QPropertyAnimation(self.opacity_effect_ui, b"opacity")
        f_out_ui.setDuration(250)
        f_out_ui.setStartValue(1.0)
        f_out_ui.setEndValue(0.0)
        fade_out_group.addAnimation(f_out_vis)
        fade_out_group.addAnimation(f_out_ui)

        fade_in_group = QParallelAnimationGroup()
        f_in_vis = QPropertyAnimation(self.opacity_effect_vis, b"opacity")
        f_in_vis.setDuration(350)
        f_in_vis.setStartValue(0.0)
        f_in_vis.setEndValue(1.0)
        f_in_ui = QPropertyAnimation(self.opacity_effect_ui, b"opacity")
        f_in_ui.setDuration(350)
        f_in_ui.setStartValue(0.0)
        f_in_ui.setEndValue(1.0)
        fade_in_group.addAnimation(f_in_vis)
        fade_in_group.addAnimation(f_in_ui)

        self.scene_transition.addAnimation(fade_out_group)
        self.scene_transition.addAnimation(fade_in_group)

        fade_out_group.finished.connect(self.update_visuals)
        self.scene_transition.finished.connect(self.unlock_clicks)

        # 4️⃣ 启动
        self.audio_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.5)
        self.audio_player.setLoops(QMediaPlayer.Loops.Infinite)
        self.play_bgm()

        start_node_id = self.story_data.get("start_node", "scene_1")
        self.pending_node_data = self.story_data.get("nodes", {}).get(start_node_id)

        self.opacity_effect_vis.setOpacity(1.0)
        self.opacity_effect_ui.setOpacity(1.0)

        self.update_visuals()

    def play_bgm(self):
        bgm_path = self.story_data.get("bgm", "")
        if bgm_path:
            full_path = resolve_asset_path(self.story_folder, bgm_path)
            if full_path:
                self.audio_player.setSource(QUrl.fromLocalFile(os.path.abspath(full_path)))
                self.audio_player.play()

    def render_node(self, node_id):
        if self.is_animating: return
        self.pending_node_data = self.story_data["nodes"].get(node_id)
        if not self.pending_node_data: return

        self.is_animating = True
        self.ui_widget.hide()
        self.scene_transition.start()

    def update_visuals(self):
        if not self.pending_node_data: return
        data = self.pending_node_data

        bg_pix = self.get_pixmap(data.get("bg_image", ""))
        if bg_pix and not bg_pix.isNull():
            self.bg_label.setPixmap(bg_pix)
        else:
            self.bg_label.clear()

        # 更新大立绘 (无移动动画，仅伴随转场淡入)
        chars_data = data.get("characters", {})
        if "char_image" in data and not chars_data:
            chars_data = {"center": data["char_image"]}

        target_h = int(self.h * self.char_ratio)

        for pos_key, label in self.char_labels.items():
            char_pix = self.get_pixmap(chars_data.get(pos_key, ""))
            if char_pix and not char_pix.isNull():
                scaled_pix = char_pix.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(scaled_pix)
                label.setFixedSize(scaled_pix.size())
                label.show()
            else:
                label.clear()
                label.hide()

        avatar_pix = self.get_pixmap(data.get("avatar_image", ""))
        if avatar_pix and not avatar_pix.isNull():
            self.avatar_label.setPixmap(avatar_pix)
            self.avatar_label.show()
        else:
            self.avatar_label.hide()

        speaker = data.get("speaker", "").strip()
        if speaker:
            self.speaker_label.setText(f"【{speaker}】")
            self.speaker_label.show()
        else:
            self.speaker_label.hide()
        self.text_label.setText(data.get("text", ""))

        while self.choices_layout.count():
            item = self.choices_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        for choice in data.get("choices", []):
            btn = QPushButton(choice.get("text", "继续"))
            btn.setMinimumWidth(350)
            btn.setStyleSheet("""
                QPushButton { 
                    background-color: rgba(245, 245, 245, 220); 
                    color: #212121; 
                    padding: 12px; 
                    font-size: 18px; 
                    font-weight: bold; 
                    border-radius: 8px; 
                    border: 2px solid #78909C; 
                } 
                QPushButton:hover { 
                    background-color: #E3F2FD; 
                    border: 2px solid #1E88E5; 
                    color: #1E88E5;
                }
            """)
            btn.clicked.connect(lambda checked=False, next_n=choice["next_node"]: self.render_node(next_n))
            self.choices_layout.addWidget(btn)

        # 立刻调整位置，由于取消了滑入动画，它们会直接出现在正确的位置上
        self.adjust_char_position()

    def adjust_char_position(self):
        y = self.h - int(self.h * self.char_ratio)

        if not self.char_labels["center"].isHidden():
            cw = self.char_labels["center"].width()
            self.char_labels["center"].setGeometry((self.w - cw) // 2, y, cw, self.char_labels["center"].height())

        if not self.char_labels["left"].isHidden():
            lw = self.char_labels["left"].width()
            self.char_labels["left"].setGeometry(50, y, lw, self.char_labels["left"].height())

        if not self.char_labels["right"].isHidden():
            rw = self.char_labels["right"].width()
            self.char_labels["right"].setGeometry(self.w - rw - 50, y, rw, self.char_labels["right"].height())

    def unlock_clicks(self):
        self.ui_widget.show()
        self.is_animating = False

    def get_pixmap(self, rel_path):
        img_full = resolve_asset_path(self.story_folder, rel_path)
        if img_full:
            pix = QPixmap(img_full)
            if not pix.isNull(): return pix
        return None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.w, self.h = self.width(), self.height()
        self.visual_widget.setGeometry(0, 0, self.w, self.h)
        self.ui_widget.setGeometry(0, 0, self.w, self.h)
        self.bg_label.setGeometry(0, 0, self.w, self.h)
        self.back_btn.raise_()
        if self.pending_node_data:
            self.adjust_char_position()


# ==========================================
# 编辑器模块：节点卡片盒子
# ==========================================
class SceneNodeWidget(QFrame):
    def __init__(self, node_id, data, canvas, story_folder):
        super().__init__(canvas)
        self.canvas = canvas
        self.node_id = node_id
        self.story_folder = story_folder

        self.setFixedSize(360, 600)
        self.setStyleSheet("""
            SceneNodeWidget { background-color: #2D2D30; border: 2px solid #555; border-radius: 8px; box-shadow: 2px 2px 10px rgba(0,0,0,0.8); }
            SceneNodeWidget:hover { border: 2px solid #64B5F6; }
            QLabel { color: #E0E0E0; font-weight: bold; }
        """)

        self._is_dragging = False
        self._drag_start_pos = QPoint()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # === 顶部标题区域 ===
        header = QHBoxLayout()
        title_text = f"🚩 起点: {node_id}" if node_id == canvas.story_data.get("start_node",
                                                                              "scene_1") else f"🎬 {node_id}"
        title = QLabel(title_text)
        title.setStyleSheet("font-size: 15px; color: #4FC3F7; background: transparent;")
        header.addWidget(title)

        del_btn = QPushButton("🗑️")
        del_btn.setFixedSize(40, 26)
        del_btn.setStyleSheet("background-color: #C62828; color: white; border-radius: 4px; font-weight: bold;")
        del_btn.clicked.connect(self.delete_self)
        header.addWidget(del_btn)
        layout.addLayout(header)

        # === 背景图区域 ===
        bg_label = QLabel("🖼️ 场景背景:")
        bg_label.setStyleSheet("color: #90A4AE; font-size: 12px; font-weight: normal;")
        layout.addWidget(bg_label)

        img_layout1 = QHBoxLayout()
        # 直接把提示语写在占位符文本里
        self.bg_drop = DropImageLabel("拖拽/点击\n上传背景图", "#1A237E", self.story_folder)
        self.bg_drop.load_image(data.get("bg_image", ""))
        img_layout1.addWidget(self.bg_drop)
        layout.addLayout(img_layout1)

        # === 人物立绘区域 ===
        char_label = QLabel("👥 人物立绘:")
        char_label.setStyleSheet("color: #90A4AE; font-size: 12px; font-weight: normal;")
        layout.addWidget(char_label)

        chars_layout = QHBoxLayout()
        # 直接把位置提示写在立绘占位符里
        self.char_left_drop = DropImageLabel("上传\n左立绘", "#004D40", self.story_folder)
        self.char_center_drop = DropImageLabel("上传\n中立绘", "#00695C", self.story_folder)
        self.char_right_drop = DropImageLabel("上传\n右立绘", "#00897B", self.story_folder)

        old_char = data.get("char_image", "")
        chars_data = data.get("characters", {"left": "", "center": old_char, "right": ""})

        self.char_left_drop.load_image(chars_data.get("left", ""))
        self.char_center_drop.load_image(chars_data.get("center", ""))
        self.char_right_drop.load_image(chars_data.get("right", ""))

        chars_layout.addWidget(self.char_left_drop)
        chars_layout.addWidget(self.char_center_drop)
        chars_layout.addWidget(self.char_right_drop)
        layout.addLayout(chars_layout)

        # === 头像与发言人区域 ===
        dialogue_label = QLabel("💬 对话与发言人:")
        dialogue_label.setStyleSheet("color: #90A4AE; font-size: 12px; font-weight: normal;")
        layout.addWidget(dialogue_label)

        img_layout2 = QHBoxLayout()
        # 头像框较小（80x80），字尽量精简
        self.avatar_drop = DropImageLabel("头像", "#3E2723", self.story_folder)
        self.avatar_drop.setFixedSize(80, 80)
        self.avatar_drop.load_image(data.get("avatar_image", ""))
        img_layout2.addWidget(self.avatar_drop)

        speaker_layout = QVBoxLayout()
        speaker_layout.addWidget(QLabel("发言人 (留空即旁白):"))
        self.speaker_input = QLineEdit(data.get("speaker", ""))
        self.speaker_input.setPlaceholderText("例如: 勇者")
        self.speaker_input.setStyleSheet(
            "background-color: #424242; color: #FFF; border: 1px solid #757575; border-radius: 3px; padding: 4px;")
        speaker_layout.addWidget(self.speaker_input)
        img_layout2.addLayout(speaker_layout)
        layout.addLayout(img_layout2)

        # === 台词区域 ===
        self.text_edit = QTextEdit(data.get("text", ""))
        self.text_edit.setPlaceholderText("在这里输入剧情台词...")
        self.text_edit.setMaximumHeight(55)
        self.text_edit.setStyleSheet(
            "background-color: #424242; color: #FFF; border: 1px solid #757575; border-radius: 3px; padding: 2px;")
        layout.addWidget(self.text_edit)

        # === 选项区域 ===
        self.choices_layout = QVBoxLayout()
        self.choice_widgets = []
        layout.addWidget(QLabel("🔀 指向下一个场景 (留空即代表剧终)"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        c_widget = QWidget()
        c_widget.setLayout(self.choices_layout)
        scroll.setWidget(c_widget)
        layout.addWidget(scroll)

        add_choice_btn = QPushButton("+ 添加选项")
        add_choice_btn.setStyleSheet(
            "background-color: #1565C0; color: white; border-radius: 4px; padding: 5px; font-weight: bold;")
        add_choice_btn.clicked.connect(lambda: self.add_choice("", ""))
        layout.addWidget(add_choice_btn)

        self.move(data.get("x", 100), data.get("y", 100))
        for choice in data.get("choices", []):
            self.add_choice(choice.get("text", ""), choice.get("next_node", ""))

    def add_choice(self, text, target):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 2)

        c_text = QLineEdit(text)
        c_text.setPlaceholderText("选项内容")
        c_text.setStyleSheet("background-color: #263238; color: #FFF; border: 1px solid #546E7A; padding: 3px;")

        c_target = QComboBox()
        c_target.setEditable(True)
        c_target.setStyleSheet("background-color: #1B5E20; color: #FFF; border: 1px solid #4CAF50; padding: 3px;")
        c_target.addItems(self.canvas.nodes.keys())
        if target: c_target.setCurrentText(target)
        c_target.currentTextChanged.connect(self.canvas.update)

        del_btn = QPushButton("✖")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet("background-color: #E65100; color: white; font-weight: bold; border-radius: 3px;")
        del_btn.clicked.connect(lambda: self.remove_choice_ui(row))

        row_layout.addWidget(c_text, 3)
        row_layout.addWidget(c_target, 3)
        row_layout.addWidget(del_btn, 1)

        self.choices_layout.addWidget(row)
        self.choice_widgets.append({'row': row, 'text': c_text, 'target': c_target})
        self.canvas.update()

    def remove_choice_ui(self, row_widget):
        row_widget.deleteLater()
        self.choice_widgets = [w for w in self.choice_widgets if w['row'] != row_widget]
        self.canvas.update()

    def update_comboboxes(self):
        keys = list(self.canvas.nodes.keys())
        for w in self.choice_widgets:
            current = w['target'].currentText()
            w['target'].clear()
            w['target'].addItems(keys)
            w['target'].setCurrentText(current)

    def get_data(self):
        choices = []
        for w in self.choice_widgets:
            t = w['text'].text().strip()
            target = w['target'].currentText().strip()
            if t: choices.append({"text": t, "next_node": target})

        return {
            "x": self.pos().x(), "y": self.pos().y(),
            "bg_image": self.bg_drop.relative_path,
            "characters": {
                "left": self.char_left_drop.relative_path,
                "center": self.char_center_drop.relative_path,
                "right": self.char_right_drop.relative_path
            },
            "avatar_image": self.avatar_drop.relative_path,
            "speaker": self.speaker_input.text(),
            "text": self.text_edit.toPlainText(),
            "choices": choices
        }

    def delete_self(self):
        dialog = CustomConfirmDialog(self, "确认", f"确定要删除节点 【{self.node_id}】 吗？\n此操作不可恢复！")
        if dialog.exec():
            self.canvas.remove_node(self.node_id)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start_pos = event.globalPosition().toPoint() - self.pos()
            self.raise_()

    def mouseMoveEvent(self, event):
        if self._is_dragging:
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)
            self.canvas.update()

    def mouseReleaseEvent(self, event):
        self._is_dragging = False


# ==========================================
# 编辑器模块：无限大连线画布
# ==========================================
class NodeCanvas(QWidget):
    def __init__(self, story_data, story_folder):
        super().__init__()
        self.story_data = story_data
        self.story_folder = story_folder
        self.nodes = {}
        self.setFixedSize(5000, 5000)

    def add_node_widget(self, node_id, data=None):
        if data is None:
            data = {"x": 400, "y": 150, "bg_image": "", "characters": {"left": "", "center": "", "right": ""},
                    "avatar_image": "", "speaker": "", "text": "",
                    "choices": []}

        widget = SceneNodeWidget(node_id, data, self, self.story_folder)
        widget.show()
        self.nodes[node_id] = widget
        self.notify_comboboxes()
        self.update()

    def remove_node(self, node_id):
        widget = self.nodes.pop(node_id)
        widget.deleteLater()
        self.notify_comboboxes()
        self.update()

    def notify_comboboxes(self):
        for node in self.nodes.values():
            node.update_comboboxes()

    def get_all_data(self):
        data = {}
        for n_id, widget in self.nodes.items():
            data[n_id] = widget.get_data()
        return data

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))

        grid_pen = QPen(QColor("#2a2a2a"), 1)
        painter.setPen(grid_pen)
        for i in range(0, self.width(), 40):
            painter.drawLine(i, 0, i, self.height())
        for i in range(0, self.height(), 40):
            painter.drawLine(0, i, self.width(), i)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        line_pen = QPen(QColor("#4FC3F7"))
        line_pen.setWidth(3)
        painter.setPen(line_pen)

        for source_id, source_widget in self.nodes.items():
            for choice_dict in source_widget.choice_widgets:
                target_id = choice_dict['target'].currentText().strip()
                if target_id in self.nodes:
                    target_widget = self.nodes[target_id]

                    p1_point = source_widget.geometry().center()
                    p1_point.setY(source_widget.geometry().bottom())
                    p1 = QPointF(p1_point)

                    p2_point = target_widget.geometry().center()
                    p2_point.setY(target_widget.geometry().top())
                    p2 = QPointF(p2_point)

                    painter.drawLine(p1, p2)

                    angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
                    arrow_size = 15
                    p3 = p2 - QPointF(arrow_size * math.cos(angle - math.pi / 6),
                                      arrow_size * math.sin(angle - math.pi / 6))
                    p4 = p2 - QPointF(arrow_size * math.cos(angle + math.pi / 6),
                                      arrow_size * math.sin(angle + math.pi / 6))

                    painter.setBrush(QColor("#4FC3F7"))
                    painter.drawPolygon([p2, p3, p4])


# ==========================================
# 顶层编辑器窗口
# ==========================================
class StoryEditor(QWidget):
    def __init__(self, story_folder):
        super().__init__()
        self.story_folder = story_folder
        self.data_file = os.path.join(self.story_folder, "story_data.json")
        self.assets_dir = os.path.join(self.story_folder, "assets")

        with open(self.data_file, 'r', encoding='utf-8') as f:
            self.story_data = json.load(f)

        self.setWindowTitle(f"正在编辑 - {self.story_data.get('title', '未知故事')}")

        screen = QApplication.primaryScreen().geometry()
        w, h = int(screen.width() * 0.8), int(screen.height() * 0.8)
        self.setFixedSize(w, h)
        self.setStyleSheet("background-color: #000;")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QWidget()
        toolbar.setStyleSheet("background-color: #2b2b2b; color: white;")
        toolbar.setFixedHeight(60)
        tool_layout = QHBoxLayout(toolbar)

        back_btn = QPushButton("🔙 返回大厅")
        back_btn.setStyleSheet(
            "background-color: #546E7A; color: white; padding: 6px 15px; font-weight: bold; border-radius: 4px;")
        back_btn.clicked.connect(self.close)
        tool_layout.addWidget(back_btn)
        tool_layout.addStretch()

        tool_layout.addWidget(QLabel("🎵 BGM:"))
        self.bgm_input = QLineEdit(self.story_data.get("bgm", ""))
        self.bgm_input.setReadOnly(True)
        self.bgm_input.setFixedWidth(150)
        self.bgm_input.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555;")
        tool_layout.addWidget(self.bgm_input)

        bgm_btn = QPushButton("导入")
        bgm_btn.setStyleSheet("background-color: #5c5c5c; padding: 5px;")
        bgm_btn.clicked.connect(self.upload_bgm)
        tool_layout.addWidget(bgm_btn)

        tool_layout.addWidget(QLabel("   📜 简介:"))
        self.desc_input = QLineEdit(self.story_data.get("desc", ""))
        self.desc_input.setFixedWidth(200)
        self.desc_input.setStyleSheet("background-color: #1e1e1e; border: 1px solid #555;")
        tool_layout.addWidget(self.desc_input)

        add_node_btn = QPushButton("➕ 新建场景")
        add_node_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; padding: 6px 15px; font-weight: bold; border-radius: 4px; margin-left: 20px;")
        add_node_btn.clicked.connect(self.add_node)
        tool_layout.addWidget(add_node_btn)

        save_btn = QPushButton("💾 保存项目")
        save_btn.setStyleSheet(
            "background-color: #FBC02D; color: black; padding: 6px 15px; font-weight: bold; border-radius: 4px;")
        save_btn.clicked.connect(self.save_project)
        tool_layout.addWidget(save_btn)

        main_layout.addWidget(toolbar)

        self.scroll_area = QScrollArea()
        self.canvas = NodeCanvas(self.story_data, self.story_folder)
        self.scroll_area.setWidget(self.canvas)
        main_layout.addWidget(self.scroll_area)

    def upload_bgm(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择背景音乐", "", "音频文件 (*.mp3 *.wav *.ogg)")
        if file_path:
            filename = os.path.basename(file_path)
            dest_path = os.path.join(self.assets_dir, filename)
            shutil.copy(file_path, dest_path)
            self.bgm_input.setText(f"assets/{filename}")
            QMessageBox.information(self, "导入成功", f"音乐 [{filename}] 已成功导入！")

    def add_node(self):
        new_id = f"scene_{len(self.canvas.nodes) + 1}"
        while new_id in self.canvas.nodes:
            new_id += "_new"
        self.canvas.add_node_widget(new_id)

    def save_project(self):
        self.story_data["desc"] = self.desc_input.text()
        self.story_data["bgm"] = self.bgm_input.text()
        self.story_data["nodes"] = self.canvas.get_all_data()

        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.story_data, f, ensure_ascii=False, indent=4)
        QMessageBox.information(self, "打包成功", "所有场景节点与连线已安全保存！")


class StoryHub(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视觉小说引擎 - 故事大厅")
        self.resize(980, 680)
        self.setStyleSheet("background-color: #F0F4F8; color: #2C3E50;")

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(25, 25, 25, 25)
        main_layout.setSpacing(25)

        # ================= 左侧：故事库与操作区 =================
        left_panel = QVBoxLayout()

        lib_label = QLabel("📚 我的故事库")
        lib_label.setStyleSheet(
            "font-size: 20px; font-weight: 900; color: #1A237E; margin-bottom: 5px; letter-spacing: 1px;")
        left_panel.addWidget(lib_label)

        self.story_list = QListWidget()
        self.story_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.story_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.story_list.setMovement(QListWidget.Movement.Static)
        self.story_list.setSpacing(15)
        self.story_list.setIconSize(QSize(130, 170))
        self.story_list.setGridSize(QSize(140, 200))

        self.story_list.setStyleSheet("""
            QListWidget {
                background-color: transparent; 
                border: none;
                outline: none;
            }
            QListWidget::item {
                background-color: transparent;
                border-radius: 12px;
                padding-top: 10px;
            }
            QListWidget::item:hover {
                background-color: #E3F2FD;
            }
            QListWidget::item:selected {
                background-color: #BBDEFB;
                border: 2px solid #2196F3;
                border-radius: 12px;
            }
        """)
        self.story_list.currentItemChanged.connect(self.update_details)
        left_panel.addWidget(self.story_list)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)

        self.new_btn = QPushButton("✨ 新建故事")
        self.new_btn.setStyleSheet("""
            QPushButton { background-color: #1E88E5; color: white; padding: 14px; font-weight: bold; font-size: 15px; border-radius: 10px; }
            QPushButton:hover { background-color: #1565C0; }
        """)
        self.new_btn.clicked.connect(self.create_new_story)

        self.edit_btn = QPushButton("🛠️ 编辑故事")
        self.edit_btn.setStyleSheet("""
            QPushButton { background-color: #F4511E; color: white; padding: 14px; font-weight: bold; font-size: 15px; border-radius: 10px; }
            QPushButton:hover { background-color: #E64A19; }
            QPushButton:disabled { background-color: #FFAB91; color: #FBE9E7; }
        """)
        self.edit_btn.clicked.connect(self.open_editor)
        self.edit_btn.setEnabled(False)

        self.ai_btn = QPushButton("🤖 AI 剧本")
        self.ai_btn.setStyleSheet("""
            QPushButton { background-color: #8E24AA; color: white; padding: 14px; font-weight: bold; font-size: 15px; border-radius: 10px; }
            QPushButton:hover { background-color: #7B1FA2; }
        """)
        self.ai_btn.clicked.connect(self.open_ai_generator)

        btn_layout.addWidget(self.new_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.ai_btn)

        left_panel.addLayout(btn_layout)
        main_layout.addLayout(left_panel, 5)

        # ================= 右侧：信息与游玩区 (高级悬浮卡片) =================
        right_card = QFrame()
        right_card.setStyleSheet("background-color: #FFFFFF; border-radius: 16px;")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(0, 0, 0, 25))
        right_card.setGraphicsEffect(shadow)

        right_panel = QVBoxLayout(right_card)
        right_panel.setContentsMargins(30, 30, 30, 30)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(320, 200)
        self.cover_label.setStyleSheet(
            "background-color: #ECEFF1; border: 2px dashed #B0BEC5; border-radius: 14px; color: #78909C; font-weight: bold;")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setText("🖼️ 暂无封面\n(从首场景背景提取)")
        right_panel.addWidget(self.cover_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.title_label = QLabel("未选择任何故事")
        self.title_label.setStyleSheet("font-size: 24px; font-weight: 900; color: #2C3E50; margin-top: 20px;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_panel.addWidget(self.title_label)

        self.desc_label = QLabel("请在左侧点击一本故事书查看详情。")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet(
            "font-size: 14px; color: #607D8B; background-color: #F8F9FA; padding: 15px; border-radius: 10px; line-height: 1.6;")
        self.desc_label.setMinimumHeight(120)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        right_panel.addWidget(self.desc_label)

        right_panel.addStretch()

        self.play_btn = QPushButton("▶️ 开始游玩")
        self.play_btn.setStyleSheet("""
            QPushButton { 
                background-color: #43A047; 
                color: white; 
                height: 60px; 
                font-size: 18px; 
                font-weight: 900; 
                border-radius: 14px; 
                letter-spacing: 2px;
            }
            QPushButton:hover { background-color: #388E3C; }
            QPushButton:disabled { background-color: #A5D6A7; color: #E8F5E9; }
        """)
        self.play_btn.clicked.connect(self.open_player)
        self.play_btn.setEnabled(False)
        right_panel.addWidget(self.play_btn)

        main_layout.addWidget(right_card, 3)

        self.refresh_list()

    def get_book_icon(self, title):
        pixmap = QPixmap(130, 170)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawRoundedRect(18, 15, 100, 140, 6, 6)

        book_rect = QRect(15, 10, 100, 140)
        path = QPainterPath()
        path.addRoundedRect(book_rect, 6, 6)

        book_img_path = get_resource_path("book_cover.png")

        if os.path.exists(book_img_path):
            original_pix = QPixmap(book_img_path)
            scaled_pix = original_pix.scaled(100, 140, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                             Qt.TransformationMode.SmoothTransformation)
            x_offset = 15 + (100 - scaled_pix.width()) // 2
            y_offset = 10 + (140 - scaled_pix.height()) // 2

            painter.setClipPath(path)
            painter.drawPixmap(x_offset, y_offset, scaled_pix)
            painter.setClipping(False)

            painter.setBrush(QColor(0, 0, 0, 70))
            painter.drawRect(15, 10, 8, 140)
            painter.setBrush(QColor(255, 255, 255, 40))
            painter.drawRect(23, 10, 2, 140)
            painter.setBrush(QColor(255, 255, 255, 120))
            painter.drawRect(112, 10, 3, 140)

            painter.setPen(QPen(QColor(0, 0, 0, 50), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(book_rect, 6, 6)
        else:
            painter.setBrush(QColor("#42A5F5"))
            painter.setPen(QPen(QColor("#1565C0"), 1))
            painter.drawPath(path)
            painter.setBrush(QColor("#1E88E5"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(15, 10, 10, 140)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRect(112, 12, 3, 136)

        font = painter.font()
        font.setBold(True)
        font.setPointSize(12)
        painter.setFont(font)
        text_rect = QRect(25, 30, 80, 100)
        draw_flags = int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap)

        painter.setPen(QColor(0, 0, 0, 180))
        painter.drawText(text_rect.translated(1, 1), draw_flags, title)
        painter.drawText(text_rect.translated(2, 2), draw_flags, title)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(text_rect, draw_flags, title)
        painter.end()
        return QIcon(pixmap)

    def open_ai_generator(self):
        dialog = AIGeneratorDialog(self)
        if dialog.exec():
            self.refresh_list()

    def refresh_list(self):
        self.story_list.clear()
        if not os.path.exists(ROOT_DIR): return
        for d in os.listdir(ROOT_DIR):
            if os.path.isdir(os.path.join(ROOT_DIR, d)):
                if os.path.exists(os.path.join(ROOT_DIR, d, "story_data.json")):
                    from PySide6.QtWidgets import QListWidgetItem
                    item = QListWidgetItem(d)
                    item.setIcon(self.get_book_icon(d))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
                    self.story_list.addItem(item)

    def update_details(self, item):
        if not item:
            self.title_label.setText("未选择任何故事")
            self.desc_label.setText("请在左侧点击一本故事书查看详情。")
            self.cover_label.clear()
            self.cover_label.setStyleSheet(
                "background-color: #ECEFF1; border: 2px dashed #B0BEC5; border-radius: 14px; color: #78909C; font-weight: bold;")
            self.cover_label.setText("🖼️ 暂无封面\n(从首场景背景提取)")
            self.edit_btn.setEnabled(False)
            self.play_btn.setEnabled(False)
            return

        folder_name = item.text()
        story_folder = os.path.join(ROOT_DIR, folder_name)
        data_file = os.path.join(story_folder, "story_data.json")
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.title_label.setText(data.get("title", folder_name))
            self.desc_label.setText(data.get("desc", "暂无简介..."))

            start_node = data.get("start_node", "scene_1")
            nodes = data.get("nodes", {})
            first_node_data = nodes.get(start_node, {})
            bg_rel_path = first_node_data.get("bg_image", "")

            cover_pixmap = None
            if bg_rel_path:
                img_full = resolve_asset_path(story_folder, bg_rel_path)
                if img_full:
                    cover_pixmap = QPixmap(img_full)

            if cover_pixmap and not cover_pixmap.isNull():
                scaled_pix = cover_pixmap.scaled(self.cover_label.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                 Qt.TransformationMode.SmoothTransformation)
                self.cover_label.setStyleSheet("border: none; border-radius: 14px;")

                rounded_pixmap = QPixmap(self.cover_label.size())
                rounded_pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(rounded_pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor("black"))
                painter.drawRoundedRect(rounded_pixmap.rect(), 14, 14)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)

                x_offset = (scaled_pix.width() - self.cover_label.width()) // 2
                y_offset = (scaled_pix.height() - self.cover_label.height()) // 2
                painter.drawPixmap(0, 0, scaled_pix, x_offset, y_offset, self.cover_label.width(),
                                   self.cover_label.height())
                painter.end()

                self.cover_label.setPixmap(rounded_pixmap)
            else:
                self.cover_label.clear()
                self.cover_label.setStyleSheet(
                    "background-color: #ECEFF1; border: 2px dashed #B0BEC5; border-radius: 14px; color: #78909C; font-weight: bold;")
                self.cover_label.setText("🖼️ 首个场景暂无背景")

            self.edit_btn.setEnabled(True)
            self.play_btn.setEnabled(True)
        except Exception:
            self.desc_label.setText("读取存档失败！")

    def create_new_story(self):
        text, ok = QInputDialog.getText(self, "新建故事", "请输入你要创作的故事名称:")
        if ok and text:
            folder_name = text.strip()
            invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
            for char in invalid_chars: folder_name = folder_name.replace(char, '')
            if not folder_name: return

            story_folder = os.path.join(ROOT_DIR, folder_name)
            if os.path.exists(story_folder):
                QMessageBox.warning(self, "错误", "该故事名称已存在！")
                return

            os.makedirs(story_folder)
            os.makedirs(os.path.join(story_folder, "assets"))

            default_data = {
                "title": folder_name,
                "desc": "这是一个刚刚创建的全新故事...",
                "bgm": "",
                "start_node": "scene_1",
                "nodes": {
                    "scene_1": {
                        "x": 400, "y": 200,
                        "bg_image": "",
                        "characters": {"left": "", "center": "", "right": ""},
                        "avatar_image": "",
                        "speaker": "系统",
                        "text": "故事从此开始...",
                        "choices": []
                    }
                }
            }
            with open(os.path.join(story_folder, "story_data.json"), 'w', encoding='utf-8') as f:
                json.dump(default_data, f, ensure_ascii=False, indent=4)

            self.refresh_list()
            items = self.story_list.findItems(folder_name, Qt.MatchFlag.MatchExactly)
            if items: self.story_list.setCurrentItem(items[0])

    def open_editor(self):
        item = self.story_list.currentItem()
        if item:
            story_folder = os.path.join(ROOT_DIR, item.text())
            self.editor = StoryEditor(story_folder)
            self.editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.editor.show()

    def open_player(self):
        item = self.story_list.currentItem()
        if item:
            story_folder = os.path.join(ROOT_DIR, item.text())
            self.player = StoryPlayer(story_folder)
            self.player.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.player.show()


if __name__ == "__main__":
    init_root_structure()
    app = QApplication(sys.argv)
    window = StoryHub()
    window.show()
    sys.exit(app.exec())