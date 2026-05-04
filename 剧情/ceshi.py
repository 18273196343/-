import json
import os
import shutil
import base64
import requests
import io
import re
import time
from datetime import datetime
from wsgiref.handlers import format_date_time
from time import mktime
import hashlib
import hmac
from urllib.parse import urlencode
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QTextEdit, QMessageBox, QStackedLayout,
                               QDialog, QProgressBar, QFormLayout, QFileDialog, QScrollArea, QFrame,
                               QGraphicsDropShadowEffect, QCheckBox, QComboBox, QGroupBox, QTabWidget, QLineEdit,
                               QGridLayout)
from PySide6.QtGui import QColor, QFont, QCursor
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from openai import OpenAI

# 【重要提示】此功能需要安装 'rembg' 库用于自动抠图
# pip install rembg pillow
import rembg
from PIL import Image

# --- 全局根目录 ---
ROOT_DIR = "./MyStories"

# ！！！请确保填写你的 APP_ID、API_KEY 和 API_SECRET ！！！
XUNFEI_APP_ID = "26ae8827"
XUNFEI_API_KEY1 = "9627a917d7d3f37aa0b7eb8d0aff9dba:ZDNkYjJhMWUwZWNlNTdkMWY0OWUwYmRj"  # 只保留冒号前面的 APIKey
XUNFEI_API_KEY2 = "9627a917d7d3f37aa0b7eb8d0aff9dba"  # 只保留冒号前面的 APIKey
XUNFEI_API_SECRET = "ZDNkYjJhMWUwZWNlNTdkMWY0OWUwYmRj"  # 对应 APIKey 冒号后面的 APISecret


# ==========================================
# 讯飞图像生成 API 鉴权工具函数
# ==========================================
class AssembleHeaderException(Exception):
    def __init__(self, msg):
        self.message = msg


class Url:
    def __init__(this, host, path, schema):
        this.host = host
        this.path = path
        this.schema = schema


def sha256base64(data):
    """calculate sha256 and encode to base64"""
    sha256 = hashlib.sha256()
    sha256.update(data)
    digest = base64.b64encode(sha256.digest()).decode(encoding='utf-8')
    return digest


def parse_url(requset_url):
    stidx = requset_url.index("://")
    host = requset_url[stidx + 3:]
    schema = requset_url[:stidx + 3]
    edidx = host.index("/")
    if edidx <= 0:
        raise AssembleHeaderException("invalid request url:" + requset_url)
    path = host[edidx:]
    host = host[:edidx]
    u = Url(host, path, schema)
    return u


def assemble_ws_auth_url(requset_url, method="GET", api_key="", api_secret=""):
    """生成鉴权url"""
    u = parse_url(requset_url)
    host = u.host
    path = u.path
    now = datetime.now()
    date = format_date_time(mktime(now.timetuple()))
    signature_origin = "host: {}\ndate: {}\n{} {} HTTP/1.1".format(host, date, method, path)
    signature_sha = hmac.new(api_secret.encode('utf-8'), signature_origin.encode('utf-8'),
                             digestmod=hashlib.sha256).digest()
    signature_sha = base64.b64encode(signature_sha).decode(encoding='utf-8')
    authorization_origin = 'api_key="%s", algorithm="%s", headers="%s", signature="%s"' % (
        api_key, "hmac-sha256", "host date request-line", signature_sha)
    authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')
    values = {
        "host": host,
        "date": date,
        "authorization": authorization
    }
    return requset_url + "?" + urlencode(values)


def get_tti_body(appid, text, width=512, height=512):
    """生成讯飞图像生成请求body体"""
    body = {
        "header": {
            "app_id": appid,
            "uid": "123456789"
        },
        "parameter": {
            "chat": {
                "domain": "general",
                "width": width,
                "height": height,
                "temperature": 0.5,
                "max_tokens": 4096
            }
        },
        "payload": {
            "message": {
                "text": [
                    {
                        "role": "user",
                        "content": text
                    }
                ]
            }
        }
    }
    return body

