import streamlit as st
import sqlite3
import os
import json
import hashlib
from datetime import datetime, date
from openai import OpenAI
from dotenv import load_dotenv

# ----------------------------
# 页面基础设置
# ----------------------------
st.set_page_config(
    page_title="AI 每日清单",
    page_icon="📝",
    layout="centered"
)

# ----------------------------
# 读取环境变量
# ----------------------------

API_KEY = st.secrets["OPENAI_API_KEY"]
BASE_URL = st.secrets["OPENAI_BASE_URL"]
MODEL_NAME = st.secrets["MODEL_NAME"]

# ----------------------------
# 初始化 AI 客户端
# ----------------------------
client = None
if API_KEY:
    if BASE_URL:
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    else:
        client = OpenAI(api_key=API_KEY)

# ----------------------------
# 数据库连接
# ----------------------------
conn = sqlite3.connect("todo.db", check_same_thread=False)
cursor = conn.cursor()

# ----------------------------
# 创建 users 表
# ----------------------------
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
)
""")
conn.commit()

# ----------------------------
# 创建 tasks 表
# ----------------------------
cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    task TEXT NOT NULL,
    category TEXT DEFAULT '其他',
    priority TEXT DEFAULT '中',
    due_date TEXT,
    created_at TEXT NOT NULL,
    completed INTEGER DEFAULT 0
)
""")
conn.commit()

# ----------------------------
# 检查旧表结构，缺什么补什么
# ----------------------------
cursor.execute("PRAGMA table_info(tasks)")
task_columns = [column[1] for column in cursor.fetchall()]

if "user_id" not in task_columns:
    cursor.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER")
    conn.commit()

if "completed" not in task_columns:
    cursor.execute("ALTER TABLE tasks ADD COLUMN completed INTEGER DEFAULT 0")
    conn.commit()

if "category" not in task_columns:
    cursor.execute("ALTER TABLE tasks ADD COLUMN category TEXT DEFAULT '其他'")
    conn.commit()

if "priority" not in task_columns:
    cursor.execute("ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT '中'")
    conn.commit()

if "due_date" not in task_columns:
    cursor.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
    conn.commit()

# ----------------------------
# session_state 初始化
# ----------------------------
if "page" not in st.session_state:
    st.session_state.page = "login"

if "current_user_id" not in st.session_state:
    st.session_state.current_user_id = None

if "current_username" not in st.session_state:
    st.session_state.current_username = None

if "prefer_priority" not in st.session_state:
    st.session_state.prefer_priority = True

if "preferred_category" not in st.session_state:
    st.session_state.preferred_category = "无"

if "use_ai_sort" not in st.session_state:
    st.session_state.use_ai_sort = False

if "ai_sorted_ids" not in st.session_state:
    st.session_state.ai_sorted_ids = []

if "ai_recognized_category" not in st.session_state:
    st.session_state.ai_recognized_category = None

if "ai_recognized_priority" not in st.session_state:
    st.session_state.ai_recognized_priority = None

# ----------------------------
# 工具函数
# ----------------------------
def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def priority_to_number(priority):
    priority_map = {
        "高": 3,
        "中": 2,
        "低": 1
    }
    return priority_map.get(priority, 2)

def category_to_preference_score(category, preferred_category):
    if preferred_category == "无":
        return 0
    return 1 if category == preferred_category else 0

def due_date_to_sort_value(due_date_str):
    if not due_date_str:
        return -999999

    try:
        due = datetime.strptime(due_date_str, "%Y-%m-%d").date()
        today = date.today()
        days_left = (due - today).days
        return -days_left
    except:
        return -999999

# ----------------------------
# 用户相关函数
# ----------------------------
def register_user(username, password):
    try:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        password_hash = hash_password(password)

        cursor.execute(
            """
            INSERT INTO users (username, password_hash, created_at)
            VALUES (?, ?, ?)
            """,
            (username, password_hash, created_at)
        )
        conn.commit()
        return True, "注册成功！"
    except sqlite3.IntegrityError:
        return False, "该用户名已存在，请更换用户名。"
    except Exception as e:
        return False, f"注册失败：{str(e)}"

def login_user(username, password):
    password_hash = hash_password(password)

    cursor.execute(
        """
        SELECT id, username
        FROM users
        WHERE username = ? AND password_hash = ?
        """,
        (username, password_hash)
    )
    user = cursor.fetchone()

    if user:
        return True, user
    return False, None

# ----------------------------
# 数据库操作函数（按用户隔离）
# ----------------------------
def add_task(user_id, task_text, category, priority, due_date):
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO tasks (user_id, task, category, priority, due_date, created_at, completed)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (user_id, task_text, category, priority, due_date, created_at)
    )
    conn.commit()

