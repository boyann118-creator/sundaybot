import os, asyncio, logging, json, base64, requests, time
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- 1. 基础配置 ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_TOKEN = os.getenv("GH_PAT_TOKEN") 
GH_REPO = "boyann118-creator/sunday-bot-data"
GH_PATH = "groups.json"

(MAIN_STATE, BCAST_GROUP, BCAST_MSG, SET_GROUP_NAME) = range(4)
DATA_CACHE = {"groups": ["未分类"], "members": []}

# --- 2. GitHub 核心逻辑 ---

def sync_from_github():
    """从 GitHub 拉取最新数据，确保 SHA 最关键"""
    global DATA_CACHE
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            json_data = resp.json()
            content = base64.b64decode(json_data['content']).decode('utf-8')
            new_data = json.loads(content)
            # 防御性检查：确保拉回来的数据包含必要的 key
            if "members" in new_data and "groups" in new_data:
                DATA_CACHE = new_data
                print(f"[{time.strftime('%H:%M:%S')}] 🔄 拉取成功 | 成员: {len(DATA_CACHE['members'])} | SHA: {json_data['sha'][:7]}")
                return json_data['sha']
        print(f"[{time.strftime('%H:%M:%S')}] ❌ 同步失败: {resp.status_code}")
        return None
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 网络异常: {e}")
        return None

def save_to_github():
    """强制先同步再写入，防止 SHA 失效导致覆盖"""
    # 写入前必须拿到最新的物理 SHA
    current_sha = sync_from_github()
    if not current_sha:
        print(f"[{time.strftime('%H:%M:%S')}] 🚫 写入终止：无法获取远程最新的 SHA")
        return False
    
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    
    # 确保保存时的结构正确
    json_str = json.dumps(DATA_CACHE, ensure_ascii=False, indent=2)
    encoded_content = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
    
    payload = {
        "message": f"Sunday Bot AutoSync - {time.strftime('%H:%M:%S')}",
        "content": encoded_content,
        "sha": current_sha,
        "branch": "main"
    }
    
    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in [200, 201]:
            print(f"[{time.strftime('%H:%M:%S')}] ✅ GitHub 物理写入完成")
            return True
        print(f"[{time.strftime('%H:%M:%S')}] ❌ 写入拒绝: {resp.text}")
        return False
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 写入异常: {e}")
        return False

# --- 3. 指令逻辑 ---

async def set_group_to_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    chat = update.effective_chat
    
    # 这里非常重要：必须确认同步拿到 SHA 后才进行后续操作
    if sync_from_github() is None:
        await update.message.reply_text("⚠️ 无法连接数据库，请稍后再试")
        return

    existing = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    if not existing:
        DATA_CACHE['members'].append({"chat_id": chat.id, "remark": chat.title, "g_name": "未分类"})
        if save_to_github():
            await update.message.reply_text(f"📥 **入库成功**\n群组: {chat.title}\nID: `{chat.id}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 数据保存失败，请检查 Logs")
    else:
        await update.message.reply_text("ℹ️ 群组已存在。")

async def set_group_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("💡 格式: `/set_kind 分组名`")
        return
        
    new_kind = context.args[0]
    chat = update.effective_chat
    
    if sync_from_github() is None: return

    member = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    if member:
        member['g_name'] = new_kind
        if new_kind not in DATA_CACHE['groups']:
            DATA_CACHE['groups'].append(new_kind)
        
        if save_to_github():
            await update.message.reply_text(f"✅ 分组更新: 【{new_kind}】")
    else:
        await update.message.reply_text("❌ 请先执行 /set_group")

# --- 4. 菜单逻辑 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: return ConversationHandler.END
    
    sync_from_github() # 进菜单自动刷新一次
    
    kb = [
        [InlineKeyboardButton("📁 分组管理 (刷新)", callback_data='sync_now')],
        [InlineKeyboardButton("🚀 执行群发", callback_data='start_bc')]
    ]
    text = f"🤖 **群发系统控制台**\n当前数据库共有群组: `{len(DATA_CACHE['members'])}` 个"
    
    reply_markup = InlineKeyboardMarkup(kb)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

# --- 群发逻辑 ---
async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🚀 {g}", callback_data=f"do_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("🎯 选择目标分组：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_target'] = update.callback_query.data.replace("do_", "")
    await update.callback_query.edit_message_text(f"已选：**{context.user_data['bc_target']}**\n请输入内容：", parse_mode="Markdown")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get('bc_target')
    # 再次确保群发前数据是最新的
    sync_from_github()
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target]
    
    msg = await update.message.reply_text(f"📣 推送中...")
    count = 0
    for cid in ids:
        try:
            await context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            count += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"⚠️ ID {cid} 失败: {e}")

    await msg.edit_text(f"✅ 发送结束: {count}/{len(ids)}")
    return BCAST_MSG

# --- 5. 入口 ---
app = Flask('')
@app.route('/')
def home(): return "Sunday Bot Active"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    sync_from_github()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    app_tg.add_handler(CommandHandler("set_group", set_group_to_db))
    app_tg.add_handler(CommandHandler("set_kind", set_group_kind))

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(start, pattern='^to_start$|^sync_now$'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
            ],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^do_'), CallbackQueryHandler(start, pattern='^to_start$')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do), CallbackQueryHandler(start, pattern='^to_start$')],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    
    app_tg.add_handler(conv)
    app_tg.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