class AIWorker(QThread):
    chunk_received = Signal(str)
    finished = Signal(str, str)

    def __init__(self, prompt_text, is_blind_box=False, genre=""):
        super().__init__()
        self.prompt_text = prompt_text
        self.is_blind_box = is_blind_box
        self.genre = genre

    def run(self):
        start_time = time.time()

        try:
            client = OpenAI(
                api_key=XUNFEI_API_KEY1,
                base_url="https://spark-api-open.xf-yun.com/x2/",
            )

            # 【核心修改】增加图片资源限制20张的规则
            system_prompt = """你是一个顶级的视觉小说（Visual Novel）游戏金牌编剧。
你的任务是：根据用户的要求，创作一个有深度、有分支选择的多场景完整剧本,字数不超过250字。

【极为严格的 JSON 规范与限制】
1. 场景(nodes)数量严格限制：必须在 2 到 10 个之间，绝对不能超过 10 个,场景scene绝对不能超过10个！
2. 绝对不允许在 JSON 字符串内部使用真实的换行符！...
3. 绝对不允许在 JSON 字符串内部使用未转义的双引号！...
4. 所有 "choices" 中的 "next_node" 必须对应 "nodes" 中真实存在的键名。禁止出现悬空引用！
5. 整个剧本的结局节点（choices 为 [] 的节点）控制在 1~3 个之间。

【⚠️ 结局节点规范（极其重要）】
1. 如果场景是故事的结局/终点，"choices" 必须是空数组 []，绝不允许指向不存在的节点。
2. 结局节点的 "text" 必须写得完整、有收束感，让玩家明确感受到"故事到此结束"。
3. 结局节点建议加上 "is_ending": true。

【⚠️ 图片资源限制（极其重要）】
1. 整个剧本中，所有场景的图片资源（包括 bg_image、avatar_image、characters 中的 left/center/right）加起来最多只能使用 20 张不同的图片！
2. 为了节省图片数量，请尽量复用同一张图片！例如：
   - 同一个角色的立绘和头像可以用同一张图（路径相同）
   - 同一个角色在不同场景出现，使用相同路径
   - 相似场景的背景可以复用
3. 如果角色位置（left/center/right）没有角色，必须留空字符串 ""，不要生成无用图片。
4. 所有图片路径的文件名必须是【纯英文且具有描述性】，如 "assets/hero_standing.png"。
5. 路径必须以 "assets/" 开头，以 ".png" 结尾。

【⚠️ 语言与格式要求（极其重要）】
1. 必须使用【中文】的字段："title"（故事中文标题）、"desc"（故事中文简介）、"speaker"（角色名）、"text"（具体台词内容）、"choices"里的"text"（玩家选项）。
2. 必须使用【纯英文】的字段："visual_style"、"bgm"、所有的图片路径名（如bg_image, avatar_image等），并且路径必须以 "assets/" 开头。
3. 为了防止生图API封禁，"visual_style" 和图片文件名【绝对不能】包含任何恐怖、血腥、暴力、上吊、尸体、怪物等敏感词汇。请用温和词汇平替！如用 "suspense" 代替恐怖；用 "shadowy figure" 代替怪物。
4. 每个场景的 text 必须生动且不少于 30 个中文字。

【JSON 结构要求示范】
{"title":"中文标题","desc":"中文故事简介","visual_style":"[合规的纯英文视觉描述]","bgm":"[留空或填写英文音频名]","start_node":"scene_1","nodes":{"scene_1":{"x":400,"y":150,"bg_image":"assets/[英文描述].png","characters":{"left":"","center":"assets/[英文描述].png","right":""},"avatar_image":"assets/[英文描述].png","speaker":"中文说话人","text":"中文剧情内容","choices":[{"text":"中文玩家选项","next_node":"scene_2"}]}}}"""

            if self.is_blind_box:
                user_content = f"我不需要提供大纲，请直接帮我构思并编写一个极其精彩的【{self.genre}】题材的视觉小说剧本！自由发挥你的想象力！"
            else:
                user_content = f"请将这个大纲扩充为剧本：\n{self.prompt_text}"

            print(f"\n{'=' * 60}")
            print(f"🚀 [AIWorker] 开始生成剧本")
            print(f"⏰ 开始时间: {datetime.now().strftime('%H:%M:%S')}")
            print(f"📝 模式: {'盲盒' if self.is_blind_box else '自定义'}")
            if self.is_blind_box:
                print(f"🎲 题材: {self.genre}")
            print(f"📊 用户输入长度: {len(user_content)} 字符")
            print(f"{'=' * 60}")

            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                model="spark-x",
                stream=True,
                user="123456",
                max_tokens=8192,
            )

            full_result = ""
            chunk_count = 0

            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    text_chunk = chunk.choices[0].delta.content
                    full_result += text_chunk
                    chunk_count += 1
                    self.chunk_received.emit(text_chunk)

            # 【核心修改】获取 Token 消耗统计 - 流式模式下需要在循环外获取
            # 注意：流式模式下 usage 可能在 response 的最后一块中
            usage = None
            if hasattr(response, 'usage') and response.usage:
                usage = response.usage
            # 如果流式模式没有直接返回 usage，尝试从最后一块获取
            elif hasattr(chunk, 'usage') and chunk.usage:
                usage = chunk.usage

            end_time = time.time()
            elapsed = end_time - start_time

            print(f"\n{'=' * 60}")
            print(f"✅ [AIWorker] 剧本生成完成")
            print(f"⏰ 结束时间: {datetime.now().strftime('%H:%M:%S')}")
            print(f"⏱️  总耗时: {elapsed:.2f} 秒 ({elapsed / 60:.2f} 分钟)")
            print(f"📦 接收数据块: {chunk_count} 个")

            # 【核心修改】打印 Token 消耗
            if usage:
                print(f"💰 Token 消耗统计:")
                print(f"   - 提示词(Prompt): {usage.prompt_tokens} tokens")
                print(f"   - 生成内容(Completion): {usage.completion_tokens} tokens")
                print(f"   - 总计(Total): {usage.total_tokens} tokens")
                if elapsed > 0:
                    print(f"   - 平均速度: {usage.completion_tokens / elapsed:.1f} tokens/秒")
            else:
                print(f"⚠️  无法获取 Token 统计信息（流式模式可能不返回）")
                print(f"💡 建议：如需精确统计，可在非流式模式下测试")

            print(f"📄 生成内容长度: {len(full_result)} 字符")
            print(f"{'=' * 60}")

            match = re.search(r'\{.*\}', full_result, re.DOTALL)
            clean_json = match.group(0) if match else full_result

            open_braces = clean_json.count('{')
            close_braces = clean_json.count('}')
            if open_braces > close_braces:
                clean_json += '}' * (open_braces - close_braces)
            elif close_braces > open_braces:
                clean_json = clean_json[:-(close_braces - open_braces)]

            self.finished.emit("success", clean_json.strip())

        except Exception as e:
            end_time = time.time()
            elapsed = end_time - start_time
            print(f"\n❌ [AIWorker] 生成失败")
            print(f"⏱️  已耗时: {elapsed:.2f} 秒")
            print(f"💥 错误: {str(e)}")
            self.finished.emit("error", str(e))