def get_active_tasks_rule_based(user_id, prefer_priority, preferred_category):
    cursor.execute("""
        SELECT id, task, category, priority, due_date, created_at
        FROM tasks
        WHERE completed = 0 AND user_id = ?
    """, (user_id,))
    tasks = cursor.fetchall()

    if prefer_priority:
        tasks = sorted(
            tasks,
            key=lambda x: (
                category_to_preference_score(x[2], preferred_category),
                due_date_to_sort_value(x[4]),
                priority_to_number(x[3])
            ),
            reverse=True
        )
    else:
        tasks = sorted(
            tasks,
            key=lambda x: (
                category_to_preference_score(x[2], preferred_category),
                due_date_to_sort_value(x[4]),
                x[0]
            ),
            reverse=True
        )

    return tasks

def get_completed_tasks(user_id):
    cursor.execute("""
        SELECT id, task, category, priority, due_date, created_at
        FROM tasks
        WHERE completed = 1 AND user_id = ?
        ORDER BY id DESC
    """, (user_id,))
    return cursor.fetchall()

def complete_task(user_id, task_id):
    cursor.execute(
        "UPDATE tasks SET completed = 1 WHERE id = ? AND user_id = ?",
        (task_id, user_id)
    )
    conn.commit()

def delete_task(user_id, task_id):
    cursor.execute(
        "DELETE FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, user_id)
    )
    conn.commit()

# ----------------------------
# AI 排序相关函数
# ----------------------------
def build_ai_sort_prompt(tasks, prefer_priority, preferred_category):
    task_list = []
    for task in tasks:
        task_list.append({
            "id": task[0],
            "task": task[1],
            "category": task[2],
            "priority": task[3],
            "due_date": task[4],
            "created_at": task[5]
        })

    prompt = f"""
你是一个智能任务排序助手。
请根据用户偏好，为下面的任务列表生成一个更合理的执行顺序。

用户偏好：
1. 是否优先处理高优先级任务：{"是" if prefer_priority else "否"}
2. 优先处理的任务类别：{preferred_category}

排序时请综合考虑：
- 用户偏好类别
- 任务优先级
- 截止日期（越近越应该靠前）

任务列表：
{json.dumps(task_list, ensure_ascii=False, indent=2)}

请你只返回一个 JSON 数组，数组中只包含任务 id，表示排序后的顺序。
例如：
[3, 1, 2]

不要返回解释，不要返回多余文字。
"""
    return prompt

def get_ai_sorted_task_ids(tasks, prefer_priority, preferred_category):
    if not client:
        return None, "未检测到可用的 AI 接口配置。"

    try:
        prompt = build_ai_sort_prompt(tasks, prefer_priority, preferred_category)

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是一个只输出 JSON 的任务排序助手。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        content = response.choices[0].message.content.strip()
        task_id_list = json.loads(content)

        if not isinstance(task_id_list, list):
            return None, "AI 返回结果不是列表。"

        return task_id_list, None

    except Exception as e:
        return None, f"AI 排序失败：{str(e)}"

def reorder_tasks_by_ai_result(tasks, ai_sorted_ids):
    task_dict = {task[0]: task for task in tasks}
    sorted_tasks = []

    for task_id in ai_sorted_ids:
        if task_id in task_dict:
            sorted_tasks.append(task_dict[task_id])

    existing_ids = {task[0] for task in sorted_tasks}
    for task in tasks:
        if task[0] not in existing_ids:
            sorted_tasks.append(task)

    return sorted_tasks

# ----------------------------
# AI 自动识别任务信息
# ----------------------------
def build_ai_extract_prompt(task_text):
    prompt = f"""
你是一个任务信息提取助手。
请根据用户输入的任务内容，判断这个任务最合适的类别和优先级。

可选类别只能是：
学习、工作、生活、健康、其他

可选优先级只能是：
高、中、低

用户输入的任务内容：
{task_text}

请你只返回 JSON，对象格式必须如下：
{{
  "category": "学习",
  "priority": "高"
}}

不要返回解释，不要返回多余文字。
"""
    return prompt

def get_ai_task_info(task_text):
    if not client:
        return None, "未检测到可用的 AI 接口配置。"

    try:
        prompt = build_ai_extract_prompt(task_text)

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是一个只输出 JSON 的任务分类助手。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        content = response.choices[0].message.content.strip()
        result = json.loads(content)

        if not isinstance(result, dict):
            return None, "AI 返回结果不是字典。"

        category = result.get("category")
        priority = result.get("priority")

        valid_categories = ["学习", "工作", "生活", "健康", "其他"]
        valid_priorities = ["高", "中", "低"]

        if category not in valid_categories:
            category = "其他"

        if priority not in valid_priorities:
            priority = "中"

        return {
            "category": category,
            "priority": priority
        }, None

    except Exception as e:
        return None, f"AI 识别失败：{str(e)}"

