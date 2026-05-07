import os, asyncio, logging, json, base64, requests, time
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler, ChatMemberHandler
)

# --- 1. 基础配置 ---
# 开启标准日志，这样 Render 的 Logs 里能看到时间戳
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_TOKEN = os.getenv("GH_PAT_TOKEN") 
GH_REPO = "boyann118-creator/sunday-bot-data"
GH_PATH = "groups.json"

(MAIN_STATE, BCAST_GROUP, BCAST_MSG, SET_GROUP_NAME) = range(4)
DATA_CACHE = {"groups": [], "members": []}

# --- 2. GitHub API 核心逻辑 (加入日志) ---

def sync_from_github():
    global DATA_CACHE
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            json_data = resp.json()
            content = base64.b64decode(json_data['content']).decode('utf-8')
            DATA_CACHE = json.loads(content)
            print(f"[{time.strftime('%H:%M:%S')}] 🔄 GitHub 数据同步成功 (SHA: {json_data['sha'][:7]})")
            return json_data['sha']
        print(f"[{time.strftime('%H:%M:%S')}] ❌ 同步失败: {resp.status_code} - 检查仓库名或Token")
        return None
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ API请求异常: {e}")
        return None

def save_to_github():
    sha = sync_from_github() 
    if not sha: 
        print(f"[{time.strftime('%H:%M:%S')}] 🚫 停止写入：无法获取有效的 SHA")
        return False
    
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    encoded_content = base64.b64encode(json.dumps(DATA_CACHE, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
    
    payload = {"message": "Update by Sunday Bot", "content": encoded_content, "sha": sha}
    
    resp = requests.put(url, headers=headers, json=payload)
    if resp.status_code in [200, 201]:
        print(f"[{time.strftime('%H:%M:%S')}] ✅ 数据已成功推送到 GitHub 远程仓库")
        return True
    print(f"[{time.strftime('%H:%M:%S')}] ❌ 写入 GitHub 失败: {resp.status_code}")
    return False

# --- 3. 手动登记功能 ---

async def manual_set_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """在群里发送 /set_group A组"""
    chat = update.effective_chat
    user = update.effective_user
    print(f"[{time.strftime('%H:%M:%S')}] 📩 收到登记请求 | 来自: {user.id} | 群: {chat.title}({chat.id})")

    if user.id not in ADMIN_IDS:
        print(f"[{time.strftime('%H:%M:%S')}] ⛔ 非管理员尝试登记，已拦截")
        return

    if not context.args:
        await update.message.reply_text("💡 请带上分组名，例如：`/set_group A组`")
        return

    g_name = context.args[0]
    sync_from_github()
    
    existing = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    if existing:
        existing['g_name'] = g_name
        existing['remark'] = chat.title
    else:
        DATA_CACHE['members'].append({"chat_id": chat.id, "remark": chat.title, "g_name": g_name})
    
    if g_name not in DATA_CACHE['groups']:
        DATA_CACHE['groups'].append(g_name)
    
    if save_to_github():
        await update.message.reply_text(f"✅ 登记成功！\n群名：{chat.title}\n分组：{g_name}")
    else:
        await update.message.reply_text("❌ 同步 GitHub 失败，请查看 Render 日志")

# --- 4. 交互逻辑 (加入日志) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: 
        print(f"[{time.strftime('%H:%M:%S')}] ⛔ 非法用户 {uid} 尝试访问私聊菜单")
        return ConversationHandler.END
    
    print(f"[{time.strftime('%H:%M:%S')}] 👤 管理员 {uid} 打开了控制主菜单")
    sync_from_github()
    
    kb = [
        [InlineKeyboardButton("📁 分组管理", callback_data='manage_g')],
        [InlineKeyboardButton("🚀 执行群发", callback_data='start_bc')],
        [InlineKeyboardButton("🔄 强制刷新数据", callback_data='sync_now')]
    ]
    reply_markup = InlineKeyboardMarkup(kb)
    text = "🤖 **群发系统控制台**\n当前已加载群组数: " + str(len(DATA_CACHE['members']))
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

# --- 其他功能保持原样，增加群发日志 ---

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_group = context.user_data.get('bc_target')
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target_group]
    
    print(f"[{time.strftime('%H:%M:%S')}] 📣 开始群发任务 | 目标组: {target_group} | 群数: {len(ids)}")
    sent_msg = await update.message.reply_text(f"🚀 正在发送至 {len(ids)} 个群...")
    
    count = 0
    for cid in ids:
        try:
            await context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"   ⚠️ 发送失败 ID {cid}: {e}")
            
    await sent_msg.edit_text(f"✅ 完成！成功发送: {count}/{len(ids)}")
    print(f"[{time.strftime('%H:%M:%S')}] ✨ 群发任务结束 | 成功: {count}")
    return BCAST_MSG

# --- 启动逻辑 ---
app = Flask('')
@app.route('/')
def home(): return "Bot Active"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    print(f"[{time.strftime('%H:%M:%S')}] 🚀 机器人初始化启动...")
    sync_from_github()
    Thread(target=run_web).start()
    
    app_tg = Application.builder().token(TOKEN).build()
    
    # 核心指令
    app_tg.add_handler(CommandHandler("set_group", manual_set_group))
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(start, pattern='^to_start$|^sync_now$'),
                CallbackQueryHandler(lambda u, c: start(u, c), pattern='^manage_g$'), # 简化展示
                CallbackQueryHandler(lambda u, c: start(u, c), pattern='^start_bc$'), # 简化展示
            ],
            # ... 其他 state 保持与之前逻辑一致 ...
        },
        fallbacks=[CommandHandler("start", start)],
    )
    # 注意：为了篇幅，这里缩减了 conv 的部分 state，请保持你之前完整版的 conv 内容
    # 只需要把 CommandHandler("set_group", manual_set_group) 放在 conv 之外即可
    
    app_tg.run_polling()

if __name__ == '__main__': main()
