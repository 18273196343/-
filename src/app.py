import os
import json
import shutil
import threading
import time
import base64
import requests
import io
import re
from flask import Flask, render_template, request, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename
from openai import OpenAI

# 可选：rembg 自动抠图（服务器装不上会自动跳过）
try:
    import rembg
    from PIL import Image

    REMBG_AVAILABLE = True
except ImportError:
    REMBG_AVAILABLE = False

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# 路径配置
ROOT_DIR = os.path.join(os.path.dirname(__file__), 'http', 'stories')
TEMP_DIR = os.path.join(os.path.dirname(__file__), 'temp_uploads')
os.makedirs(TEMP_DIR, exist_ok=True)

# API 密钥（生产环境建议用环境变量）
XUNFEI_APP_ID = os.environ.get("XUNFEI_APP_ID", "26ae8827")
XUNFEI_API_KEY1 = os.environ.get("XUNFEI_API_KEY", "9627a917d7d3f37aa0b7eb8d0aff9dba:ZDNkYjJhMWUwZWNlNTdkMWY0OWUwYmRj")
XUNFEI_API_KEY2 = os.environ.get("XUNFEI_API_KEY", "9627a917d7d3f37aa0b7eb8d0aff9dba")
XUNFEI_API_SECRET = os.environ.get("XUNFEI_API_SECRET", "ZDNkYjJhMWUwZWNlNTdkMWY0OWUwYmRj")

# ==================== 任务管理 ====================
_jobs = {}
_jobs_lock = threading.Lock()


class JobStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self.progress = 0
        self.status_text = "等待中"
        self.result = None
        self.error = None
        self.finished = False
        self.data = {}
        self.stream_text = ""

    def update(self, progress=None, status=None):
        with self._lock:
            if progress is not None:
                self.progress = progress
            if status is not None:
                self.status_text = status

    def set_result(self, result):
        with self._lock:
            self.result = result
            self.finished = True

    def set_error(self, error):
        with self._lock:
            self.error = error
            self.finished = True

    def reset(self):
        with self._lock:
            self.progress = 0
            self.status_text = "等待中"
            self.result = None
            self.error = None
            self.finished = False

    def to_dict(self):
        with self._lock:
            d = {
                "progress": self.progress,
                "status_text": self.status_text,
                "finished": self.finished,
                "error": self.error,
                "has_result": self.result is not None,
                "stream_text": self.stream_text
            }
            if self.result and isinstance(self.result, dict):
                d.update({k: v for k, v in self.result.items() if
                          k in ('script', 'images', 'success', 'message', 'warning')})
            return d


# ==================== 工具函数 ====================
def init_root():
    os.makedirs(ROOT_DIR, exist_ok=True)


def safe_story_name(name):
    """保留中文的故事名安全化（secure_filename会删掉所有中文字符）"""
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*', '\0']:
        name = name.replace(char, '')
    name = name.strip().strip('.')
    return name


def safe_upload_filename(filename):
    """保留中文的上传文件名安全化（secure_filename会删掉中文）"""
    for char in ['<', '>', ':', '"', '/', '\\', '|', '?', '*', '\0']:
        filename = filename.replace(char, '')
    filename = filename.lstrip('. ').rstrip()
    if not filename:
        filename = f"upload_{int(time.time())}"
    return filename


def get_story_path(name):
    return os.path.join(ROOT_DIR, safe_story_name(name))