# ----------------------------
# Sidebar
# ----------------------------
with st.sidebar:
    st.header("用户中心")

    if st.session_state.current_user_id is not None:
        st.success(f"当前用户：{st.session_state.current_username}")
        if st.button("退出登录"):
            st.session_state.current_user_id = None
            st.session_state.current_username = None
            st.session_state.page = "login"
            st.session_state.use_ai_sort = False
            st.session_state.ai_sorted_ids = []
            st.session_state.ai_recognized_category = None
            st.session_state.ai_recognized_priority = None
            st.rerun()
    else:
        st.info("请先登录或注册")

# ----------------------------
# 登录页
# ----------------------------
def show_login_page():
    st.title("登录")
    st.write("请先登录你的账号，进入专属 Todo List。")

    username = st.text_input("用户名", key="login_username")
    password = st.text_input("密码", type="password", key="login_password")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("登录"):
            if username.strip() == "" or password.strip() == "":
                st.warning("用户名和密码不能为空。")
            else:
                success, user = login_user(username.strip(), password.strip())
                if success:
                    st.session_state.current_user_id = user[0]
                    st.session_state.current_username = user[1]
                    st.session_state.page = "main"
                    st.session_state.use_ai_sort = False
                    st.session_state.ai_sorted_ids = []
                    st.session_state.ai_recognized_category = None
                    st.session_state.ai_recognized_priority = None
                    st.success("登录成功！")
                    st.rerun()
                else:
                    st.error("用户名或密码错误。")

    with col2:
        if st.button("去注册"):
            st.session_state.page = "register"
            st.rerun()

# ----------------------------
# 注册页
# ----------------------------
def show_register_page():
    st.title("注册")
    st.write("创建一个新账号。")

    username = st.text_input("用户名", key="register_username")
    password = st.text_input("密码", type="password", key="register_password")
    confirm_password = st.text_input("确认密码", type="password", key="register_confirm_password")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("确认注册"):
            if username.strip() == "" or password.strip() == "" or confirm_password.strip() == "":
                st.warning("请完整填写所有信息。")
            elif password != confirm_password:
                st.error("两次输入的密码不一致。")
            else:
                success, message = register_user(username.strip(), password.strip())
                if success:
                    st.success("注册成功！请返回登录。")
                    st.session_state.page = "login"
                    st.rerun()
                else:
                    st.error(message)

    with col2:
        if st.button("返回登录"):
            st.session_state.page = "login"
            st.rerun()