# ==========================================
# 线程 2：AI 图像生成 (限制最多20张 + 时间统计)
# ==========================================# ==========================================
# 线程 2：AI 图像生成 (讯飞鉴权格式 + 限制最多20张 + 时间统计)
# ==========================================
class ImageGenWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(str, str)

    def __init__(self, final_data, images_to_generate, target_folder):
        super().__init__()
        self.final_data = final_data
        # 【限制】最多只处理20张图片
        self.images_to_generate = list(images_to_generate)[:20]
        self.target_folder = target_folder

    def sanitize_prompt(self, text):
        banned_dict = {
            'horror': 'suspense', 'blood': 'red liquid', 'hanged': 'floating', 'dead': 'sleeping',
            'death': 'slumber', 'monster': 'shadowy figure', 'creature': 'mysterious entity',
            'flesh': 'soft texture', 'kill': 'defeat', 'suicide': 'despair', 'terrified': 'surprised',
            'gore': 'messy', 'scary': 'mysterious', 'creepy': 'unusual'
        }
        res = text.lower()
        for bad, good in banned_dict.items():
            res = res.replace(bad, good)
        return res

    def run(self):
        total = len(self.images_to_generate)

        print(f"\n{'=' * 60}")
        print(f"🎨 [ImageGenWorker] 开始批量生成图片")
        print(f"⏰ 开始时间: {datetime.now().strftime('%H:%M:%S')}")
        print(f"📊 待生成图片总数: {total} 张 (上限20张)")
        print(f"{'=' * 60}")

        if total == 0:
            print("⚠️  无需生成图片")
            self.finished.emit("success", "无需生成图片")
            return

        # 【核心修改】使用讯飞鉴权方式
        host = 'http://spark-api.cn-huabei-1.xf-yun.com/v2.1/tti'

        success_count = 0
        fail_count = 0
        total_img_time = 0

        try:
            for idx, img_path in enumerate(self.images_to_generate):
                img_start_time = time.time()
                filename = os.path.basename(img_path)

                raw_prompt = self._get_specific_prompt_for_img(img_path, filename)
                image_prompt = self.sanitize_prompt(raw_prompt)

                self.progress.emit(int(idx / total * 100), f"正在绘制: {filename} ({idx + 1}/{total})")

                print(f"\n--- [{idx + 1}/{total}] 生成图片: {filename} ---")
                print(f"⏰ 开始: {datetime.now().strftime('%H:%M:%S')}")
                print(f"📝 Prompt: {image_prompt[:100]}...")

                # 【核心修改】使用讯飞鉴权URL和请求格式
                url = assemble_ws_auth_url(
                    host,
                    method='POST',
                    api_key=XUNFEI_API_KEY2,
                    api_secret=XUNFEI_API_SECRET
                )
                content = get_tti_body(XUNFEI_APP_ID, image_prompt, width=768, height=768)

                print(f"🔗 请求URL: {url[:80]}...")
                # 【增强】发送请求并添加详细调试
                response = requests.post(
                    url,
                    json=content,
                    headers={'content-type': "application/json"},
                    timeout=60
                )

                print(f"📡 HTTP状态码: {response.status_code}")

                # 【增强】处理非JSON响应（如401/403 HTML页面）
                try:
                    response_data = response.json()
                except json.JSONDecodeError as e:
                    print(f"❌ 响应解析失败: {str(e)}")
                    print(f"📄 原始响应内容(前500字): {response.text[:500]}")
                    fail_count += 1
                    continue

                img_end_time = time.time()
                img_elapsed = img_end_time - img_start_time
                total_img_time += img_elapsed

                # 【增强】更完善的错误码处理
                header = response_data.get("header", {})
                code = header.get("code")

                if code == 0:
                    # 【核心修改】讯飞返回格式：payload.choices.text[0].content
                    text_list = response_data["payload"]["choices"]["text"]
                    imageContent = text_list[0]
                    base64_img = imageContent["content"]
                    img_bytes = base64.b64decode(base64_img)
                    img_type = self._get_img_type(img_path)

                    try:
                        if img_type != "background":
                            print(f"✂️  执行抠图: {filename}")
                            img_bytes_after_removal = rembg.remove(img_bytes, post_process_mask=True)
                            output_path = os.path.join(self.target_folder, filename)
                            pil_image = Image.open(io.BytesIO(img_bytes_after_removal))
                            pil_image.save(output_path, "PNG")
                            print(f"✅ 完成: {filename} (抠图+保存)")
                        else:
                            with open(os.path.join(self.target_folder, filename), "wb") as f:
                                f.write(img_bytes)
                            print(f"✅ 完成: {filename} (背景图原图保存)")

                        success_count += 1

                    except Exception as e:
                        print(f"⚠️  抠图失败，降级保存原图: {str(e)}")
                        with open(os.path.join(self.target_folder, filename), "wb") as f:
                            f.write(img_bytes)
                        success_count += 1

                else:
                    # 【增强】更详细的错误信息提取
                    err_msg = header.get("message", "未知错误")
                    err_code = code if code is not None else "未知代码"
                    sid = header.get("sid", "无SID")

                    print(f"❌ 失败: {filename}")
                    print(f"   错误码: {err_code}")
                    print(f"   错误信息: {err_msg}")
                    print(f"   请求ID: {sid}")

                    # 常见错误码提示
                    if err_code == 11200:
                        print(f"   💡 提示: 功能未授权，请检查appid是否正确，并确保已开通TTI服务")
                    elif err_code == 11201:
                        print(f"   💡 提示: 该APPID的每日交互次数超过限制")
                    elif response.status_code == 401:
                        print(f"   💡 提示: 鉴权失败，请检查 API_KEY 和 API_SECRET 是否正确")
                        print(f"   💡 当前API_KEY: {XUNFEI_API_KEY2[:10]}...")
                    elif response.status_code == 403:
                        print(f"   💡 提示: 时钟偏移校验失败，请检查系统时间是否正确（误差需<5分钟）")

                    fail_count += 1

                print(f"⏱️  本张耗时: {img_elapsed:.2f} 秒")
                print(f"📊 进度: {success_count}成功 / {fail_count}失败 / {total}总计")

            # 打印最终统计
            print(f"\n{'=' * 60}")
            print(f"🎉 [ImageGenWorker] 批量生成完成")
            print(f"⏰ 结束时间: {datetime.now().strftime('%H:%M:%S')}")
            print(f"⏱️  总耗时: {total_img_time:.2f} 秒 ({total_img_time / 60:.2f} 分钟)")
            print(f"📊 统计: {success_count} 张成功, {fail_count} 张失败")
            if success_count > 0:
                print(f"🚀 平均速度: {total_img_time / success_count:.2f} 秒/张")
            print(f"{'=' * 60}")

            self.progress.emit(100, "全部图像绘制与抠图完成！")

            if fail_count > 0:
                self.finished.emit("error", f"部分失败: {success_count}成功, {fail_count}失败")
            else:
                self.finished.emit("success", f"所有 {success_count} 张图片已就绪")

        except Exception as e:
            print(f"\n❌ [ImageGenWorker] 全局异常: {str(e)}")
            self.finished.emit("error", f"生图/抠图异常: {str(e)}")

    def _get_img_type(self, target_img_path):
        for node_id, node_data in self.final_data.get("nodes", {}).items():
            if not isinstance(node_data, dict): continue
            if node_data.get("bg_image") == target_img_path:
                return "background"
            elif node_data.get("avatar_image") == target_img_path:
                return "avatar"
            elif "characters" in node_data and isinstance(node_data["characters"], dict):
                for pos, char_path in node_data["characters"].items():
                    if char_path == target_img_path: return "character"
        return "unknown"

    def _get_specific_prompt_for_img(self, target_img_path, filename):
        global_style_en = self.final_data.get("visual_style", "masterpiece, highly detailed, vivid colors")
        name_keywords = os.path.splitext(filename)[0].replace('_', ' ')
        img_type = self._get_img_type(target_img_path)
        if img_type == "background":
            return f"A scenery of {name_keywords}, wide angle background, {global_style_en}, no humans, empty environment, immersive landscape."
        elif img_type == "character":
            return f"A full body concept art portrait of {name_keywords}, art style of ({global_style_en}), standing in the center, looking at viewer, flat solid plain grey background, completely empty background, zero shadows on background."
        elif img_type == "avatar":
            return f"A close-up face portrait of {name_keywords}, expressive face, detailed eyes, art style of ({global_style_en}), flat solid plain grey background, completely empty background."
        return f"{name_keywords}, {global_style_en}"


class AIGeneratorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 故事创作中心 - 剧本与美术一键生成")
        self.resize(980, 820)
        self.setStyleSheet("background-color: #F0F4F8; color: #2C3E50; font-family: 'Microsoft YaHei';")

        self.uploaded_bgm_path = ""
        self.final_data = {}
        self.image_ui_refs = {}
        self.is_blind_box = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(25, 25, 25, 25)

        self.card_frame = QFrame()
        self.card_frame.setStyleSheet("background-color: #FFFFFF; border-radius: 16px;")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setYOffset(5)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.card_frame.setGraphicsEffect(shadow)

        self.card_layout = QVBoxLayout(self.card_frame)
        self.card_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.addWidget(self.card_frame)

        self.stacked_layout = QStackedLayout()
        self.card_layout.addLayout(self.stacked_layout)

        self._init_page_input()
        self._init_page_loading_text()
        self._init_page_preview()
        self._init_page_image_config()
        self._init_page_loading_img()

    def _init_page_input(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #CFD8DC; border-radius: 8px; background: white; }
            QTabBar::tab { background: #ECEFF1; color: #546E7A; padding: 12px 25px; font-weight: bold; font-size: 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px;}
            QTabBar::tab:selected { background: #FFFFFF; color: #1565C0; border-bottom: 2px solid #1565C0; }
        """)

        # ----- 模式1：大纲定制生成 -----
        tab_custom = QWidget()
        layout_custom = QVBoxLayout(tab_custom)
        layout_custom.addWidget(QLabel("📝 输入故事梗概 (背景、角色、结局走向)"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("例如：在赛博朋克都市中，赏金猎人零号接到了一份神秘委托...")
        self.prompt_edit.setStyleSheet(
            "background-color: #F8F9FA; border: 1px solid #E0E0E0; border-radius: 6px; padding: 10px; font-size: 14px;")
        layout_custom.addWidget(self.prompt_edit)
        btn_custom = QPushButton("✍️ 开始构思剧本")
        btn_custom.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_custom.setStyleSheet("""
            QPushButton { background-color: #1976D2; color: white; padding: 14px; font-weight: bold; border-radius: 8px; font-size: 16px; }
            QPushButton:hover { background-color: #1565C0; }
        """)
        btn_custom.clicked.connect(lambda: self.start_generation(is_blind_box=False))
        layout_custom.addWidget(btn_custom)
        tabs.addTab(tab_custom, "🎯 大纲定制")

        # ----- 模式2：灵感盲盒生成 -----
        tab_blind = QWidget()
        layout_blind = QVBoxLayout(tab_blind)
        lbl_blind = QLabel("🎲 懒人专属：选择题材，AI 自动为你编织整个世界！")
        lbl_blind.setStyleSheet("font-size: 15px; color: #E65100; font-weight: bold; margin-top: 10px;")
        layout_blind.addWidget(lbl_blind)

        self.genre_combo = QComboBox()
        self.genre_combo.addItems(
            ["传统武侠", "中式悬疑(规则怪谈)", "赛博朋克科幻", "西方奇幻冒险", "现代都市恋爱", "末日废土求生"])
        self.genre_combo.setStyleSheet(
            "padding: 12px; font-size: 16px; border: 2px solid #FFCA28; border-radius: 6px; background: white;")
        layout_blind.addWidget(self.genre_combo)
        layout_blind.addStretch()

        btn_blind = QPushButton("🎁 抽取盲盒剧本")
        btn_blind.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_blind.setStyleSheet("""
            QPushButton { background-color: #FF8F00; color: white; padding: 15px; font-weight: bold; font-size: 16px; border-radius: 8px; }
            QPushButton:hover { background-color: #FF6F00; }
        """)
        btn_blind.clicked.connect(lambda: self.start_generation(is_blind_box=True))
        layout_blind.addWidget(btn_blind)
        tabs.addTab(tab_blind, "🎲 灵感盲盒")

        layout.addWidget(tabs)

        # BGM 控制
        bgm_layout = QHBoxLayout()
        self.bgm_checkbox = QCheckBox("🎵 AI 自动配置 BGM 名称")
        self.bgm_checkbox.setChecked(True)
        self.bgm_checkbox.setStyleSheet("font-weight: bold; color: #455A64;")
        self.bgm_upload_btn = QPushButton("📁 上传本地 BGM")
        self.bgm_upload_btn.setStyleSheet(
            "background: #ECEFF1; border: 1px solid #CFD8DC; padding: 6px 15px; border-radius: 6px; font-weight: bold;")
        self.bgm_upload_btn.clicked.connect(self.browse_bgm)
        self.bgm_label = QLabel("未选择")
        self.bgm_label.setStyleSheet("color: #78909C; font-weight: bold;")

        bgm_layout.addWidget(self.bgm_checkbox)
        bgm_layout.addStretch()
        bgm_layout.addWidget(self.bgm_upload_btn)
        bgm_layout.addWidget(self.bgm_label)
        layout.addLayout(bgm_layout)

        self.page_input = page
        self.stacked_layout.addWidget(page)

    def browse_bgm(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择背景音乐", "", "Audio (*.mp3 *.wav *.ogg)")
        if file_path:
            self.uploaded_bgm_path = file_path
            self.bgm_label.setText(f"✅ {os.path.basename(file_path)}")
            self.bgm_label.setStyleSheet("color: #2E7D32; font-weight: bold;")
            self.bgm_checkbox.setChecked(False)

    def _init_page_loading_text(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.txt_loading_lbl = QLabel("🧠 AI 编剧正在构思剧本，请稍候...")
        self.txt_loading_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #1976D2;")
        self.txt_loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.txt_loading_lbl)

        self.txt_progress_bar = QProgressBar()
        self.txt_progress_bar.setFixedHeight(15)
        self.txt_progress_bar.setRange(0, 100)
        self.txt_progress_bar.setStyleSheet(
            "QProgressBar { border: none; background-color: #E0E0E0; border-radius: 7px; text-align: center; color: transparent; } QProgressBar::chunk { background-color: #29B6F6; border-radius: 7px; }")
        layout.addWidget(self.txt_progress_bar)

        self.stream_console = QTextEdit()
        self.stream_console.setReadOnly(True)
        self.stream_console.setStyleSheet(
            "background-color: #1E1E1E; color: #00E676; font-family: Consolas; font-size: 13px; border-radius: 8px; padding: 10px;")
        layout.addWidget(self.stream_console)

        self.page_loading_text = page
        self.stacked_layout.addWidget(page)

    def _init_page_preview(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        header_lbl = QLabel("✅ 第1步/共2步：剧本已生成！请核对与修改剧情文本")
        header_lbl.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #2E7D32; margin-bottom: 5px; padding: 10px; background-color: #E8F5E9; border-radius: 8px;")
        layout.addWidget(header_lbl)

        self.editor_tabs = QTabWidget()
        self.editor_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #CFD8DC; border-radius: 8px; background: white; }
            QTabBar::tab { background: #ECEFF1; color: #546E7A; padding: 10px 20px; font-weight: bold; font-size: 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px;}
            QTabBar::tab:selected { background: #FFFFFF; color: #2E7D32; border-bottom: 2px solid #2E7D32; }
        """)

        # 1. 智能剧情表单
        self.visual_editor_widget = QWidget()
        self.visual_layout = QVBoxLayout(self.visual_editor_widget)

        form_top = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setStyleSheet(
            "font-size: 16px; font-weight: bold; padding: 8px; border: 1px solid #CFD8DC; border-radius: 4px;")
        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(65)
        self.desc_edit.setStyleSheet("padding: 8px; border: 1px solid #CFD8DC; border-radius: 4px;")
        form_top.addRow("📜 故事标题:", self.title_edit)
        form_top.addRow("📝 故事简介:", self.desc_edit)
        self.visual_layout.addLayout(form_top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        scroll.setWidget(self.scroll_content)
        self.visual_layout.addWidget(scroll)

        self.editor_tabs.addTab(self.visual_editor_widget, "🖥️ 智能剧情表单 (推荐)")

        # 2. JSON 源码
        self.json_editor = QTextEdit()
        self.json_editor.setStyleSheet(
            "background-color: #2D2D30; color: #CE9178; font-family: Consolas; font-size: 14px;")
        self.editor_tabs.addTab(self.json_editor, "⚙️ JSON 源码 (高级)")

        layout.addWidget(self.editor_tabs)
        self.editor_tabs.currentChanged.connect(self.sync_editors)

        self.confirm_btn = QPushButton("➡️ 下一步：确认剧情，配置美术资源")
        self.confirm_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.confirm_btn.setStyleSheet("""
            QPushButton { background-color: #1976D2; color: white; height: 50px; font-weight: bold; font-size: 16px; border-radius: 12px; }
            QPushButton:hover { background-color: #1565C0; }
        """)
        self.confirm_btn.clicked.connect(self.on_preview_confirmed)
        layout.addWidget(self.confirm_btn)

        self.page_preview = page
        self.stacked_layout.addWidget(page)

    def _init_page_image_config(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        header_lbl = QLabel("✅ 第2步/共2步：剧情已确认，请配置对应的美术资源")
        header_lbl.setStyleSheet("""
            font-size: 18px; 
            font-weight: bold; 
            color: #D84315; 
            margin-bottom: 5px;
            padding: 12px;
            background-color: #FBE9E7;
            border-radius: 8px;
        """)
        layout.addWidget(header_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        self.img_config_content = QWidget()
        self.img_config_layout = QVBoxLayout(self.img_config_content)
        self.img_config_layout.setContentsMargins(0, 10, 0, 10)
        scroll.setWidget(self.img_config_content)
        layout.addWidget(scroll)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 10, 0, 0)

        self.back_btn = QPushButton("⬅️ 返回修改剧情")
        self.back_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_btn.setStyleSheet("""
            QPushButton { background-color: #78909C; color: white; height: 50px; font-weight: bold; font-size: 15px; border-radius: 12px; padding: 0 20px; }
            QPushButton:hover { background-color: #607D8B; }
        """)
        self.back_btn.clicked.connect(lambda: self.stacked_layout.setCurrentWidget(self.page_preview))

        self.start_pack_btn = QPushButton("🚀 开始生成项目与绘图")
        self.start_pack_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.start_pack_btn.setStyleSheet("""
            QPushButton { background-color: #43A047; color: white; height: 50px; font-weight: bold; font-size: 16px; border-radius: 12px; }
            QPushButton:hover { background-color: #388E3C; }
        """)
        self.start_pack_btn.clicked.connect(self.on_image_config_confirmed)

        btn_layout.addWidget(self.back_btn)
        btn_layout.addWidget(self.start_pack_btn, 1)
        layout.addLayout(btn_layout)

        self.page_image_config = page
        self.stacked_layout.addWidget(page)

    def _init_page_loading_img(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.img_status_label = QLabel("🎨 AI 画师正在全神贯注为您绘制场景与角色...")
        self.img_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_status_label.setStyleSheet("font-size: 22px; font-weight: 900; color: #D84315; margin-bottom: 25px;")
        layout.addWidget(self.img_status_label)

        self.img_progress_bar = QProgressBar()
        self.img_progress_bar.setFixedHeight(25)
        self.img_progress_bar.setStyleSheet(
            "QProgressBar { border: none; background-color: #E0E0E0; border-radius: 12px; text-align: center; color: white; font-weight: bold; font-size: 14px;} QProgressBar::chunk { background-color: #FF7043; border-radius: 12px; }")
        layout.addWidget(self.img_progress_bar)

        self.page_loading_img = page
        self.stacked_layout.addWidget(page)

    def _get_img_type(self, target_img_path):
        for node_id, node_data in self.final_data.get("nodes", {}).items():
            if not isinstance(node_data, dict): continue
            if node_data.get("bg_image") == target_img_path:
                return "background"
            elif node_data.get("avatar_image") == target_img_path:
                return "avatar"
            elif "characters" in node_data and isinstance(node_data["characters"], dict):
                for pos, char_path in node_data["characters"].items():
                    if char_path == target_img_path: return "character"
        return "unknown"

    def sync_editors(self, index):
        if index == 1:
            self.sync_visual_data_to_json()
            self.json_editor.setText(json.dumps(self.final_data, ensure_ascii=False, indent=4))
        elif index == 0:
            try:
                new_data = json.loads(self.json_editor.toPlainText())
                self.sanitize_json_paths(new_data)
                self.final_data = new_data
                self.build_visual_editor()
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "JSON 错误", f"当前 JSON 存在语法错误，无法生成可视化表单，请先修复！\n{str(e)}")
                self.editor_tabs.blockSignals(True)
                self.editor_tabs.setCurrentIndex(1)
                self.editor_tabs.blockSignals(False)

    def start_generation(self, is_blind_box=False):
        self.is_blind_box = is_blind_box

        if is_blind_box:
            genre = self.genre_combo.currentText()
            final_prompt = ""
        else:
            base_prompt = self.prompt_edit.toPlainText().strip()
            if not base_prompt:
                return QMessageBox.warning(self, "提示", "请输入故事大纲！")
            genre = ""
            final_prompt = base_prompt

        if self.bgm_checkbox.isChecked() and not self.uploaded_bgm_path:
            final_prompt += "\n\n【附加要求】：请在 JSON 的 'bgm' 字段中填写一个符合本故事氛围的英文音频文件名（如 assets/mysterious.mp3）。"
        else:
            final_prompt += "\n\n【附加要求】：JSON的 'bgm' 字段请直接留空。"

        self.stream_console.clear()
        self.txt_progress_bar.setValue(0)
        self.stacked_layout.setCurrentWidget(self.page_loading_text)

        self.sim_progress = 0
        self.progress_timer = QTimer(self)
        self.progress_timer.timeout.connect(self.update_simulated_progress)
        self.progress_timer.start(300)

        self.txt_worker = AIWorker(final_prompt, is_blind_box=is_blind_box, genre=genre)
        self.txt_worker.chunk_received.connect(self.on_text_chunk)
        self.txt_worker.finished.connect(self.on_text_generated)
        self.txt_worker.start()

    def update_simulated_progress(self):
        if self.sim_progress < 95:
            self.sim_progress += 1
            self.txt_progress_bar.setValue(self.sim_progress)

    def on_text_chunk(self, chunk):
        cursor = self.stream_console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.stream_console.setTextCursor(cursor)

    def sanitize_json_paths(self, data):
        if data.get("bgm") and data["bgm"].startswith("assets_"):
            data["bgm"] = data["bgm"].replace("assets_", "assets/", 1)
        for node in data.get("nodes", {}).values():
            if not isinstance(node, dict): continue
            if node.get("bg_image") and node["bg_image"].startswith("assets_"):
                node["bg_image"] = node["bg_image"].replace("assets_", "assets/", 1)
            if node.get("avatar_image") and node["avatar_image"].startswith("assets_"):
                node["avatar_image"] = node["avatar_image"].replace("assets_", "assets/", 1)
            if "characters" in node and isinstance(node["characters"], dict):
                for pos in ["left", "center", "right"]:
                    if node["characters"].get(pos) and node["characters"][pos].startswith("assets_"):
                        node["characters"][pos] = node["characters"][pos].replace("assets_", "assets/", 1)

    def on_text_generated(self, status, result):
        self.progress_timer.stop()
        self.txt_progress_bar.setValue(100)

        if status == "error":
            QMessageBox.critical(self, "剧本生成失败", result)
            self.stacked_layout.setCurrentWidget(self.page_input)
            return

        try:
            self.final_data = json.loads(result)
            self.sanitize_json_paths(self.final_data)

            # ========== 【新增】结局节点自动修复与校验 ==========
            self._fix_and_validate_story_data()
            # =====================================================

            self.editor_tabs.blockSignals(True)
            self.json_editor.setText(json.dumps(self.final_data, ensure_ascii=False, indent=4))
            self.editor_tabs.setCurrentIndex(0)
            self.build_visual_editor()
            self.editor_tabs.blockSignals(False)

        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "需要手动修复", f"JSON 存在瑕疵：{str(e)}\n已切换至源码模式，请修复后再继续。")
            self.editor_tabs.blockSignals(True)
            self.json_editor.setText(result)
            self.editor_tabs.setCurrentIndex(1)
            self.editor_tabs.blockSignals(False)

        self.stacked_layout.setCurrentWidget(self.page_preview)

    def _fix_and_validate_story_data(self):
        """
        自动修复剧本数据中的常见问题：
        1. 移除指向不存在节点的悬空 choices
        2. 清理空 choices 后自动标记 is_ending
        3. 如果没有结局节点，强制把最后一个场景设为结局
        4. 打印修复日志
        """
        nodes = self.final_data.get("nodes", {})
        if not nodes:
            return

        valid_node_ids = set(nodes.keys())
        ending_count = 0
        fix_log = []

        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue

            original_choices = node.get("choices", [])
            if not isinstance(original_choices, list):
                original_choices = []
                fix_log.append(f"[{node_id}] choices 不是列表，已重置为空")

            # 过滤掉指向不存在节点的 choice
            valid_choices = []
            for choice in original_choices:
                if isinstance(choice, dict) and choice.get("next_node") in valid_node_ids:
                    valid_choices.append(choice)
                else:
                    bad_target = choice.get("next_node", "N/A") if isinstance(choice, dict) else "非法格式"
                    fix_log.append(f"[{node_id}] 移除悬空指向 -> {bad_target}")

            # 如果清理后 choices 为空，标记为结局
            if not valid_choices:
                node["choices"] = []
                node["is_ending"] = True
                ending_count += 1
                if original_choices:  # 原本有 choice 但被清理掉了
                    fix_log.append(f"[{node_id}] 所有指向均无效，已强制设为结局节点")
            else:
                node["choices"] = valid_choices
                # 如果原本标记了 is_ending 但现在有有效 choices，移除标记
                if node.get("is_ending"):
                    del node["is_ending"]
                    fix_log.append(f"[{node_id}] 存在有效分支，移除 is_ending 标记")

        # 兜底：如果整个剧本一个结局都没有，强制最后一个场景为结局
        if ending_count == 0 and nodes:
            last_node_id = list(nodes.keys())[-1]
            nodes[last_node_id]["choices"] = []
            nodes[last_node_id]["is_ending"] = True
            fix_log.append(f"[{last_node_id}] 剧本无结局，已强制设为结局")

        # 打印修复日志
        if fix_log:
            print(f"\n{'=' * 60}")
            print("🔧 [剧本自动修复报告]")
            for msg in fix_log:
                print(f"   {msg}")
            print(f"{'=' * 60}")
        else:
            print("✅ [剧本校验通过] 未发现悬空节点或格式问题")

    def build_visual_editor(self):
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.title_edit.setText(self.final_data.get("title", ""))
        self.desc_edit.setText(self.final_data.get("desc", ""))

        scene_group = QGroupBox("🎬 剧情台词核对与修改")
        scene_group.setStyleSheet(
            "QGroupBox { border: 1px solid #B0BEC5; border-radius: 6px; margin-top: 15px; padding-top: 15px; background: #FAFAFA;} QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #1565C0; font-weight: bold; font-size:14px; }")
        scene_layout = QVBoxLayout(scene_group)
        self.scene_ui_refs = {}

        nodes = self.final_data.get("nodes", {})
        for node_id, node_data in nodes.items():
            if not isinstance(node_data, dict):
                continue

            node_widget = QWidget()
            form = QFormLayout(node_widget)

            speaker_edit = QLineEdit(node_data.get("speaker", ""))
            speaker_edit.setStyleSheet(
                "background: #FFF; border: 1px solid #CFD8DC; border-radius: 4px; padding: 6px; font-weight: bold;")

            text_edit = QTextEdit(node_data.get("text", ""))
            text_edit.setMaximumHeight(70)
            text_edit.setStyleSheet(
                "background: #FFF; border: 1px solid #CFD8DC; border-radius: 4px; padding: 6px; font-size: 13px;")

            form.addRow(f"[{node_id}] 发言人:", speaker_edit)
            form.addRow(f"[{node_id}] 台词:", text_edit)

            scene_layout.addWidget(node_widget)
            self.scene_ui_refs[node_id] = {"speaker": speaker_edit, "text": text_edit}

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Sunken)
            line.setStyleSheet("background-color: #ECEFF1;")
            scene_layout.addWidget(line)

        self.scroll_layout.addWidget(scene_group)
        self.scroll_layout.addStretch()

    def extract_images_from_data(self, data):
        """提取图片路径，去重，并检查20张限制"""
        imgs = set()
        for node in data.get("nodes", {}).values():
            if not isinstance(node, dict): continue
            if isinstance(node.get("bg_image"), str) and node.get("bg_image"):
                imgs.add(node["bg_image"])
            if isinstance(node.get("avatar_image"), str) and node.get("avatar_image"):
                imgs.add(node.get("avatar_image"))

            chars = node.get("characters")
            if isinstance(chars, dict):
                for pos in ["left", "center", "right"]:
                    val = chars.get(pos)
                    if isinstance(val, str) and val:
                        imgs.add(val)

        unique_count = len(imgs)
        print(f"\n📊 [图片统计] 剧本中引用图片: {unique_count} 张 (已去重)")

        # 检查是否超过20张
        if unique_count > 20:
            print(f"⚠️  警告：图片数量 {unique_count} 张超过20张限制！")

        return imgs

    def sync_visual_data_to_json(self):
        self.final_data["title"] = self.title_edit.text().strip()
        self.final_data["desc"] = self.desc_edit.toPlainText().strip()

        for node_id, refs in self.scene_ui_refs.items():
            if node_id in self.final_data.get("nodes", {}):
                self.final_data["nodes"][node_id]["speaker"] = refs["speaker"].text().strip()
                self.final_data["nodes"][node_id]["text"] = refs["text"].toPlainText().strip()

    def on_preview_confirmed(self):
        if self.editor_tabs.currentIndex() == 0:
            self.sync_visual_data_to_json()
        else:
            try:
                self.final_data = json.loads(self.json_editor.toPlainText())
                self.sanitize_json_paths(self.final_data)
            except json.JSONDecodeError as e:
                return QMessageBox.warning(self, "JSON 格式错误", f"源码修改有误，请检查！\n{e}")

        required_imgs = self.extract_images_from_data(self.final_data)

        if not required_imgs:
            self.start_packaging(imgs_to_ai=[], local_copies=[])
            return

        self.populate_image_config(required_imgs)
        self.stacked_layout.setCurrentWidget(self.page_image_config)

    def populate_image_config(self, required_imgs):
        while self.img_config_layout.count():
            item = self.img_config_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.image_ui_refs.clear()
        img_count = len(required_imgs)

        summary_frame = QFrame()
        summary_frame.setStyleSheet("""
            QFrame {
                background-color: #F4FBFF;
                border: 1px solid #BBDEFB;
                border-radius: 12px;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 20))
        shadow.setOffset(0, 3)
        summary_frame.setGraphicsEffect(shadow)

        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(20, 20, 20, 20)
        summary_layout.setSpacing(15)

        title_lbl = QLabel(
            f"📦 剧本解析完毕！本项目共计需要绘制 <span style='color:#E65100; font-size:18px;'>{img_count}</span> 张美术资产：")
        title_lbl.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #1565C0; border: none; background: transparent;")
        summary_layout.addWidget(title_lbl)

        # 显示20张限制警告
        if img_count > 20:
            warn_lbl = QLabel("⚠️ 警告：超过20张限制！建议返回修改剧本，复用图片资源。")
            warn_lbl.setStyleSheet(
                "color: #E53935; font-size: 14px; font-weight: bold; background: #FFEBEE; padding: 8px; border-radius: 4px;")
            summary_layout.addWidget(warn_lbl)

        categories = {"background": [], "character": [], "avatar": [], "unknown": []}
        for path in sorted(list(required_imgs)):
            itype = self._get_img_type(path)
            categories[itype].append(os.path.basename(path))

        def add_category_ui(icon, title, items, color_hex, bg_hex):
            if not items: return

            cat_layout = QHBoxLayout()
            cat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

            cat_label = QLabel(f"{icon} {title}:")
            cat_label.setFixedWidth(100)
            cat_label.setStyleSheet(
                f"font-size: 15px; font-weight: bold; color: {color_hex}; border: none; background: transparent; padding-top: 5px;")
            cat_layout.addWidget(cat_label)

            tags_container = QWidget()
            tags_container.setStyleSheet("background: transparent;")
            tags_layout = QGridLayout(tags_container)
            tags_layout.setContentsMargins(0, 0, 0, 0)
            tags_layout.setSpacing(8)

            row, col = 0, 0
            max_cols = 3

            for item in items:
                tag = QLabel(item)
                tag.setStyleSheet(f"""
                    background-color: {bg_hex};
                    color: {color_hex};
                    border: 1px solid {color_hex};
                    border-radius: 8px;
                    padding: 5px 12px;
                    font-size: 13px;
                    font-weight: bold;
                """)
                tags_layout.addWidget(tag, row, col)
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1

            tags_layout.setColumnStretch(max_cols, 1)
            cat_layout.addWidget(tags_container, 1)
            summary_layout.addLayout(cat_layout)

        add_category_ui("🏞️", "场景背景", categories["background"], "#2E7D32", "#E8F5E9")
        add_category_ui("🧍", "角色立绘", categories["character"], "#E65100", "#FFF3E0")
        add_category_ui("👤", "角色头像", categories["avatar"], "#6A1B9A", "#F3E5F5")
        add_category_ui("❓", "其他资源", categories["unknown"], "#37474F", "#ECEFF1")

        self.img_config_layout.addWidget(summary_frame)

        if self.is_blind_box:
            blind_info = QLabel("✨ 当前为【盲盒模式】，上述所有资产将由 AI 大模型全自动为您绘制生成。")
            blind_info.setStyleSheet(
                "color: #E65100; font-size: 15px; font-weight: bold; margin-top: 15px; padding: 10px; background: #FFF8E1; border-radius: 6px; border: 1px dashed #FFCA28;")
            blind_info.setWordWrap(True)
            self.img_config_layout.addWidget(blind_info)
        else:
            action_group = QGroupBox("⚙️ 请选择美术生成方式")
            action_group.setStyleSheet(
                "QGroupBox { border: 2px dashed #90A4AE; border-radius: 8px; margin-top: 20px; padding-top: 20px; background: #F8F9FA;} QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #455A64; font-weight: bold; font-size: 15px; }")
            action_layout = QVBoxLayout(action_group)

            global_mode_layout = QHBoxLayout()
            global_mode_layout.addWidget(
                QLabel("图片来源:", styleSheet="font-weight:bold; color:#37474F; font-size:15px;"))

            self.global_art_mode_combo = QComboBox()
            self.global_art_mode_combo.addItems(["🤖 全部交由 AI 自动绘制 (推荐)", "📁 全部由我手动配置本地图片"])
            self.global_art_mode_combo.setStyleSheet(
                "background: white; padding: 10px; border-radius: 6px; border: 2px solid #1976D2; font-weight: bold; color: #1976D2; font-size:14px;")
            global_mode_layout.addWidget(self.global_art_mode_combo)
            global_mode_layout.addStretch()
            action_layout.addLayout(global_mode_layout)

            self.manual_upload_container = QWidget()
            manual_upload_layout = QVBoxLayout(self.manual_upload_container)
            manual_upload_layout.setContentsMargins(0, 15, 0, 0)
            self.manual_upload_container.hide()

            for img_path in sorted(list(required_imgs)):
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 5, 0, 5)

                lbl_name = QLabel(f"🖼️ {os.path.basename(img_path)}")
                lbl_name.setFixedWidth(200)
                lbl_name.setStyleSheet("font-weight: bold; color: #37474F; font-size: 14px;")

                btn_browse = QPushButton("📁 浏览文件")
                btn_browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn_browse.setStyleSheet("""
                    QPushButton { background: #CFD8DC; color: #263238; border-radius: 6px; padding: 8px 15px; font-weight: bold; }
                    QPushButton:hover { background: #B0BEC5; }
                """)

                lbl_path = QLabel("尚未选择 (必填)")
                lbl_path.setStyleSheet("color: #E53935; font-size: 13px; font-weight: bold;")

                def make_browse_func(path_label, img_key):
                    def browse():
                        file_path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "Images (*.png *.jpg *.jpeg)")
                        if file_path:
                            self.image_ui_refs[img_key]["local_path"] = file_path
                            path_label.setText(f"✅ {os.path.basename(file_path)}")
                            path_label.setStyleSheet("color: #2E7D32; font-weight: bold;")

                    return browse

                btn_browse.clicked.connect(make_browse_func(lbl_path, img_path))
                self.image_ui_refs[img_path] = {"local_path": ""}

                row_layout.addWidget(lbl_name)
                row_layout.addWidget(btn_browse)
                row_layout.addWidget(lbl_path)
                row_layout.addStretch()

                manual_upload_layout.addWidget(row_widget)

            action_layout.addWidget(self.manual_upload_container)

            def on_global_mode_changed(text):
                if "手动" in text:
                    self.manual_upload_container.show()
                    self.global_art_mode_combo.setStyleSheet(
                        "background: white; padding: 10px; border-radius: 6px; border: 2px solid #D84315; font-weight: bold; color: #D84315; font-size:14px;")
                else:
                    self.manual_upload_container.hide()
                    self.global_art_mode_combo.setStyleSheet(
                        "background: white; padding: 10px; border-radius: 6px; border: 2px solid #1976D2; font-weight: bold; color: #1976D2; font-size:14px;")

            self.global_art_mode_combo.currentTextChanged.connect(on_global_mode_changed)
            self.img_config_layout.addWidget(action_group)

        self.img_config_layout.addStretch()

    def on_image_config_confirmed(self):
        imgs_to_ai = []
        local_copies = []

        if self.is_blind_box:
            imgs_to_ai = list(self.extract_images_from_data(self.final_data))
        else:
            global_mode = self.global_art_mode_combo.currentText()
            if "手动" in global_mode:
                for img_path, refs in self.image_ui_refs.items():
                    local_path = refs["local_path"]
                    if not local_path or not os.path.exists(local_path):
                        return QMessageBox.warning(self, "操作阻止",
                                                   f"请注意！\n【{os.path.basename(img_path)}】尚未配置本地文件！\n\n请点击“浏览文件”补充完整，或将上方模式切换回“AI 自动绘制”。")
                    local_copies.append((local_path, img_path))
            else:
                imgs_to_ai = list(self.extract_images_from_data(self.final_data))

        self.start_packaging(imgs_to_ai, local_copies)

    def start_packaging(self, imgs_to_ai, local_copies):
        title = self.final_data.get("title", "AI新故事")
        for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
            title = title.replace(char, '')

        self.story_folder = os.path.join(ROOT_DIR, title)
        counter = 1
        orig_title = title
        while os.path.exists(self.story_folder):
            title = f"{orig_title}_{counter}"
            self.story_folder = os.path.join(ROOT_DIR, title)
            counter += 1

        os.makedirs(self.story_folder)
        self.assets_dir = os.path.join(self.story_folder, "assets")
        os.makedirs(self.assets_dir)

        if self.uploaded_bgm_path and os.path.exists(self.uploaded_bgm_path):
            bgm_filename = os.path.basename(self.uploaded_bgm_path)
            shutil.copy(self.uploaded_bgm_path, os.path.join(self.assets_dir, bgm_filename))
            self.final_data["bgm"] = f"assets/{bgm_filename}"

        for local_src, json_target_path in local_copies:
            ext = os.path.splitext(local_src)[1]
            target_filename = os.path.splitext(os.path.basename(json_target_path))[0] + ext

            new_target_rel_path = f"assets/{target_filename}"
            self.replace_path_in_json(json_target_path, new_target_rel_path)

            shutil.copy(local_src, os.path.join(self.assets_dir, target_filename))

        if not imgs_to_ai:
            self.finish_and_save()
        else:
            # 【限制】去重后限制20张
            unique_imgs = list(dict.fromkeys(imgs_to_ai))[:20]

            if len(imgs_to_ai) > 20:
                print(f"⚠️  图片数量从 {len(imgs_to_ai)} 张限制为 20 张")

            self.stacked_layout.setCurrentWidget(self.page_loading_img)
            self.img_worker = ImageGenWorker(self.final_data, unique_imgs, self.assets_dir)
            self.img_worker.progress.connect(self.update_img_progress)
            self.img_worker.finished.connect(self.on_images_generated)
            self.img_worker.start()

    def replace_path_in_json(self, old_path, new_path):
        for node in self.final_data.get("nodes", {}).values():
            if not isinstance(node, dict): continue
            if node.get("bg_image") == old_path: node["bg_image"] = new_path
            if node.get("avatar_image") == old_path: node["avatar_image"] = new_path
            if "characters" in node and isinstance(node["characters"], dict):
                for pos in ["left", "center", "right"]:
                    if node["characters"].get(pos) == old_path: node["characters"][pos] = new_path

    def update_img_progress(self, percent, text):
        self.img_progress_bar.setValue(percent)
        self.img_status_label.setText(text)

    def on_images_generated(self, status, msg):
        if status == "error":
            QMessageBox.warning(self, "生图瑕疵", f"部分图片生成或抠图失败。\n{msg}")
        self.finish_and_save()

    def finish_and_save(self):
        with open(os.path.join(self.story_folder, "story_data.json"), 'w', encoding='utf-8') as f:
            json.dump(self.final_data, f, ensure_ascii=False, indent=4)

        # 统计最终项目信息
        assets_dir = os.path.join(self.story_folder, "assets")
        asset_count = len(os.listdir(assets_dir)) if os.path.exists(assets_dir) else 0

        print(f"\n{'=' * 60}")
        print(f"📦 [项目打包完成]")
        print(f"📁 项目路径: {self.story_folder}")
        print(f"📊 场景数量: {len(self.final_data.get('nodes', {}))} 个")
        print(f"🖼️  资源文件: {asset_count} 个")
        print(f"{'=' * 60}")

        QMessageBox.information(self, "大功告成",
                                f"🎉 游戏项目【{os.path.basename(self.story_folder)}】构建完毕！\n"
                                f"📊 共 {len(self.final_data.get('nodes', {}))} 个场景，{asset_count} 个资源文件。\n"
                                f"请返回大厅游玩或编辑。")
        self.accept()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    if not os.path.exists(ROOT_DIR):
        os.makedirs(ROOT_DIR)

    dialog = AIGeneratorDialog()
    dialog.show()
    sys.exit(app.exec())