def get_story_data(name):
    path = os.path.join(get_story_path(name), "story_data.json")
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_story_data(name, data):
    path = os.path.join(get_story_path(name), "story_data.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def resolve_asset(story_name, rel_path):
    if not rel_path:
        return ""
    base = get_story_path(story_name)
    candidates = [
        os.path.join(base, rel_path),
        os.path.join(base, rel_path.replace("assets_", "assets/", 1) if rel_path.startswith("assets_") else rel_path),
        os.path.join(base, "assets", os.path.basename(rel_path)),
        os.path.join(base, os.path.basename(rel_path))
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""


# ==================== AI 核心逻辑 ====================
def _sanitize_prompt(text):
    banned = {
        'horror': 'suspense', 'blood': 'red liquid', 'hanged': 'floating',
        'dead': 'sleeping', 'death': 'slumber', 'monster': 'shadowy figure',
        'creature': 'mysterious entity', 'flesh': 'soft texture',
        'kill': 'defeat', 'suicide': 'despair', 'terrified': 'surprised',
        'gore': 'messy', 'scary': 'mysterious', 'creepy': 'unusual'
    }
    res = text.lower()
    for bad, good in banned.items():
        res = res.replace(bad, good)
    return res


def _extract_images(data):
    imgs = set()
    for node in data.get("nodes", {}).values():
        if not isinstance(node, dict):
            continue
        for key in ["bg_image", "avatar_image"]:
            if node.get(key):
                imgs.add(node[key])
        chars = node.get("characters", {})
        if isinstance(chars, dict):
            for pos in ["left", "center", "right"]:
                if chars.get(pos):
                    imgs.add(chars[pos])
    return imgs


def _get_img_type(target_path, data):
    for node in data.get("nodes", {}).values():
        if not isinstance(node, dict):
            continue
        if node.get("bg_image") == target_path:
            return "background"
        if node.get("avatar_image") == target_path:
            return "avatar"
        chars = node.get("characters", {})
        if isinstance(chars, dict):
            for pos, path in chars.items():
                if path == target_path:
                    return "character"
    return "unknown"


def _build_image_prompt(filename, img_type, visual_style):
    name = os.path.splitext(filename)[0].replace('_', ' ')
    style = visual_style or "masterpiece, highly detailed, vivid colors"
    prompts = {
        "background": f"A scenery of {name}, wide angle background, {style}, no humans, empty environment, immersive landscape.",
        "character": f"A full body concept art portrait of {name}, art style of ({style}), standing in the center, looking at viewer, flat solid plain grey background, completely empty background, zero shadows on background.",
        "avatar": f"A close-up face portrait of {name}, expressive face, detailed eyes, art style of ({style}), flat solid plain grey background, completely empty background."
    }
    return prompts.get(img_type, f"{name}, {style}")


def _sanitize_json_paths(data):
    if data.get("bgm") and data["bgm"].startswith("assets_"):
        data["bgm"] = data["bgm"].replace("assets_", "assets/", 1)
    for node in data.get("nodes", {}).values():
        if not isinstance(node, dict):
            continue
        for key in ["bg_image", "avatar_image"]:
            if node.get(key) and node[key].startswith("assets_"):
                node[key] = node[key].replace("assets_", "assets/", 1)
        chars = node.get("characters", {})
        if isinstance(chars, dict):
            for pos in ["left", "center", "right"]:
                if chars.get(pos) and chars[pos].startswith("assets_"):
                    chars[pos] = chars[pos].replace("assets_", "assets/", 1)


# ==================== AI 生成线程 ====================
SYSTEM_PROMPT = """你是一个顶级的视觉小说（Visual Novel）游戏金牌编剧。
你的任务是：根据用户的要求，创作一个有深度、有分支选择的多场景完整剧本。

【极为严格的 JSON 规范与限制】
1. 场景(nodes)数量严格限制：必须在 2 到 10 个之间，绝对不能超过 10 个！
2. 绝对不允许在 JSON 字符串内部使用真实的换行符！如果台词需要换行，请一律写成 \\n 。
3. 绝对不允许在 JSON 字符串内部使用未转义的双引号！如果台词中需要引用，请一律使用单引号 ' '。
4. 必须输出大括号闭合的完整、合法的 JSON，不要带 ```json 等标记，绝不能遗漏任何逗号或大括号。

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



def _do_generate_script(job_id, prompt_text, is_blind_box, genre):
    job = _jobs.get(job_id)
    if not job:
        return
    try:
        client = OpenAI(api_key=XUNFEI_API_KEY1, base_url="https://spark-api-open.xf-yun.com/x2")

        user_content = (
            f"我不需要提供大纲，请直接帮我构思并编写一个极其精彩的【{genre}】题材的视觉小说剧本！自由发挥你的想象力！"
            if is_blind_box else
            f"请将这个大纲扩充为剧本：\n{prompt_text}")

        response = client.chat.completions.create(
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_content}],
            model="spark-x", stream=True, user="123456", max_tokens=8192
        )

        full_result, chunk_count = "", 0
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                text_chunk = chunk.choices[0].delta.content
                full_result += text_chunk
                chunk_count += 1
                with job._lock:
                    job.stream_text += text_chunk
                job.update(min(int(chunk_count * 2), 95), "正在构思剧情...")

        job.update(95, "正在整理剧本格式...")

        match = re.search(r'\{.*\}', full_result, re.DOTALL)
        clean_json = match.group(0) if match else full_result

        # 修复括号
        open_b, close_b = clean_json.count('{'), clean_json.count('}')
        if open_b > close_b:
            clean_json += '}' * (open_b - close_b)
        elif close_b > open_b:
            clean_json = clean_json[:-(close_b - open_b)]

        data = json.loads(clean_json.strip())
        _sanitize_json_paths(data)
        _fix_and_validate_story_data(data)   # ← 新增：修复悬空节点和烂尾
        job.data['script'] = data
        job.set_result({"script": data, "images": list(_extract_images(data))})
    except Exception as e:
        job.set_error(str(e))
def _fix_and_validate_story_data(data):
    """
    自动修复剧本数据中的常见问题：
    1. 移除指向不存在节点的悬空 choices
    2. 清理空 choices 后自动标记 is_ending
    3. 如果没有结局节点，强制把最后一个场景设为结局
    4. 打印修复日志
    """
    nodes = data.get("nodes", {})
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
            if original_choices:
                fix_log.append(f"[{node_id}] 所有指向均无效，已强制设为结局节点")
        else:
            node["choices"] = valid_choices
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

# ==================== AI 生成线程 ====================
# encoding: UTF-8
import time
import requests
from datetime import datetime
from wsgiref.handlers import format_date_time
from time import mktime
import hashlib
import base64
import hmac
from urllib.parse import urlencode
import json
from PIL import Image
from io import BytesIO
import os

class AssembleHeaderException(Exception):
    def __init__(self, msg):
        self.message = msg

class Url:
    def __init__(this, host, path, schema):
        this.host = host
        this.path = path
        this.schema = schema

def sha256base64(data):
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
    u = parse_url(requset_url)
    host = u.host
    path = u.path
    now = datetime.now()
    date = format_date_time(mktime(now.timetuple()))
    signature_origin = "host: {}\ndate: {}\n{} {} HTTP/1.1".format(host, date, method, path)
    signature_sha = hmac.new(api_secret.encode('utf-8'), signature_origin.encode('utf-8'),
                             digestmod=hashlib.sha256).digest()
    signature_sha = base64.b64encode(signature_sha).decode(encoding='utf-8')
    authorization_origin = "api_key=\"%s\", algorithm=\"%s\", headers=\"%s\", signature=\"%s\"" % (
        api_key, "hmac-sha256", "host date request-line", signature_sha)
    authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')
    values = {
        "host": host,
        "date": date,
        "authorization": authorization
    }
    return requset_url + "?" + urlencode(values)

def _do_generate_images(job_id, story_data, images_to_generate, target_folder):
    job = _jobs.get(job_id)
    if not job:
        return

    total = len(images_to_generate)
    if total == 0:
        job.set_result({"success": True, "message": "无需生成图片"})
        return

    success_count = fail_count = 0

    try:
        for idx, img_path in enumerate(images_to_generate):
            filename = os.path.basename(img_path)
            img_type = _get_img_type(img_path, story_data)
            image_prompt = _sanitize_prompt(_build_image_prompt(filename, img_type, story_data.get("visual_style")))

            job.update(int((idx / total) * 100), f"正在绘制: {filename} ({idx + 1}/{total})")

            # 讯飞 TTI 正确鉴权 + 请求
            host = "https://spark-api.cn-huabei-1.xf-yun.com/v2.1/tti"
            url = assemble_ws_auth_url(host, method="POST", api_key=XUNFEI_API_KEY2, api_secret=XUNFEI_API_SECRET)

            # ✅ 只保留接口支持的字段，删除所有不支持的高级参数
            body = {
                "header": {
                    "app_id": XUNFEI_APP_ID,
                    "uid": "123456"
                },
                "parameter": {
                    "chat": {
                        "domain": "xopqwentti20b",
                        "width": 768,
                        "height": 768
                    }
                },
                "payload": {
                    "message": {
                        "text": [
                            {"role": "user", "content": image_prompt}
                        ]
                    }
                }
            }

            resp = requests.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=60)
            resp_data = resp.json()

            if resp_data.get("header", {}).get("code") == 0:
                base64_img = resp_data["payload"]["choices"]["text"][0]["content"]
                img_bytes = base64.b64decode(base64_img)
                output_path = os.path.join(target_folder, filename)

                try:
                    if img_type != "background" and REMBG_AVAILABLE:
                        img_bytes = rembg.remove(img_bytes, post_process_mask=True)
                        Image.open(BytesIO(img_bytes)).save(output_path, "PNG")
                    else:
                        with open(output_path, "wb") as f:
                            f.write(img_bytes)
                    success_count += 1
                    print(f"[生图] ✓ {filename} 保存成功")
                except Exception as e:
                    with open(output_path, "wb") as f:
                        f.write(img_bytes)
                    success_count += 1
                    print(f"[生图] ⚠ {filename} 抠图失败，保存原图: {e}")
            else:
                fail_count += 1
                err_code = resp_data.get("header", {}).get("code", "unknown")
                err_msg = resp_data.get("header", {}).get("message", "")
                print(f"[生图] ✗ {filename} API错误: code={err_code}, msg={err_msg}")
                if 'error_details' not in job.data:
                    job.data['error_details'] = f"code={err_code}, msg={err_msg}"

        job.update(100, "全部图像绘制完成！")
        msg = f"所有 {success_count} 张图片已就绪" if fail_count == 0 else f"{success_count}成功, {fail_count}失败"
        if fail_count > 0 and job.data.get('error_details'):
            msg += f" (错误: {job.data['error_details']})"
        job.set_result({"success": True, "message": msg, "warning": msg if fail_count > 0 else None})
    except Exception as e:
        job.set_error(f"生图异常: {str(e)}")


# ==================== 页面路由 ====================
@app.route('/')
def hub():
    stories = []
    if os.path.exists(ROOT_DIR):
        for d in os.listdir(ROOT_DIR):
            story_dir = os.path.join(ROOT_DIR, d)
            if os.path.isdir(story_dir) and os.path.exists(os.path.join(story_dir, "story_data.json")):
                data = get_story_data(d)
                stories.append({
                    'name': d,
                    'title': data.get('title', d) if data else d,
                    'desc': data.get('desc', '暂无简介') if data else '',
                    'cover': data.get('nodes', {}).get(data.get('start_node', 'scene_1'), {}).get('bg_image',
                                                                                                  '') if data else ''
                })
    return render_template('hub.html', stories=stories)


@app.route('/editor/<name>')
def editor(name):
    return render_template('editor.html', story_name=name)


@app.route('/play/<name>')
def player(name):
    data = get_story_data(name)
    if not data:
        abort(404)
    return render_template('player.html', story_name=name, story_data=data)


@app.route('/ai-generator')
def ai_generator():
    return render_template('ai_generator.html')


# ==================== API 路由：故事管理 ====================
@app.route('/api/story', methods=['POST'])
def create_story():
    data = request.json
    name = safe_story_name(data.get('name', '').strip())
    if not name:
        return jsonify({'error': '名称不能为空'}), 400

    story_dir = get_story_path(name)
    if os.path.exists(story_dir):
        return jsonify({'error': '故事已存在'}), 409

    os.makedirs(story_dir)
    os.makedirs(os.path.join(story_dir, "assets"))

    default_data = {
        "title": name, "desc": "这是一个刚刚创建的全新故事...", "bgm": "",
        "start_node": "scene_1",
        "nodes": {
            "scene_1": {
                "x": 400, "y": 200, "bg_image": "",
                "characters": {"left": "", "center": "", "right": ""},
                "avatar_image": "", "speaker": "系统", "text": "故事从此开始...", "choices": []
            }
        }
    }
    save_story_data(name, default_data)
    return jsonify({'success': True, 'name': name})


@app.route('/api/story/<name>', methods=['GET', 'PUT', 'DELETE'])
def story_api(name):
    story_dir = get_story_path(name)

    if request.method == 'GET':
        data = get_story_data(name)
        if not data:
            return jsonify({'error': '故事不存在'}), 404
        return jsonify(data)

    elif request.method == 'PUT':
        save_story_data(name, request.json)
        return jsonify({'success': True})

    elif request.method == 'DELETE':
        if os.path.exists(story_dir):
            shutil.rmtree(story_dir)
        return jsonify({'success': True})


@app.route('/api/story/<name>/upload', methods=['POST'])
def upload_asset(name):
    story_dir = get_story_path(name)
    assets_dir = os.path.join(story_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename = safe_upload_filename(file.filename)
    filepath = os.path.join(assets_dir, filename)
    file.save(filepath)

    return jsonify({
        'success': True,
        'path': f"assets/{filename}",
        'url': f"/static/stories/{name}/assets/{filename}"
    })


@app.route('/api/story/<name>/asset')
def get_asset(name):
    rel_path = request.args.get('path', '')
    full_path = resolve_asset(name, rel_path)
    if not full_path or not os.path.exists(full_path):
        abort(404)
    return send_from_directory(os.path.dirname(full_path), os.path.basename(full_path))


# ==================== API 路由：AI 生成 ====================
@app.route('/api/ai/upload-temp', methods=['POST'])
def upload_temp():
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename = safe_upload_filename(file.filename)
    filepath = os.path.join(TEMP_DIR, f"{int(time.time())}_{filename}")
    file.save(filepath)

    return jsonify({'success': True, 'path': filepath, 'filename': filename})


@app.route('/api/ai/start', methods=['POST'])
def ai_start():
    data = request.json
    prompt = data.get('prompt', '').strip()
    is_blind = data.get('is_blind_box', False)
    genre = data.get('genre', '')

    if not is_blind and not prompt:
        return jsonify({'error': '请输入故事大纲'}), 400

    job_id = f"job_{int(time.time() * 1000)}"
    with _jobs_lock:
        _jobs[job_id] = JobStatus()

    threading.Thread(
        target=_do_generate_script,
        args=(job_id, prompt, is_blind, genre),
        daemon=True
    ).start()

    return jsonify({'job_id': job_id})


@app.route('/api/ai/status/<job_id>')
def ai_status(job_id):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(job.to_dict())


@app.route('/api/ai/generate-images', methods=['POST'])
def ai_generate_images():
    data = request.json
    job_id = data.get('job_id')
    story_data = data.get('story_data')
    images = data.get('images', [])
    bgm_path = data.get('bgm_path', '')
    local_copies = data.get('local_copies', {})

    job = _jobs.get(job_id)

    # 为图像生成创建新的 job（复用旧 job 会导致 finished=True 立即触发 finalize）
    img_job_id = f"job_{int(time.time() * 1000)}_img"
    img_job = JobStatus()
    with _jobs_lock:
        _jobs[img_job_id] = img_job

    # 创建故事目录（统一使用 safe_story_name 保证路径一致）
    title = safe_story_name(story_data.get('title', 'AI新故事'))

    story_folder = os.path.join(ROOT_DIR, title)
    counter = 1
    orig_title = title
    while os.path.exists(story_folder):
        title = f"{orig_title}_{counter}"
        story_folder = os.path.join(ROOT_DIR, title)
        counter += 1

    os.makedirs(story_folder)
    assets_dir = os.path.join(story_folder, "assets")
    os.makedirs(assets_dir)

    # 保存 BGM
    if bgm_path and os.path.exists(bgm_path):
        bgm_filename = os.path.basename(bgm_path)
        shutil.copy(bgm_path, os.path.join(assets_dir, bgm_filename))
        story_data['bgm'] = f"assets/{bgm_filename}"

    # 处理手动上传的图片
    for old_path, local_path in local_copies.items():
        if local_path and os.path.exists(local_path):
            ext = os.path.splitext(local_path)[1] or '.png'
            target_filename = os.path.splitext(os.path.basename(old_path))[0] + ext
            new_path = f"assets/{target_filename}"

            for node in story_data.get("nodes", {}).values():
                if not isinstance(node, dict):
                    continue
                if node.get("bg_image") == old_path:
                    node["bg_image"] = new_path
                if node.get("avatar_image") == old_path:
                    node["avatar_image"] = new_path
                chars = node.get("characters", {})
                if isinstance(chars, dict):
                    for pos in ["left", "center", "right"]:
                        if chars.get(pos) == old_path:
                            chars[pos] = new_path

            shutil.copy(local_path, os.path.join(assets_dir, target_filename))

    # 过滤出需要 AI 生成的图片
    ai_images = []
    for img_path in images:
        filename = os.path.basename(img_path)
        if not os.path.exists(os.path.join(assets_dir, filename)):
            ai_images.append(img_path)
    ai_images = list(dict.fromkeys(ai_images))[:20]

    if not ai_images:
        save_story_data(os.path.basename(story_folder), story_data)
        return jsonify({
            'success': True,
            'story_name': os.path.basename(story_folder),
            'message': '项目构建完毕（无需生成图片）'
        })

    img_job.data['story_folder'] = story_folder
    img_job.data['story_data'] = story_data

    threading.Thread(
        target=_do_generate_images,
        args=(img_job_id, story_data, ai_images, assets_dir),
        daemon=True
    ).start()

    return jsonify({'success': True, 'job_id': img_job_id, 'total_images': len(ai_images)})


@app.route('/api/ai/finalize', methods=['POST'])
def ai_finalize():
    data = request.json
    job_id = data.get('job_id')
    job = _jobs.get(job_id)

    if not job or not job.result:
        return jsonify({'error': '任务未完成'}), 400

    story_folder = job.data.get('story_folder')
    story_data = job.data.get('story_data')

    if not story_folder or not story_data:
        return jsonify({'error': '数据丢失'}), 500

    save_story_data(os.path.basename(story_folder), story_data)

    assets_dir = os.path.join(story_folder, "assets")
    asset_count = len(os.listdir(assets_dir)) if os.path.exists(assets_dir) else 0

    return jsonify({
        'success': True,
        'story_name': os.path.basename(story_folder),
        'scene_count': len(story_data.get('nodes', {})),
        'asset_count': asset_count
    })


# ==================== 启动 ====================
if __name__ == '__main__':
    init_root()
    app.run(debug=True, host='0.0.0.0', port=5000)