# ----------------------------
# 主 Todo 页面
# ----------------------------
def show_main_page():
    st.title("AI 每日清单")
    st.write(f"欢迎，**{st.session_state.current_username}**！这是你的专属任务清单。")

    st.subheader("排序偏好设置")

    st.session_state.prefer_priority = st.checkbox(
        "优先处理高优先级任务",
        value=st.session_state.prefer_priority
    )

    st.session_state.preferred_category = st.selectbox(
        "优先处理哪一类任务：",
        ["无", "学习", "工作", "生活", "健康", "其他"],
        index=["无", "学习", "工作", "生活", "健康", "其他"].index(
            st.session_state.preferred_category
        )
    )

    st.subheader("添加新任务")

    new_task = st.text_input("请输入今天要做的事情：")

    manual_category = st.selectbox(
        "手动选择任务类别：",
        ["学习", "工作", "生活", "健康", "其他"]
    )

    manual_priority = st.selectbox(
        "手动选择优先级：",
        ["高", "中", "低"]
    )

    manual_due_date = st.date_input(
        "请选择截止日期：",
        value=date.today()
    )

    col_add1, col_add2 = st.columns(2)

    with col_add1:
        if st.button("手动添加任务"):
            if new_task.strip() != "":
                add_task(
                    st.session_state.current_user_id,
                    new_task,
                    manual_category,
                    manual_priority,
                    manual_due_date.strftime("%Y-%m-%d")
                )
                st.success("任务添加成功！")
                st.session_state.use_ai_sort = False
                st.session_state.ai_sorted_ids = []
                st.session_state.ai_recognized_category = None
                st.session_state.ai_recognized_priority = None
                st.rerun()
            else:
                st.warning("任务内容不能为空。")

    with col_add2:
        if st.button("AI 智能识别任务信息"):
            if new_task.strip() != "":
                ai_result, error_message = get_ai_task_info(new_task)

                if error_message:
                    st.error(error_message)
                else:
                    st.session_state.ai_recognized_category = ai_result["category"]
                    st.session_state.ai_recognized_priority = ai_result["priority"]
                    st.success("AI 识别完成！")
            else:
                st.warning("请先输入任务内容。")

    if st.session_state.ai_recognized_category and st.session_state.ai_recognized_priority:
        st.markdown("### AI 识别结果")
        st.write(f"**类别：** {st.session_state.ai_recognized_category}")
        st.write(f"**优先级：** {st.session_state.ai_recognized_priority}")
        st.write(f"**截止日期：** {manual_due_date.strftime('%Y-%m-%d')}")

        if st.button("确认使用 AI 识别结果添加任务"):
            if new_task.strip() != "":
                add_task(
                    st.session_state.current_user_id,
                    new_task,
                    st.session_state.ai_recognized_category,
                    st.session_state.ai_recognized_priority,
                    manual_due_date.strftime("%Y-%m-%d")
                )
                st.success("任务已按 AI 识别结果添加！")
                st.session_state.use_ai_sort = False
                st.session_state.ai_sorted_ids = []
                st.session_state.ai_recognized_category = None
                st.session_state.ai_recognized_priority = None
                st.rerun()
            else:
                st.warning("任务内容不能为空。")

    st.subheader("排序操作")

    col_sort1, col_sort2 = st.columns(2)

    with col_sort1:
        if st.button("使用规则排序"):
            st.session_state.use_ai_sort = False
            st.session_state.ai_sorted_ids = []
            st.rerun()

    with col_sort2:
        if st.button("AI 智能排序"):
            active_tasks_for_ai = get_active_tasks_rule_based(
                st.session_state.current_user_id,
                st.session_state.prefer_priority,
                st.session_state.preferred_category
            )

            if active_tasks_for_ai:
                ai_sorted_ids, error_message = get_ai_sorted_task_ids(
                    active_tasks_for_ai,
                    st.session_state.prefer_priority,
                    st.session_state.preferred_category
                )

                if error_message:
                    st.error(error_message)
                else:
                    st.session_state.use_ai_sort = True
                    st.session_state.ai_sorted_ids = ai_sorted_ids
                    st.success("AI 排序完成！")
                    st.rerun()
            else:
                st.info("当前没有可排序的任务。")

    st.subheader("当前任务")

    active_tasks = get_active_tasks_rule_based(
        st.session_state.current_user_id,
        st.session_state.prefer_priority,
        st.session_state.preferred_category
    )

    if st.session_state.use_ai_sort and st.session_state.ai_sorted_ids:
        active_tasks = reorder_tasks_by_ai_result(
            active_tasks,
            st.session_state.ai_sorted_ids
        )
        st.caption("当前显示顺序：AI 智能排序")
    else:
        st.caption("当前显示顺序：规则排序")

    if active_tasks:
        for task in active_tasks:
            col1, col2, col3 = st.columns([1, 6, 2])

            with col1:
                if st.checkbox("完成", key=f"complete_{task[0]}"):
                    complete_task(st.session_state.current_user_id, task[0])
                    st.session_state.ai_sorted_ids = [
                        i for i in st.session_state.ai_sorted_ids if i != task[0]
                    ]
                    st.rerun()

            with col2:
                st.write(f"**{task[1]}**")
                st.caption(
                    f"类别：{task[2]} ｜ 优先级：{task[3]} ｜ 截止日期：{task[4]} ｜ 创建时间：{task[5]}"
                )

            with col3:
                if st.button("删除", key=f"delete_active_{task[0]}"):
                    delete_task(st.session_state.current_user_id, task[0])
                    st.session_state.ai_sorted_ids = [
                        i for i in st.session_state.ai_sorted_ids if i != task[0]
                    ]
                    st.rerun()
    else:
        st.info("当前没有未完成任务。")

    st.subheader("已完成任务")

    completed_tasks = get_completed_tasks(st.session_state.current_user_id)

    if completed_tasks:
        for task in completed_tasks:
            col1, col2 = st.columns([6, 2])

            with col1:
                st.write(f"~~{task[1]}~~")
                st.caption(
                    f"类别：{task[2]} ｜ 优先级：{task[3]} ｜ 截止日期：{task[4]} ｜ 创建时间：{task[5]}"
                )

            with col2:
                if st.button("删除", key=f"delete_completed_{task[0]}"):
                    delete_task(st.session_state.current_user_id, task[0])
                    st.rerun()
    else:
        st.info("当前还没有已完成任务。")

# ----------------------------
# 页面路由
# ----------------------------
if st.session_state.current_user_id is not None:
    show_main_page()
else:
    if st.session_state.page == "register":
        show_register_page()
    else:
        show_login_page()